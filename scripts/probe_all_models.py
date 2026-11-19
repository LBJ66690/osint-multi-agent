"""当前配置中全部模型的实际小请求验收，结果与错误均保存。"""
import asyncio,json,sys,time,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config,models
from agents import build_agent
from report_generation import transient

async def main():
    slots=asyncio.Semaphore(2)
    async def probe(model):
        async with slots:
            started=time.monotonic();agent=build_agent(model['id'],'只输出 {"ok":true}，不要解释。','模型验收',role='analyst',output_tokens=8192)
            try:
                response=await asyncio.wait_for(agent.run('请输出规定的 JSON。'),45)
                text=response.text.strip()
                row={'id':model['id'],'ok':'"ok"' in text and 'true' in text,'text':text[:160]}
            except Exception as exc:
                first_error=str(exc)[:350]
                if transient(exc):
                    try:
                        response=await asyncio.wait_for(agent.run('请输出规定的 JSON。'),45)
                        text=response.text.strip();row={'id':model['id'],'ok':'"ok"' in text and 'true' in text,'text':text[:160],'attempts':2,'first_error':first_error}
                    except Exception as retry:row={'id':model['id'],'ok':False,'error':str(retry)[:350],'attempts':2,'first_error':first_error}
                else:row={'id':model['id'],'ok':False,'error':first_error,'attempts':1}
            finally:await agent.client.client.close()
            row['seconds']=round(time.monotonic()-started,2);print(json.dumps(row,ensure_ascii=False),flush=True);return row
    rows=await asyncio.gather(*(probe(model) for model in models.MODELS))
    (config.OUTPUT_DIR/'acceptance_models.json').write_text(json.dumps({'tested_at':time.time(),'account_fingerprint':hashlib.sha256(config.DASHSCOPE_API_KEY.encode()).hexdigest(),'models':rows},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if all(row['ok'] for row in rows) else 1
if __name__=='__main__':sys.exit(asyncio.run(main()))
