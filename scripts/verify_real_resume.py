"""克隆一个推理失败任务，通过 HTTP 恢复；原任务和原报告保持原样。"""
import asyncio
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
import config
import pipeline
import server

async def main():
    source=pipeline.get_checkpoint_path(sys.argv[1])
    original=source.read_bytes();data=json.loads(original)
    assert data.get('completed_stages')==['planning','collecting','analyzing'],'需要推理阶段失败的测试样本'
    assert not data.get('reports') and not data.get('report_state'),'避免克隆已有报告路径'
    rid='resumecheck_'+uuid.uuid4().hex[:8]
    data.update(run_id=rid,status='error')
    await pipeline.save_checkpoint(rid,data)
    server._restore_history_from_disk()
    restored=server.runs[rid]
    web=uvicorn.Server(uvicorn.Config(server.app,host='127.0.0.1',port=8878,log_level='warning'))
    web_task=asyncio.create_task(web.serve())
    start=time.monotonic();accepted=[];last=None
    try:
        for _ in range(100):
            if web.started:break
            await asyncio.sleep(.05)
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8878',trust_env=False,timeout=10) as client:
            response=await client.post(f'/api/runs/{rid}/resume',json={});response.raise_for_status()
            print(json.dumps(response.json(),ensure_ascii=False),flush=True)
            while True:
                detail=(await client.get(f'/api/runs/{rid}')).json()
                position=(detail['status'],detail['timeline'].get('current_stage'))
                if position!=last:
                    print(json.dumps({'status':position,'elapsed':round(time.monotonic()-start)},ensure_ascii=False),flush=True);last=position
                if detail['status']=='awaiting_gate':
                    accepted.append(detail['pending_gate'])
                    await client.post(f'/api/runs/{rid}/gate',json={'decision':'continue','feedback':'恢复验收：保留核查问题以供审阅'})
                if detail['status'] in {'done','error','cancelled'}:break
                await asyncio.sleep(1)
            await restored.task_obj
            after=pipeline.load_checkpoint(rid)
            assert source.read_bytes()==original,'原任务不得被修改'
            for key,row in data.get('operations',{}).items():
                if key.startswith(('planning:','search:','analyzing:')):
                    assert after['operations'][key]==row,'已成功操作不应重做'
            assert not any(e['type']=='stage' and e.get('status')=='running' and e.get('stage') in {'planning','collecting','analyzing'} for e in detail['events'])
            result={'ok':detail['status']=='done','run_id':rid,'source_run_id':sys.argv[1],
                    'seconds':round(time.monotonic()-start,2),'source_unchanged':True,'reused_stages':['planning','collecting','analyzing'],
                    'accepted_gate_issues':len(accepted),'reports':after.get('reports',[]),'error':after.get('last_error')}
            if result['ok']:
                download=await client.get(f'/api/runs/{rid}/report.md');download.raise_for_status()
                result['download_chars']=len(download.text)
            (config.OUTPUT_DIR/'real_resume_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False),flush=True)
            return 0 if result['ok'] else 1
    finally:
        if restored.task_obj and not restored.task_obj.done():
            restored.task_obj.cancel();await asyncio.gather(restored.task_obj,return_exceptions=True)
        web.should_exit=True
        await web_task

if __name__=='__main__':sys.exit(asyncio.run(main()))
