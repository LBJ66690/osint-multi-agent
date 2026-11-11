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


def _provenance_fields(profile, excerpt_chars):
    """仅携带保守的来源线索，元数据共用正文配额，不挤空正文。"""
    if not profile:
        return {}
    fields={}
    group=profile.get('source_group')
    if isinstance(group,str) and re.fullmatch(r'[A-Za-z0-9_:-]{1,32}',group):
        fields['source_group']=group
    origin_type=profile.get('origin_type')
    if origin_type in {'secondary','original_claim','unknown'}:
        fields['origin_type']=origin_type
    status=profile.get('independence_status')
    if status in {'shared_origin','unknown'}:
        fields['independence_status']=status
    # 不接受调用方传入 verified/independent 等未经验证的认证标签。
    if not fields:
        return {}
    fields.setdefault('independence_status','unknown')
    limit=min(240,max(0,excerpt_chars//3))
    if len(json.dumps(fields,ensure_ascii=False))>limit:
        return {}
    origin=profile.get('origin_reference')
    if isinstance(origin,str) and origin.strip():
        full=origin.strip()
        for size in range(min(len(full),159),0,-1):
            candidate=full[:size]+('…' if size<len(full) else '')
            trial={**fields,'origin_reference':candidate}
            if len(json.dumps(trial,ensure_ascii=False))<=limit:
                fields=trial
                break
    return fields


def _validated_evidence_ids(registry):
    """检查持久化的 URL→编号关系；损坏时安全停止，避免把旧引用悄悄改绑。"""
    if not isinstance(registry,dict):
        raise ValueError('证据编号登记表无效：应为 URL 到 E 编号的字典，已停止避免引用错配。')
    normalized={};assigned={}
    for url,identifier in registry.items():
        if (not isinstance(url,str) or not url.strip() or not isinstance(identifier,str)
                or re.fullmatch(r'E[1-9]\d*',identifier) is None):
            raise ValueError('证据编号登记表无效：来源与编号格式不正确，已停止避免引用错配。')
        key=canonical_url(url)
        if key in normalized and normalized[key]!=identifier:
            raise ValueError('证据编号登记表冲突：同一来源对应多个编号，已停止避免引用错配。')
        if identifier in assigned and assigned[identifier]!=key:
            raise ValueError('证据编号登记表冲突：不同来源共用编号，已停止避免引用错配。')
        normalized[key]=identifier;assigned[identifier]=key
    return normalized


def evidence_context(evidence, plan, policy, provenance_profiles=None, evidence_ids=None):
    # 默认不登记，保持旧调用输出；传入的字典由调用方写入检查点持久化。
    registered=_validated_evidence_ids(evidence_ids) if evidence_ids is not None else None
    next_id=max((int(value[1:]) for value in registered.values()),default=0)+1 if registered is not None else 1
    profiles={}
    for profile in provenance_profiles or ():
        if isinstance(profile,dict) and isinstance(profile.get('source'),str):
            profiles.setdefault(canonical_url(profile['source']),profile)
    grouped = {}
    for e in evidence:
        if not e.content.strip() or "未检索到" == e.content.strip():
            continue
        key = canonical_url(e.source)
        if key not in grouped:
            if registered is None:
                identifier=f"E{len(grouped)+1}"
            else:
                if key not in registered:
                    registered[key]=f'E{next_id}';next_id+=1
                identifier=registered[key]
            grouped[key] = {"id":identifier, "title": e.title, "source": e.source, "content": e.content, "variants": [e.content],
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
            origin=_provenance_fields(profiles.get(canonical_url(row['source'])),policy.excerpt_chars)
            excerpt_limit=policy.excerpt_chars-(len(json.dumps(origin,ensure_ascii=False)) if origin else 0)
            row.update(origin)
            quota = max(1, excerpt_limit // len(variants))
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
            row["content"] = "\n".join(snippets)[:excerpt_limit]
            if len(json.dumps(selected + [row], ensure_ascii=False)) <= policy.evidence_chars:
                selected.append(row)
    if evidence_ids is not None:
        # 验证与上下文选择成功后才原地更新；异常不会写入半份登记表。
        evidence_ids.clear();evidence_ids.update(registered)
    return json.dumps(selected, ensure_ascii=False), {
        "raw_count": len(evidence), "unique_count": len(grouped), "selected_count": len(selected),
        "context_chars": len(json.dumps(selected, ensure_ascii=False)),
    }
