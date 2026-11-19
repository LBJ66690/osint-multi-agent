"""一条真实简明研究验收；独立测试任务，不续跑或修改用户历史任务。"""
import asyncio
import json
import sys
import time
import uuid
import argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
import pipeline

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('run_id',nargs='?');parser.add_argument('--mode',choices=['auto','quick','standard','deep'],default='auto');parser.add_argument('--task',default='抖音最近很火的大肥鱼指的是什么东西？');args=parser.parse_args()
    resume = bool(args.run_id)
    run_id=args.run_id if resume else 'smoke_'+uuid.uuid4().hex[:8]
    events=[]
    start=time.monotonic()
    async def emit(event):
        events.append(event)
        if event['type'] in {'stage','operation_done','error','done'}:
            print(json.dumps({k:v for k,v in event.items() if k in {'type','stage','status','operation','elapsed_seconds','message'}},ensure_ascii=False),flush=True)
    async def confirm(_):return ''
    async def select(_):return ['parallel','you']
    accepted=[]
    async def gate(payload):
        redo=payload.get('stage')=='reporting' and not any(row.get('stage')=='reporting' for row in accepted)
        accepted.append({**payload,'decision':'redo' if redo else 'continue'})
        return {'decision':'redo' if redo else 'continue','feedback':'逐条修正核查问题，引用仅使用证据中给定的E编号和URL' if redo else '自动验收保留核查问题，供报告审阅'}
    try:
        result=await pipeline.run_pipeline(args.task,'qwen3.8-flash',emit,confirm,run_id=run_id,select_sources=select,resume=resume,execution_mode=args.mode,gate=gate)
        output={'ok':True,'run_id':run_id,'seconds':round(time.monotonic()-start,2),'summary':result,
                'usage':sum(e.get('total',0) for e in events if e['type']=='usage'),'accepted_gate_issues':accepted}
    except Exception as exc:
        output={'ok':False,'run_id':run_id,'seconds':round(time.monotonic()-start,2),'error':str(exc)}
    (config.OUTPUT_DIR/'research_smoke_result.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    (config.OUTPUT_DIR/f'research_smoke_result_{args.mode}.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False),flush=True)
    return 0 if output['ok'] else 1

if __name__=='__main__':sys.exit(asyncio.run(main()))
