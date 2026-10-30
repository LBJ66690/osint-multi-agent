# main.py -- 命令行（CLI）版情报分析执行入口
# 复用 pipeline.run_pipeline，支持终端格式化输出、人工交互确认与断点续传
from __future__ import annotations

import asyncio
import argparse
import sys
import uuid

import config
import pipeline


def hr(title: str = "") -> None:
    print("\n" + "=" * 64)
    if title:
        print(title)
        print("=" * 64)


async def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description='OSINT 情报任务与断点恢复')
    parser.add_argument('--resume',metavar='RUN_ID',help='从指定任务检查点恢复')
    parser.add_argument('--task',help='新任务内容；省略时交互输入')
    parser.add_argument('--mode',choices=['auto','quick','standard','deep'],default='auto')
    args=parser.parse_args(argv)
    config.check_key()
    hr("OSINT 开源情报多智能体分析平台 (CLI)")

    if args.resume:
        from checkpoint_resume import resume_info
        checkpoint=pipeline.load_checkpoint(args.resume)
        try:resume_info(checkpoint,args.resume)
        except (ValueError,TypeError,KeyError) as exc:
            print(f'无法恢复：{exc}');return 1
        if checkpoint.get('status')=='done':
            print('任务已完成，请查看已保存报告。');return 0
        task,run_id=checkpoint['task'],args.resume
    else:
        task=(args.task or '').strip()
        while not task:task=(await asyncio.to_thread(input,'请输入情报任务：')).strip()
        run_id=uuid.uuid4().hex[:12]

    async def cli_emit(ev: dict):
        ev_type = ev.get("type")
        if ev_type == "stage":
            status = ev.get("status")
            if status == "running":
                hr(f"阶段【{ev.get('stage')}】进行中: {ev.get('label')}")
            elif status == "done":
                print(f"✓ {ev.get('label')}")
        elif ev_type == "plan":
            p = ev.get("plan", {})
            print(f"🎯 目标：{p.get('goal')}")
            for i, sq in enumerate(p.get("subquestions", []), 1):
                print(f"  {i}. {sq.get('question')}")
                print(f"     搜索词: {', '.join(sq.get('search_queries', []))}")
        elif ev_type == "tool_call":
            print(f"  [工具调用] {ev.get('tool')}: {ev.get('args')}")
        elif ev_type == "tool_result":
            prev = ev.get("preview", "")
            print(f"  [工具返回] {prev[:100]}...")
        elif ev_type == "tool_error":
            print(f"  [工具报错] {ev.get('error')}")
        elif ev_type == "evidence":
            print(f"📚 收集到情报资料 {ev.get('count')} 条")
        elif ev_type == "knowledge":
            kn = ev.get("knowledge", {})
            print(f"🧠 分析提炼完成: 别名 {len(kn.get('aliases', []))} 组，观点 {len(kn.get('viewpoints', []))} 条")
        elif ev_type == "findings":
            print(f"🔍 核心研判发现共 {len(ev.get('findings', []))} 条")
        elif ev_type == "report_delta":
            print(ev.get("delta", ""), end="", flush=True)
        elif ev_type == "report":
            print(f"\n📄 报告生成完成: {ev.get('path')}")
        elif ev_type == "error":
            print(f"\n❌ 执行出错: {ev.get('message')}")

    async def cli_confirm(plan: dict) -> str:
        hr("★ 人工确认门")
        print("规划官已拆解出以上计划。")
        fb = (await asyncio.to_thread(input,"是否按计划执行？直接回车=确认执行；输入修改意见=重新规划：")).strip()
        return fb

    async def cli_sources(payload: dict) -> list[str]:
        hr("★ 选择搜索渠道（可多选）")
        options = payload["options"]
        for i, source in enumerate(options, 1):
            cost = "免费公开接口" if source["free"] else "可能计费"
            print(f"{i}. {source['name']} [{cost}]：{source['description']}")
        while True:
            raw = await asyncio.to_thread(input, "请输入渠道编号，以逗号分隔：")
            try:
                numbers = [int(x.strip()) for x in raw.replace("，", ",").split(",")]
                if not numbers or any(n < 1 or n > len(options) for n in numbers):
                    raise ValueError()
                return list(dict.fromkeys(options[n - 1]["id"] for n in numbers))
            except ValueError:
                print("请至少选择一个有效编号。")

    async def cli_gate(payload):
        hr('核查需要决定');print(payload.get('summary',''))
        for issue in payload.get('issues',[]):print(issue.get('claim',''),issue.get('explanation',''))
        while True:
            choice=(await asyncio.to_thread(input,'输入 redo 重做，或 continue 接受问题继续：')).strip()
            if choice in {'redo','continue'}:return {'decision':choice,'feedback':''}

    try:
        summary = await pipeline.run_pipeline(
            task=task,
            model_id=config.resolve_model(),
            emit=cli_emit,
            confirm=cli_confirm,
            run_id=run_id,
            select_sources=cli_sources,
            gate=cli_gate,
            resume=bool(args.resume),
            execution_mode=args.mode,
        )
        hr("全部阶段圆满完成")
        print("报告路径:")
        for r in summary.get("reports", []):
            print(" -", r)
        return 0
    except Exception as e:
        print(f"\n任务中断: {e}")
        print(f"恢复命令：python main.py --resume {run_id}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
