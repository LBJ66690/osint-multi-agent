"""完整证据留档，给模型的上下文按 URL 合并并按子问题均衡选取。"""
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def canonical_url(url):
    try:
        p = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))
    except ValueError:
        return url


def terms(text):
    latin = re.findall(r"[a-z0-9]{2,}", text.lower())
    han = re.findall(r"[\u4e00-\u9fff]+", text)
    return set(latin + [s[i:i+2] for s in han for i in range(len(s)-1)])


def evidence_context(evidence, plan, policy):
    grouped = {}
    for e in evidence:
        if not e.content.strip() or "未检索到" == e.content.strip():
            continue
        key = canonical_url(e.source)
        if key not in grouped:
            grouped[key] = {"id":f"E{len(grouped)+1}", "title": e.title, "source": e.source, "content": e.content, "variants": [e.content],
                            "subquestions": [e.subquestion]}
        else:
            row = grouped[key]
            if e.subquestion not in row["subquestions"]:
                row["subquestions"].append(e.subquestion)
            if e.content not in row["content"]:
                row["content"] += "\n" + e.content
                row["variants"].append(e.content)
    query_terms = terms(plan.goal + " " + " ".join(q for s in plan.subquestions for q in s.search_queries))
    def score(row):
        return len(terms(row["title"] + " " + row["content"]) & query_terms)
    ranked = sorted(grouped.values(), key=score, reverse=True)
    selected, used = [], set()
    # 轮询各子问题，避免前几个搜索返回值吃光上下文。
    buckets = [[r for r in ranked if s.question in r["subquestions"]] for s in plan.subquestions]
    while len(selected) < policy.evidence_items and any(buckets):
        for bucket in buckets:
            while bucket and canonical_url(bucket[0]["source"]) in used:
                bucket.pop(0)
            if not bucket or len(selected) >= policy.evidence_items:
                continue
            row = dict(bucket.pop(0))
            used.add(canonical_url(row["source"]))
            # 按相关性选正文段落，再恢复原文次序；不改写内容或凭空补充。
            variants = row.pop("variants")
            quota = max(1, policy.excerpt_chars // len(variants))
            snippets = []
            for variant in variants:
                parts = [p.strip() for p in re.split(r"\n+|(?<=[。！？])", variant) if p.strip()]
                order = sorted(range(len(parts)), key=lambda i: len(terms(parts[i]) & query_terms), reverse=True)
                keep, size = [], 0
                for i in order:
                    if size >= quota:
                        break
                    keep.append((i, parts[i][:quota-size])); size += len(keep[-1][1])
                snippets.append("\n".join(p for _, p in sorted(keep)))
            row["content"] = "\n".join(snippets)[:policy.excerpt_chars]
            if len(json.dumps(selected + [row], ensure_ascii=False)) <= policy.evidence_chars:
                selected.append(row)
    return json.dumps(selected, ensure_ascii=False), {
        "raw_count": len(evidence), "unique_count": len(grouped), "selected_count": len(selected),
        "context_chars": len(json.dumps(selected, ensure_ascii=False)),
    }
