"""有界报告生成：流中断后重开整份报告，不拼接不完整内容。"""
import asyncio
import inspect
import uuid
import time

def transient(exc):
    parts, seen, pending = [], set(), [exc]
    while pending:
        error = pending.pop()
        if id(error) in seen: continue
        seen.add(id(error))
        parts.append(type(error).__name__ + " " + str(error))
        pending.extend(x for x in [error.__cause__, error.__context__] if x)
        pending.extend(getattr(error, "exceptions", []))
    text = " ".join(parts).lower()
    return any(t in text for t in ["connection", "timeout", "protocolerror", "chunked", "ratelimit", "429", "502", "503", "504"])

async def generate_report(reporter, prompt, report_type, emit, save_draft, usage_of, attempts=1, timeout=120, backoff=1, total_timeout=150):
    deadline = time.monotonic() + total_timeout
    async def notify(kind, **data):
        await emit({"type": kind, "stage": "reporting", "report_type": report_type, **data})
    for attempt in range(attempts + 1):
        mode = "stream" if attempt < attempts else "nonstream"
        version = uuid.uuid4().hex[:10]
        buf, stream = [], None
        await notify("report_start", version=version, attempt=attempt + 1, mode=mode)
        async def run():
            nonlocal stream
            if mode == "nonstream":
                response = await reporter.run(prompt)
                return response
            stream = reporter.run(prompt, stream=True)
            async for update in stream:
                piece = getattr(update, "text", "") or ""
                if piece:
                    buf.append(piece)
                    await notify("report_delta", delta=piece, version=version)
            return await stream.get_final_response()
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("报告生成已达到共享时间预算")
            response = await asyncio.wait_for(run(), min(timeout, remaining))
            text = getattr(response, "text", "") or "".join(buf)
            if not text.strip(): raise RuntimeError("报告模型返回空正文")
            await emit({"type": "usage", "stage": "reporting", **usage_of(response)})
            return text
        except asyncio.CancelledError:
            if buf: await save_draft("".join(buf))
            raise
        except Exception as exc:
            if buf: await save_draft("".join(buf))
            can_retry = transient(exc) and attempt < attempts and deadline-time.monotonic() > backoff*(attempt+1)+2
            await notify("report_interrupted", version=version, attempt=attempt + 1, retrying=can_retry,
                         partial_chars=sum(map(len, buf)), message="云端响应中断，已保存不完整草稿" if buf else "云端请求失败")
            if not can_retry: raise
            await notify("report_retry", attempt=attempt + 2, message="重新生成整份报告" if attempt + 1 < attempts else "切换为普通响应，规避流式连接中断")
            await asyncio.sleep(backoff * (attempt + 1))
        finally:
            close = getattr(stream, "aclose", None)
            if close:
                try:
                    result = close()
                    if inspect.isawaitable(result): await asyncio.wait_for(result, 2)
                except Exception: pass
