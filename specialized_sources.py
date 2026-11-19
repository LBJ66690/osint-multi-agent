"""专项检索规则扩展点；不注册或偷偷启用额外 Provider。"""
import re
from urllib.parse import urlsplit

def source_quality(url):
    try:host=(urlsplit(url).hostname or '').lower()
    except ValueError:host=''
    if host=='gov.cn' or host.endswith(('.gov.cn','.edu.cn','.ac.cn','.edu')):
        return {'level':'primary','weight':2,'reason':'政府/高校/科研机构域名规则，不代表事实已核实'}
    if host in {'xinhuanet.com','www.xinhuanet.com','people.com.cn','www.people.com.cn','cctv.com','www.cctv.com'}:
        return {'level':'secondary','weight':1,'reason':'已知媒体域名规则'}
    return {'level':'ordinary','weight':0,'reason':'来源身份未由本程序验证'}

def refine_query(question, original, index=0, conflict=False):
    base=re.sub(r'\s+site:\S+','',original).strip()
    if conflict:return f'{question} 投资总额 官方 公告'
    if re.search('政策|条例|财政|政府|补贴',question):return base+' site:gov.cn'
    if '贵州大学' in question:return base+' site:gzu.edu.cn'
    if re.search('学术|论文|研究进展',question):return base+(' site:arxiv.org' if index%2==0 else ' site:openalex.org')
    if re.search('开源|软件|github',question,re.I):return base+' site:github.com'
    return question+(' 官方公告 原始来源' if index%2==0 else ' 最新资料 独立报道')
