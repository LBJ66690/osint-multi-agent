"""轻量、可解释的证据指标。来源数量不是事实正确率。"""
import re
from urllib.parse import urlsplit
from difflib import SequenceMatcher
from evidence_context import canonical_url, terms
from specialized_sources import source_quality

def record(e):return e.model_dump() if hasattr(e,'model_dump') else e
def domain(url):
    host=(urlsplit(url).hostname or '').lower()
    parts=host.split('.')
    return '.'.join(parts[-3:] if len(parts)>2 and '.'.join(parts[-2:]) in {'gov.cn','edu.cn','ac.cn','com.cn','co.uk'} else parts[-2:])
def valid(e):
    try:return urlsplit(e.get('source','')).scheme in {'http','https'} and bool(domain(e['source'])) and len(e.get('content','').strip())>=20
    except ValueError:return False
def similar(a,b):
    if canonical_url(a['source'])==canonical_url(b['source']):return True
    x,y=a.get('content','')[:1800],b.get('content','')[:1800]
    if x==y:return True
    if re.findall(r'\d+(?:\.\d+)?',x)!=re.findall(r'\d+(?:\.\d+)?',y):return False
    if min(len(x),len(y))<40:return False
    tx,ty=terms(x),terms(y)
    if any(x.count(w)!=y.count(w) for w in ['不','未','没有','否认']):return False
    return (bool(tx|ty) and len(tx&ty)/len(tx|ty)>=.90 or
            max(len(x),len(y))<=600 and SequenceMatcher(None,x,y,autojunk=False).ratio()>=.92)
def deduplicate(rows):
    unique=[]
    for e in map(record,rows):
        if valid(e) and not any(similar(e,u) for u in unique):unique.append(e)
    return unique
def assess(rows,questions):
    raw=list(map(record,rows)); usable=[e for e in raw if valid(e)]; unique=deduplicate(usable)
    coverage={}
    for q in questions:
        keywords=terms(q)
        subset=deduplicate([e for e in usable if e.get('subquestion')==q and
                            (not keywords or keywords & terms(e.get('title','')+' '+e['content']))])
        urls={canonical_url(e['source']) for e in subset}; domains={domain(e['source']) for e in subset}
        coverage[q]={'status':'sufficient' if len(urls)>=2 and len(domains)>=2 else 'weak' if urls else 'insufficient',
                     'valid_count':len(subset),'unique_urls':len(urls),'unique_domains':len(domains)}
    return {'raw_count':len(raw),'unique_url_count':len({canonical_url(e['source']) for e in usable}),
            'unique_domain_count':len({domain(e['source']) for e in usable}), 'nonduplicate_count':len(unique),
            'duplicate_ratio':round(1-len(unique)/len(usable),3) if usable else 0,'coverage':coverage}
def information_gain(before,after):
    a,b=list(map(record,before)),list(map(record,after)); a=[x for x in a if valid(x)]; b=[x for x in b if valid(x)]
    urls={canonical_url(e['source']) for e in a}; domains={domain(e['source']) for e in a}; words=set().union(*(terms(e.get('title','')) for e in a))
    novel=[e for e in deduplicate(b) if not any(similar(e,u) for u in a)]
    nu=len({canonical_url(e['source']) for e in b}-urls); nd=len({domain(e['source']) for e in b}-domains)
    nw=len(set().union(*(terms(e.get('title','')) for e in b))-words)
    level='high' if novel and nd else 'medium' if novel and (nu or nw) else 'low'
    return {'level':level,'new_urls':nu,'new_domains':nd,'new_title_terms':nw,'new_contents':len(novel)}
def conflicts(rows):
    # 仅提示同一子问题、同一显式年份的投资金额差异，不自动裁决真假。
    groups={}
    for e in map(record,rows):
        for sentence in re.split('[。；\n]',e.get('content','')):
            years=tuple(re.findall(r'20\d{2}年',sentence))
            for amount,unit in re.findall(r'(?:投资总额|总投资|投资额)[为约达：:\s]*(\d+(?:\.\d+)?)\s*(亿|万)元?',sentence):
                key=(e.get('subquestion',''),years)
                groups.setdefault(key,{}).setdefault(str(round(float(amount)*(10000 if unit=='亿' else 1),4)),[]).append(e['source'])
    return [{'subquestion':q,'topic':'投资总额','period':list(years),'values':values,'status':'uncertain',
             'reason':'同一子问题的投资金额存在不同说法；可能涉及不同对象或口径，需查证'}
            for (q,years),values in groups.items() if len(values)>1][:3]
def rank(rows):
    # 不丢弃原始资料；只调整传给上下文选择器的先后顺序。
    return sorted(rows,key=lambda e:source_quality(record(e)['source'])['weight'],reverse=True)

def curate(rows):
    result=[]
    for e in rank(rows):
        r=record(e)
        if valid(r) and not any(record(x).get('subquestion')==r.get('subquestion') and similar(r,record(x)) for x in result):
            result.append(e)
    return result
