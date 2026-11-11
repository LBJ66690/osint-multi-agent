"""轻量、可解释的证据指标。来源数量不是事实正确率。"""
import re
from decimal import Decimal
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
def _body(e):
    # 清理来源标签和排版差异只用于比较，不改写原始存档。
    text=e.get('content','')
    text=re.sub(r'(?m)^\s*(?:来源|转载自|转自|责任编辑|编辑|作者)\s*[:：][^\n。]+(?:\n|$)','',text)
    return re.sub(r'\s+','',text).strip()

def similar(a,b):
    # 同一 URL 的不同片段也可能是补充信息或反证，不能仅凭 URL 丢弃。
    x,y=_body(a),_body(b)
    if not x or not y:return False
    if x==y:return True
    if re.findall(r'\d+(?:\.\d+)?',x)!=re.findall(r'\d+(?:\.\d+)?',y):return False
    if min(len(x),len(y))<40:return False
    if any(x.count(w)!=y.count(w) for w in ['不','未','没有','否认']):return False
    if any(len(re.findall(r'\b'+w+r'\b',a.get('content',''),re.I))!=
           len(re.findall(r'\b'+w+r'\b',b.get('content',''),re.I))
           for w in ['not','never','no','deny','reject','support','oppose']):return False
    if any(x.count(w)!=y.count(w) for w in ['支持','反对','成功','失败','安全','危险','合法','违法','有效','无效']):return False
    short,long=sorted((x,y),key=len)
    if len(short)>=80 and short in long and len(short)/len(long)>=.70:return True
    tx,ty=terms(x),terms(y)
    return (bool(tx|ty) and len(tx&ty)/len(tx|ty)>=.90 or
            max(len(x),len(y))<=1800 and SequenceMatcher(None,x,y,autojunk=False).ratio()>=.92)
def deduplicate(rows):
    unique=[]
    for e in map(record,rows):
        if valid(e) and not any(similar(e,u) for u in unique):unique.append(e)
    return unique
def _origin(e):
    """页面自述的出处，不把自述或域名当作已认证的原始来源。"""
    for key in ('original_source','origin_url','original_url'):
        value=e.get(key)
        if isinstance(value,str) and value.strip():
            value=value.strip()
            return 'secondary',canonical_url(value) if value.startswith(('http://','https://')) else value
    text=e.get('content','')[:4000]
    match=re.search(r'(?:转载自|转自|来源\s*[:：])\s*(https?://[^\s，。；<>]+|[^\n，。；:：]{2,40})',text)
    if not match:match=re.search(r'据\s*([^\n，。；:：]{2,25}?)\s*(?:报道|消息|发布的|公布的)',text)
    if match:
        origin=match[1].strip().rstrip('。')
        aliases={'新华社':'新华社','新华网':'新华社','新华社客户端':'新华社'}
        return 'secondary',aliases.get(origin,canonical_url(origin) if origin.startswith(('http://','https://')) else origin)
    if re.search(r'本(?:研究|机构|公司|团队)(?:通过|独立|实测|调查|发布|开展)|我们(?:独立|实测|调查)|独立(?:实测|采访|调查)',text):
        return 'original_claim',None
    return 'unknown',None

def provenance(rows):
    """记录可见转载关系；不能判断的独立性保留未知。"""
    usable=[e for e in map(record,rows) if valid(e)]
    parent=list(range(len(usable)));profiles=[_origin(e) for e in usable]
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,e in enumerate(usable):
        for j,other in enumerate(usable[:i]):
            same=bool(profiles[i][1]) and profiles[i][1]==profiles[j][1]
            linked=profiles[i][1]==canonical_url(other['source']) or profiles[j][1]==canonical_url(e['source'])
            if same or linked or canonical_url(e['source'])==canonical_url(other['source']) or similar(e,other):
                parent[root(i)]=root(j)
    groups={}
    for i in range(len(usable)):groups.setdefault(root(i),[]).append(i)
    labels={key:'G'+str(n+1) for n,key in enumerate(groups)}
    return [{'source':e['source'],'origin_type':profiles[i][0],'origin_reference':profiles[i][1],
             'source_group':labels[root(i)],
             'independence_status':'shared_origin' if len(groups[root(i)])>1 else 'unknown',
             'reason':'正文重复、同一页面或页面自述同一出处，不能作为多份独立佐证' if len(groups[root(i)])>1 else
                      '现有片段不足以确认来源独立性；域名及页面自述不构成独立事实核验'}
            for i,e in enumerate(usable)]

def assess(rows,questions):
    raw=list(map(record,rows)); usable=[e for e in raw if valid(e)]; unique=deduplicate(usable)
    coverage={}
    for q in questions:
        keywords=terms(q)
        subset=deduplicate([e for e in usable if e.get('subquestion')==q and
                            (not keywords or keywords & terms(e.get('title','')+' '+e['content']))])
        urls={canonical_url(e['source']) for e in subset}; domains={domain(e['source']) for e in subset}
        groups={r['source_group'] for r in provenance(subset)}
        coverage[q]={'status':'sufficient' if len(urls)>=2 and len(domains)>=2 and len(groups)>=2 else 'weak' if urls else 'insufficient',
                     'valid_count':len(subset),'unique_urls':len(urls),'unique_domains':len(domains),
                     'source_group_count':len(groups),'independent_source_count':None,'independence_status':'unknown',
                     'meaning':'检索内容覆盖与多样性，不代表事实已核实或来源已确认独立'}
    origins=provenance(usable)
    return {'raw_count':len(raw),'unique_url_count':len({canonical_url(e['source']) for e in usable}),
            'unique_domain_count':len({domain(e['source']) for e in usable}), 'nonduplicate_count':len(unique),
            'duplicate_ratio':round(1-len(unique)/len(usable),3) if usable else 0,'coverage':coverage,
            'provenance':origins,'source_group_count':len({r['source_group'] for r in origins}),
            'independent_source_count':None,'independence_status':'unknown','verification_status':'not_independently_verified'}
def information_gain(before,after):
    a,b=list(map(record,before)),list(map(record,after)); a=[x for x in a if valid(x)]; b=[x for x in b if valid(x)]
    urls={canonical_url(e['source']) for e in a}; domains={domain(e['source']) for e in a}; words=set().union(*(terms(e.get('title','')) for e in a))
    novel=[e for e in deduplicate(b) if not any(similar(e,u) for u in a)]
    nu=len({canonical_url(e['source']) for e in b}-urls); nd=len({domain(e['source']) for e in b}-domains)
    nw=len(set().union(*(terms(e.get('title','')) for e in b))-words)
    known_origins={origin for _,origin in map(_origin,a) if origin}
    new_groups=[e for e in novel if not _origin(e)[1] or _origin(e)[1] not in known_origins]
    level='high' if new_groups and nd else 'medium' if novel and (nu or nw) else 'low'
    return {'level':level,'new_urls':nu,'new_domains':nd,'new_title_terms':nw,'new_contents':len(novel),
            'independence_status':'unknown'}
def _subject(prefix):
    prefix=re.sub(r'20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?','',prefix)
    prefix=re.sub(r'^(?:据[^，]{2,25}(?:报道|消息)[，,]?|截至|统计显示[，,]?|公告显示[，,]?)','',prefix)
    prefix=re.sub(r'(?:于|在|的|约|实际|计划|预计|累计|新增|全年|合并口径|母公司口径|一季度|二季度|三季度|四季度|前三季度|上半年|下半年)+$','',prefix)
    return prefix.strip(' ，,:：')[-50:]

def _scope(sentence):
    explicit=re.findall(r'(?:口径|统计范围)\s*(?:为|是|[:：])\s*([^，。；]+)',sentence)
    labels=re.findall(r'合并口径|母公司口径|一期口径|二期口径|三期口径|[（(][一二三]期[）)]|国内|全球|全年|一季度|二季度|三季度|四季度|前三季度|上半年|下半年|计划|预计|实际|累计|新增',sentence)
    return tuple(sorted(set(explicit+labels)))

def _period(sentence):
    # 保留可见年月日精度，避免把不同月/不同观察日当成同一期。
    dates=re.findall(r'20\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?|20\d{2}-\d{1,2}(?:-\d{1,2})?',sentence)
    return tuple(sorted({'-'.join(str(int(n)) for n in re.findall(r'\d+',d)) for d in dates}))

def conflicts(rows):
    """提取同主体、同口径的显式差异，输出待核验线索，不自动裁决真假。"""
    groups={}
    def add(e,kind,subject,topic,period,scope,value):
        if not subject:return
        key=(e.get('subquestion',''),kind,subject,topic,period,scope)
        urls=groups.setdefault(key,{}).setdefault(value,[])
        if e['source'] not in urls:urls.append(e['source'])
    metric=r'投资总额|总投资|投资额|营业收入|营收|净利润|员工人数|员工数|注册用户数|用户数|装机容量'
    date=r'20\d{2}年\d{1,2}月(?:\d{1,2}日)?|20\d{2}-\d{1,2}-\d{1,2}'
    event=r'开工|竣工|成立|获批|投产|上市'
    for e in map(record,rows):
        for sentence in re.split(r'[。；\n]',e.get('content','')):
            sentence=sentence.strip()
            years=_period(sentence)
            scope=_scope(sentence)
            for m in re.finditer(r'('+metric+r')[为约达是：:\s]*(\d+(?:\.\d+)?)\s*(万亿|亿|万|千)?\s*(美元|人民币元|欧元|港元|元|人|户|个|兆瓦|千瓦|MW|GW)?',sentence):
                if len(years)>1:continue  # 多年份比较中不猜测数字归属。
                name='投资总额' if m[1] in {'投资总额','总投资','投资额'} else '营业收入' if m[1]=='营收' else m[1]
                scale={'万亿':Decimal('1000000000000'),'亿':Decimal('100000000'),'万':Decimal('10000'),'千':Decimal('1000'),None:Decimal(1)}[m[3]]
                amount=Decimal(m[2])*scale
                unit=m[4] or ('元' if name in {'投资总额','营业收入','净利润'} else '人' if name in {'员工人数','员工数'} else '')
                if unit=='人民币元':unit='元'
                if unit=='GW':amount*=1000;unit='MW'
                if unit=='兆瓦':unit='MW'
                if unit=='千瓦':amount/=1000;unit='MW'
                if name=='投资总额' and unit=='元':
                    value=str(round(float(amount/10000),4))  # 兼容旧版万元值。
                else:value=format(amount.normalize(),'f')+unit
                add(e,'numeric',_subject(sentence[:m.start()]),name,years,scope+('单位:'+unit,),value)
            patterns=[r'(.{2,50}?)(?:于|在)('+date+r')(?:正式)?('+event+r')',
                      r'(.{2,50}?)('+event+r')(?:日期|时间)[为是：:\s]*('+date+r')']
            for index,pattern in enumerate(patterns):
                for m in re.finditer(pattern,sentence):
                    when,action=(m[2],m[3]) if index==0 else (m[3],m[2])
                    parts=[str(int(n)) for n in re.findall(r'\d+',when)]
                    when='-'.join(parts)
                    add(e,'event_time',_subject(m[1]),action+'时间',(),scope+('日期精度:'+str(len(parts)),),when)
            for m in re.finditer(r'(.{2,50}?)(已经|已|尚未|并未|未|没有)(?:正式)?(获批|开工|投产|上市|支持离线部署|开放源代码|开源)(?:[，,\s]|$)',sentence):
                polarity='肯定' if m[2] in {'已经','已'} else '否定'
                add(e,'factual',_subject(m[1]),m[3],years,scope,polarity)
            for m in re.finditer(r'(.{2,50}?)(不支持|支持)(离线部署|私有化部署|商业使用)(?:[，,\s]|$)',sentence):
                add(e,'factual',_subject(m[1]),'支持'+m[3],years,scope,'否定' if m[2]=='不支持' else '肯定')
    found=[]
    for (q,kind,subject,topic,years,scope),values in groups.items():
        if len(values)<2:continue
        found.append({'subquestion':q,'kind':kind,'subject':subject,'topic':topic,'period':list(years),'scope':list(scope),
                      'values':values,'status':'uncertain',
                      'reason':f'{subject}的{topic}出现不同说法；可能存在更新、范围或口径差异，现有片段不足以裁决，需核验'})
    return found[:8]
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
