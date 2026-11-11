"""可解释的引用和证据缺口检查；规则提示风险，不保证事实正确。"""
import json
import re

from evidence_context import canonical_url, terms
from schemas import VerificationIssue, VerificationReport


UNCERTAIN = re.compile(r'无法确认|无法确定|尚未确认|尚无|不确定|存疑|可能|推测|尚有|不同说法|据称|待核实|未能确认|不能确认|不能确定|不足以确认')
LINK = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)')


def _row(item):
    return item.model_dump() if hasattr(item, 'model_dump') else item


def _issue(claim, explanation, kind='semantic_support', verdict='存疑', ids=(), severity='中'):
    return VerificationIssue(claim=claim[:250], verdict=verdict, explanation=explanation,
                             severity=severity, check_type=kind, evidence_ids=list(ids))


def _rows(context):
    return json.loads(context) if isinstance(context, str) else [_row(e) for e in context]


def _meaningful(text):
    stop = {'the', 'and', 'for', 'that', 'this', 'from', 'with', 'are', 'was', 'has', 'have',
            '一个', '相关', '资料', '来源', '信息', '显示', '报告', '表明', '认为', '发布', '项目'}
    return {t for t in terms(text) if t not in stop and not t.isdigit()}


def _attribute_subject(prefix):
    prefix = re.split(r'[。；;!?！？\n，,：:]', prefix)[-1]
    prefix = re.sub(r'(?:19|20)\d{2}年(?:\d{1,2}月)?(?:\d{1,2}日)?', '', prefix)
    prefix = re.sub(r'^(?:据|根据)', '', prefix.strip())
    return re.sub(r'\s', '', prefix).rstrip('的于在')


def _attribute_values(matches):
    values = set()
    for match in matches:
        parts = match.groups()
        value = float(parts[0])
        if len(parts) > 1 and parts[1] in {'亿元', '万元'}:
            value *= 10000 if parts[1] == '亿元' else 1
        values.add(round(value, 6))
    return values


def support_risks(claim, rows):
    """只识别明显无关片段和同属性的显式值冲突；其余仍需现有核查官判断。"""
    if not claim.strip() or not rows:
        return []
    text = '\n'.join(str(r.get('title', '')) + ' ' + str(r.get('content', '')) for r in rows)
    content_text = '\n'.join(str(r.get('content', '')) for r in rows)
    ids = [r['id'] for r in rows if r.get('id')]
    risks = []
    ct, et = _meaningful(claim), _meaningful(text)
    if len(claim) >= 14 and len(ct) >= 5 and len(text) >= 30 and not ct & et:
        risks.append(_issue(claim, '引用存在，但提供的片段没有共同的关键主题词，未见支持该陈述的内容；需人工或语义核查，不能据此判定事实真伪。', ids=ids))
    # 不比较句子里任意数字，避免把不同年份、人数与金额等混为一谈。
    patterns = [r'(?:成立于|成立时间[为是：:]?)\s*(20\d{2}|19\d{2})年',
                r'(?:总投资|投资总额|投资额)[为是约达：:\s]*(\d+(?:\.\d+)?)\s*(亿元|万元)',
                r'(?:营收|营业收入)[为是约达：:\s]*(\d+(?:\.\d+)?)\s*(亿元|万元)',
                r'(?:员工人数|员工数量)[为是约达：:\s]*(\d+)\s*人']
    for pattern in patterns:
        cm, em = list(re.finditer(pattern, claim)), list(re.finditer(pattern, content_text))
        claimed, actual = _attribute_values(cm), _attribute_values(em)
        # 缺少共同主体或存在不同显式年份，不做简单数值冲突裁决。
        if not claimed or not actual or claimed & actual or not ct & et:
            continue
        # 仅当属性之前明确指向相同主体时比较；“该公司”等指代无法可靠消解。
        subjects = {_attribute_subject(claim[:m.start()]) for m in cm}
        other_subjects = {_attribute_subject(content_text[:m.start()]) for m in em}
        subjects.discard(''); other_subjects.discard('')
        if not subjects & other_subjects:
            continue
        cy, ey = set(re.findall(r'(?:19|20)\d{2}年', claim)), set(re.findall(r'(?:19|20)\d{2}年', text))
        if '成立' not in pattern and cy and ey and cy.isdisjoint(ey):
            continue
        risks.append(_issue(claim, '陈述与所引片段在同一显式属性上给出不同数值；可能涉及主体、年份、单位或统计口径差异，现有片段尚不能支持确定结论。', ids=ids))
    return risks


def structural_check(stage, value, evidence=(), plan=None):
    """保留旧检查入口，增加引用关联和反证风险；无法确认的结果可以正常交付。"""
    rows = [_row(e) for e in evidence]
    issues, targets = [], []
    if stage == 'planning':
        if not value.goal.strip() or not value.subquestions or not value.report_types:
            issues.append(_issue('计划不完整', '计划缺少目标、子问题或报告类型', 'structure'))
        if any(not s.question.strip() or not s.search_queries for s in value.subquestions):
            issues.append(_issue('子问题不完整', '子问题缺少问题或可用查询', 'structure'))
    if stage == 'collecting':
        covered = {e.get('subquestion') for e in rows if e.get('content', '').strip() and e.get('source', '').strip()}
        targets = [s.question for s in plan.subquestions if s.question not in covered]
        if targets:
            issues.append(_issue('资料缺口', '以下子问题缺少资料：' + '；'.join(targets), 'structure'))
    if stage == 'analyzing' and not value.viewpoints:
        issues.append(_issue('未提炼观点', '未提炼任何观点；需要说明证据缺口', 'structure'))
    if stage == 'reasoning':
        by_url = {}
        for r in rows:
            by_url.setdefault(canonical_url(r.get('source', '')), []).append(r)
        # 原始证据没有上下文编号；只有显式包含id时才验证该映射。
        ids = {r['id']: r for r in rows if r.get('id')}
        if not value.findings:
            issues.append(_issue('缺少发现', '尚未形成任何核心发现', 'structure'))
        for f in value.findings:
            uncertain = bool(UNCERTAIN.search(f.claim))
            if (not f.evidence or not f.sources) and not uncertain:
                issues.append(_issue(f.claim, '发现缺少依据或来源；资料不足时应明确说明无法确认', 'reference', '无出处'))
            if any(canonical_url(u) not in by_url for u in f.sources):
                issues.append(_issue(f.claim, '发现引用了证据清单外的来源', 'reference', '无出处', severity='高'))
            linked = [r for u in f.sources for r in by_url.get(canonical_url(u), [])]
            issues.extend(support_risks(f.claim, linked))
            if ids and any(i not in ids for i in f.support_ids + f.counter_ids):
                issues.append(_issue(f.claim, '支持或反证编号不在本次上下文中', 'reference', '无出处'))
            if f.counter_evidence or f.counter_ids:
                if not UNCERTAIN.search(f.claim) and not f.uncertainty_reason:
                    issues.append(_issue(f.claim, '存在反证或不同声音，却未解释其影响；应保留不确定性，或说明为什么反证不适用', 'uncertainty', '推测当事实', f.counter_ids))
    return VerificationReport(verdict='fail' if issues else 'pass',
        summary='；'.join(i.explanation for i in issues[:5]) if issues else '程序检查通过（结构、覆盖或引用范围）；尚未独立核实来源真实性和事实正确性。',
        issues=issues[:5], redo_targets=targets)


def report_reference_check(body, context, analysis=None):
    rows = _rows(context)
    by_url = {}
    for row in rows:
        by_url.setdefault(canonical_url(row['source']), []).append(row)
    ids = {row['id']: canonical_url(row['source']) for row in rows if row.get('id')}
    issues = []
    for match in LINK.finditer(body):
        label, url = match.groups()
        key = canonical_url(url)
        if key not in by_url or (re.fullmatch(r'E\d+', label) and ids.get(label) != key):
            issues.append(_issue(match.group(), '引用编号或URL与本次提供的证据不匹配', 'reference', '无出处', severity='高'))
            continue
        # 同段落内的陈述与所引片段对照，规则只提示最明显的支持缺口。
        start = body.rfind('\n', 0, match.start()) + 1
        claim = LINK.sub('', body[start:match.start()]).strip(' -*#：:')
        issues.extend(support_risks(claim, by_url[key]))
    # 已结构化的关键发现应在报告中保留引用及其不确定状态。
    if analysis is not None:
        findings = analysis.findings if hasattr(analysis, 'findings') else analysis.get('findings', [])
        for f in findings:
            f = _row(f)
            claim = f.get('claim', '').strip()
            explicit_ids = f.get('support_ids', []) + f.get('counter_ids', [])
            if any(i not in ids for i in explicit_ids):
                issues.append(_issue(claim, '关键发现关联的证据编号不在本次提供的上下文中', 'reference', '无出处', severity='高'))
            source_urls = {canonical_url(u) for u in f.get('sources', [])}
            mismatched = [i for i in f.get('support_ids', []) if i in ids and ids[i] not in source_urls]
            if mismatched:
                issues.append(_issue(claim, '支持证据编号与该结论声明的来源URL不匹配；不能用另一来源的编号替代实际依据',
                                     'reference', '无出处', mismatched, severity='高'))
            if set(f.get('support_ids', [])) & set(f.get('counter_ids', [])):
                issues.append(_issue(claim, '同一证据编号同时被标为支持和反证，需要说明其中不同陈述及其限制', 'uncertainty'))
            if len(claim) < 12 or claim not in body:
                continue
            line = next((line for line in body.splitlines() if claim in line), '')
            if not UNCERTAIN.search(claim) and not LINK.search(line) and f.get('sources'):
                issues.append(_issue(claim, '报告复述了关键结论，但所在段落没有具体证据引用；需补充或明确无法确认', 'reference', '无出处'))
            if f.get('status') == 'uncertain' or (f.get('status') == 'unconfirmed' and f.get('uncertainty_reason')) or f.get('counter_evidence') or f.get('counter_ids'):
                if not UNCERTAIN.search(line) and not any(w in line for w in ['反证', '不同声音', '口径', '局限']):
                    issues.append(_issue(claim, '该发现尚未确认或存在反证，报告却未保留证据缺口或不确定性', 'uncertainty', '推测当事实'))
            if f.get('status') == 'inference' and not UNCERTAIN.search(line) and not any(w in line for w in ['推断', '判断']):
                issues.append(_issue(claim, '该发现属于模型推断，报告需要保留推断标记和依据', 'uncertainty', '推测当事实'))
    return issues[:5]


def finding_details(analysis, context, checkpoint):
    rows = _rows(context)
    ids = {r['id']: r for r in rows if r.get('id')}
    urls = {canonical_url(r['source']): r['id'] for r in rows if r.get('id')}
    conflicted = {canonical_url(u) for c in checkpoint.get('conflicts', []) for values in c.get('values', {}).values() for u in values}
    results = []
    for f in analysis.findings:
        row = f.model_dump()
        links = {canonical_url(u) for u in f.sources}
        # 保留模型给出的错误编号供后续核查。不能过滤后悄悄回退成看似正常的URL关联。
        explicit = list(f.support_ids)
        counter = list(f.counter_ids)
        invalid_support = [i for i in explicit if i not in ids]
        invalid_counter = [i for i in counter if i not in ids]
        mismatched = [i for i in explicit if i in ids and canonical_url(ids[i]['source']) not in links]
        mapping_problems = []
        if invalid_support or invalid_counter:
            mapping_problems.append('支持或反证编号不在本次上下文中：' + '、'.join(invalid_support + invalid_counter))
        if mismatched:
            mapping_problems.append('支持证据编号与声明来源URL不匹配：' + '、'.join(mismatched))
        conflict = bool(links & conflicted)
        uncertain = conflict or bool(mapping_problems) or bool(f.counter_evidence or counter) or f.status != 'supported'
        reason = f.uncertainty_reason or ('涉及相互不同的公开资料，尚未确认主体、时间或统计口径' if conflict else
                   '存在反证或不同声音，需要说明其影响' if f.counter_evidence or counter else
                   '仅建立引用关联，尚未独立核实事实；来源匹配不等于语义支持')
        if mapping_problems:
            reason = '；'.join(mapping_problems + [reason])
        row.update(support_ids=explicit or [i for u, i in urls.items() if u in links], counter_ids=counter,
                   linked_ids=[i for u, i in urls.items() if u in links],
                   link_status='invalid_mapping' if mapping_problems else 'explicit_mapping' if explicit else 'reference_only',
                   confidence='low' if conflict else 'unknown', confidence_basis='未计算事实正确率',
                   uncertain=uncertain, uncertainty_reason=reason,
                   status='uncertain' if conflict or mapping_problems else f.status)
        results.append(row)
    return results


def verification_appendix(report_type, check):
    lines = ['\n\n---\n\n', f'## 附：事实核查（{report_type}）\n\n',
             f'**本次材料核查结论**：{check.summary}\n\n',
             '核查范围：程序检查结构与引用匹配，核查官对照本次提供的片段检查语义支持。'
             '未进行额外的外部独立事实调查，也未保证来源真实、完整或彼此独立；通过不代表事实正确率。\n\n']
    if not check.issues:
        lines.append('本次材料对照未报告待修订问题；仍应结合原文、时效与来源条件判断。\n')
    else:
        lines.append('### 尚未解决的问题\n\n')
        lines.append('| 被核查陈述 | 风险类型 | 依据与缺口 | 严重度 |\n|---|---|---|---|\n')
        for issue in sorted(check.issues, key=lambda i: {'高': 0, '中': 1, '低': 2}.get(i.severity, 3)):
            fields = [issue.claim, issue.verdict, issue.explanation, issue.severity]
            lines.append('| ' + ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ') for v in fields) + ' |\n')
    return ''.join(lines)
