"""小请求验证默认模型和当前 Responses 参数，不执行完整研究任务。"""
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config
from agents import build_agent


async def main():
    rows = []
    variants=[("qwen3.8-flash","planner","quick"),("deepseek-v4.1-flash","analyst","quick"),("qwen3.8-27b","reporter","standard"),("qwen3.8-flash","verifier","quick")]
    if '--profiles' in sys.argv:
        variants=[('deepseek-v4.1-flash-1017','analyst','standard'),('deepseek-v4.1-flash-1017','analyst','deep'),('qwen3.8-max','planner','deep'),('deepseek-v4-pro-0813','reasoner','deep'),('kimi-k3','reporter','deep')]
    for model, role, mode in variants:
        agent = build_agent(model, '仅输出 JSON：{"ok":true}，不要附加内容。', "兼容性验证", role=role, mode=mode, output_tokens=8192)
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(agent.run('请输出规定的 JSON。'), 40)
            body = response.text.strip()
            row = {"model": model, "role": role, "ok": '"ok"' in body and 'true' in body, "text": body[:160]}
        except Exception as exc:
            row = {"model": model, "role": role, "ok": False, "error": str(exc)[:500]}
        finally:
            await agent.client.client.close()
        row["seconds"] = round(time.monotonic()-start, 2)
        row['mode']=mode
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    (config.OUTPUT_DIR/('profile_model_probe.json' if '--profiles' in sys.argv else 'role_model_probe.json')).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

if __name__ == '__main__':
    asyncio.run(main())
