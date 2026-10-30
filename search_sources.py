"""通用网页搜索 MCP，仅接入通过真实 tools/call 测试的免密钥服务。"""
from __future__ import annotations
import asyncio
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

SOURCES = [
    {"id": "parallel", "name": "Parallel 网页搜索 MCP", "free": True,
     "description": "已实测中文网页搜索，返回来源链接与相关正文摘录。免 Key，有匿名限流。"},
    {"id": "you", "name": "You.com 网页搜索 MCP", "free": True,
     "description": "已实测中文网页搜索，返回网页、摘要与片段。官方免费档每日 100 次，免 Key。"},
    {"id": "bailian", "name": "百炼 WebSearch MCP", "free": False,
     "description": "原有通用联网搜索，需要百炼 MCP 权限，额度用尽可能计费；只在勾选后启用。"},
]
FREE_IDS = {"parallel", "you"}
ENDPOINTS = {"parallel": "https://mcp.parallel.ai/mcp", "you": "https://api.you.com/mcp?profile=free"}
PROBE_QUERIES = {"parallel": "贵阳 花溪 旅游 景点", "you": "贵阳 花溪 旅游 景点"}

class SourceError(RuntimeError):
    pass

def validate_sources(ids: list[str]) -> list[str]:
    allowed = {s["id"] for s in SOURCES}
    if not isinstance(ids, list) or not ids:
        raise ValueError("请至少选择一个搜索 MCP")
    if any(not isinstance(s, str) or s not in allowed for s in ids):
        raise ValueError("包含已撤下或未知搜索渠道，请重新选择")
    return list(dict.fromkeys(ids))

def source_options() -> list[dict]:
    return [dict(s) for s in SOURCES]

def plain(value) -> str:
    return html.unescape(re.sub(r"<[^>]*>", "", str(value or ""))).strip()

def _http_url(value) -> str:
    try:
        parsed = urlsplit(str(value or ""))
        return str(value) if parsed.scheme in {"https", "http"} and parsed.netloc else ""
    except ValueError:
        return ""

def parse_results(provider: str, data: dict, limit: int = 5) -> list[dict]:
    if provider not in FREE_IDS or not isinstance(data, dict):
        raise SourceError("无法识别搜索 MCP 返回内容")
    rows = data.get("results")
    if provider == "you" and isinstance(rows, dict): rows = rows.get("web")
    if not isinstance(rows, list): raise SourceError("搜索 MCP 未返回网页结果列表")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): continue
        url, title = _http_url(row.get("url")), plain(row.get("title"))
        fragments = row.get("excerpts") or row.get("snippets") or row.get("highlights") or []
        if isinstance(fragments, str): fragments = [fragments]
        body = plain("\n".join([str(row.get("description") or ""), *[str(s) for s in fragments]]))
        if url and title and body and url not in seen:
            seen.add(url)
            date = row.get("publish_date") or row.get("page_age") or "未提供"
            result.append({"title": title, "source": url, "content": f"网页检索片段（非独立核验）；发布时间：{date}\n{body[:5000]}",
                           "provider": provider, "retrieved_at": datetime.now(timezone.utc).isoformat()})
        if len(result) >= limit: break
    return result

def response_data(response) -> dict:
    if response.isError:
        raise SourceError("搜索 MCP 工具执行失败：" + " ".join(getattr(c, "text", "") for c in response.content)[:240])
    structured = getattr(response, "structuredContent", None)
    if isinstance(structured, dict) and "results" in structured: return structured
    for block in response.content:
        try:
            data = json.loads(getattr(block, "text", ""))
            if isinstance(data, dict) and "results" in data: return data
        except (ValueError, TypeError): pass
    raise SourceError("MCP 返回不是可解析的网页搜索结果")

class MCPWebSearchClient:
    """每渠道独立限额、缓存，单次包含握手最多 45 秒。"""
    def __init__(self, max_calls: int = 12):
        self.max_calls, self.counts, self.cache = max_calls, {}, {}
        self.locks = {p: asyncio.Lock() for p in FREE_IDS}
        self.queues, self.workers = {}, {}

    async def _serve(self, provider):
        """会话在同一 worker 中进入和退出，避免 MCP/AnyIO 跨任务关闭错误。"""
        queue = self.queues[provider]
        pending = None
        try:
            while True:
                pending = await queue.get()
                try:
                    async with streamablehttp_client(ENDPOINTS[provider], timeout=15, sse_read_timeout=25) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await asyncio.wait_for(session.initialize(), 15)
                            listing = await asyncio.wait_for(session.list_tools(), 15)
                            name = "web_search" if provider == "parallel" else "you-search"
                            if not any(t.name == name for t in listing.tools):
                                raise SourceError("MCP 缺少已验证的搜索工具")
                            while True:
                                query, limit, future = pending
                                if not future.cancelled():
                                    args = ({"objective":query,"search_queries":[query]} if provider == "parallel"
                                            else {"query":query,"count":min(limit,5),"extraction":"highlights"})
                                    response = await asyncio.wait_for(session.call_tool(name,args),30)
                                    rows = parse_results(provider,response_data(response),limit)
                                    if not future.done():
                                        future.set_result(rows)
                                pending = None
                                pending = await queue.get()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if pending and not pending[2].done():
                        pending[2].set_exception(SourceError(f"{provider} MCP 暂不可用：{exc}"))
                    pending = None  # 下一次查询重建会话；已成功查询保持缓存。
        finally:
            if pending and not pending[2].done():
                pending[2].cancel()
            while not queue.empty():
                _, _, future = queue.get_nowait()
                if not future.done():
                    future.cancel()

    async def aclose(self):
        for worker in self.workers.values():
            worker.cancel()
        if self.workers:
            await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.workers.clear()
        self.queues.clear()

    async def search(self, provider: str, query: str, limit: int = 5) -> list[dict]:
        if provider not in FREE_IDS: raise ValueError("未知搜索 MCP")
        query = query.strip()[:350]
        if not query: return []
        key = (provider, query, limit)
        async with self.locks[provider]:
            if key in self.cache: return self.cache[key]
            if self.counts.get(provider, 0) >= self.max_calls:
                raise SourceError(f"该 MCP 已达本任务 {self.max_calls} 次请求预算")
            self.counts[provider] = self.counts.get(provider, 0) + 1
            if provider not in self.workers:
                self.queues[provider] = asyncio.Queue()
                self.workers[provider] = asyncio.create_task(self._serve(provider))
            future = asyncio.get_running_loop().create_future()
            self.queues[provider].put_nowait((query,limit,future))
            try:
                rows = await asyncio.wait_for(future,45)
                self.cache[key] = rows
                return rows
            except asyncio.CancelledError: raise
            except Exception as exc:
                detail = str(exc)
                if getattr(exc, "exceptions", None): detail += " " + repr(exc.exceptions)
                raise SourceError(f"{provider} MCP 暂不可用：{detail[:260]}") from exc
