"""启动临时本地服务并验证静态资源/配置接口，结束后自动关闭。无模型调用。"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
from server import app

async def main():
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=8877,log_level='warning'))
    task=asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started:break
            if task.done():await task
            await asyncio.sleep(.05)
        assert server.started,'server did not start'
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8877',trust_env=False,timeout=5) as client:
            for path in ['/','/static/app.js?v=20','/static/style.css?v=20','/static/visual-effects.js?v=20','/static/visual-effects.css?v=20','/api/search-sources','/api/models','/api/model-recommendation?execution_mode=deep','/api/health']:
                response=await client.get(path);response.raise_for_status();print(path,response.status_code,flush=True)
            response=await client.get('/api/search-sources');assert [s['id'] for s in response.json()['sources']]==['parallel','you','bailian']
            response=await client.get('/api/models');assert response.json()['role_defaults']['planner']=='qwen3.8-flash'
            assert any(m['id']=='deepseek-v4.1-flash-1017' for m in response.json()['models'])
            for mode in ['quick','standard','deep']:
                response=await client.get('/api/model-recommendation',params={'execution_mode':mode})
                assert response.json()['role_models']==__import__('models').role_defaults(mode)
    finally:
        server.should_exit=True
        await task

if __name__=='__main__':asyncio.run(main())
