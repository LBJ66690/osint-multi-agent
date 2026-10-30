# 开源情报（OSINT）多智能体平台

首次部署或将项目交给其他人使用，请先阅读 [部署与使用指南](部署与使用指南.md)，其中包含完整安装、密钥输入、启动、验收和排错步骤。

依据《Designing Multi-Agent Systems》一书的原则与模式，用 **Microsoft Agent Framework (MAF)** 编排 5 个智能体，
**多模型分工**调用阿里云百炼（Model Studio）云端模型，通过 **MCP** 联网收集情报，并提供一个
"领导驾驶舱"风格的 **Web 界面**，实时展示实施进度与工具调用。

## 它做什么

输入一个情报任务（例如："看一下小红书上对贵阳花溪区旅游的评价，输出舆情分析报告"），系统依次：

1. **规划官**：理解意图、拆解子问题 —— ★ 经人工确认后才继续；
2. **收集官**：先由用户选择渠道，再通过免费公开搜索接口和/或百炼联网搜索 MCP 收集带来源的资料；
3. **分析官**：清洗去重、别名归一（如：黔=贵州）、归纳观点与情感；
4. **推理官**：形成核心发现，配齐证据 / 反证 / 来源；
5. **报告官**：从本地工具取报告模板与本地情报，输出《舆情分析》《资政建议》等报告。

### 简明解答的默认分工

| 岗位 | 模型 | 选型理由 |
|---|---|---|
| 规划官 | `qwen3.8-flash` | 快速生成短计划，关闭额外思考 |
| 收集官 | `deepseek-v4.1-flash` | 快速模型，高并发联网检索、工具调用稳定 |
| 分析官 | `deepseek-v4.1-flash` | 普通任务关闭额外思考，提炼精选证据 |
| 推理官 | `deepseek-v4.1-flash` | 标准研究低强度推理，深入研究提高强度 |
| 报告官 | `qwen3.8-27b` | 适度篇幅的中文写作；模板在本地直接提供 |
| 核查官 | `qwen3.8-flash` | 独立检查最终报告的关键陈述与引用 |

> 分工集中在 `models.py` 的 `ROLE_DEFAULTS`，前后端共用。左上角默认使用推荐配置，切换“手动配置”可逐角色选择，并可一键恢复。免费 MCP 直采时不会调用收集模型。

### 三种研究深度的默认模型（2026-10-30）

| 岗位 | 简明解答 | 标准研究 | 深入研究 |
|---|---|---|---|
| 规划 | Qwen3.8 Flash | Qwen3.8 27B | Qwen3.8 Max |
| 收集（仅百炼搜索） | DeepSeek V4.1 Flash | DeepSeek V4.1 Flash | DeepSeek V4.1 Flash 1017 |
| 分析 | DeepSeek V4.1 Flash | DeepSeek V4.1 Flash 1017 | DeepSeek V4.1 Flash 1017 |
| 推理 | DeepSeek V4.1 Flash（通常合并分析） | DeepSeek V4.1 Flash 1017 | DeepSeek V4 Pro 0813 |
| 报告 | Qwen3.8 27B | Qwen3.8 27B | Kimi K3 |
| 核查 | Qwen3.8 Flash | Qwen3.8 27B | Qwen3.8 Max |

三档配置集中在 `models.py:ROLE_PROFILES`。切换深度立即更新推荐模型；自动模式在输入问题后由后端采用同一任务分类规则推荐，实际创建任务也按此规则选择。手动模式切换深度保留人工选择；“恢复推荐配置”恢复当前深度的组合。恢复旧任务的推荐组合遵循该任务检查点中保存的研究深度。

截图中的 10 个模型均在候选列表，新增 `deepseek-v4.1-flash-1017`；其余已存在。GLM-5.3、Qwen3.8 2.4T A95B、0731 等也可手动选择，不要求所有模型都成为默认值。深入研究的强模型可能增加等待与费用，仍受原有时间预算约束。

`GET /api/models` 提供 `role_profiles` 和 `profile_reasons`；`GET /api/model-recommendation?task=...&execution_mode=auto` 返回自动识别深度、岗位模型与理由。新建任务未提交手动岗位覆盖时由服务器选择该深度默认值，CLI 同样生效。

选型参考[阿里云 Qwen Flash](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[DeepSeek Flash](https://help.aliyun.com/zh/model-studio/deepseek-v4-1-flash)、[Qwen 27B](https://help.aliyun.com/zh/model-studio/qwen3-8-27b)与[Responses 参数说明](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)。推荐是本项目对能力与开销的取舍，不是厂商间性能排名。

### 执行深度与恢复（2026-10-30）

| 深度 | 子问题上限 | 精选证据上限 | 本次工作时间预算 |
|---|---:|---:|---:|
| 简明解答 | 1 | 8 | 5 分钟 |
| 标准研究 | 3 | 16 | 8 分钟 |
| 深入研究 | 5 | 24 | 12 分钟 |

自动模式按问题范围选择深度。简单问答合并分析与推理，通常需要规划、整理、报告、最终核查 4 次逻辑模型调用。标准模式通过程序检查结构、覆盖和引用范围，保留最终报告独立核查；深入模式增加分析、推理的定向模型核查。程序检查通过不代表事实已经核验。

预算排除人工确认、渠道选择与核查决策等待。模型调用、JSON 修复和网络重试共用阶段时间预算，SDK 隐式重试关闭；报告最多一次流式请求加一次普通请求，共用报告预算。达到预算会停止并保存进度，不保证在预算内一定产出合格报告。

推理官单步预算现为简明 75 秒、标准 120 秒、深入 360 秒（仍受本次任务剩余预算约束）。聊天客户端的网络等待设为对应岗位预算再加 10 秒，避免旧的固定 75 秒网络超时提前中断深度推理；应用层仍控制总等待。前端活动会显示每次模型请求的次数、返回或失败状态、实际耗时。

推理专用证据上下文最多 12 条、16000 字符、每条摘录最多 500 字符，结构化知识另行提供。完整采集资料保持留档；减少重复全文输入，避免单靠延长等待。

断点使用原子替换保存到 `outputs/checkpoints/`。每次成功查询、模型产出、核查和人工决策分别保存。失败后只重新执行未成功的操作；已完成的报告正文和核查可复用。重写意图先保存，恢复时不会误用待重写的旧报告；附录从原始正文重新生成，避免重复追加。本次恢复的工作预算重新计算，历史计时保留。

恢复按钮现默认保留该任务原有模型。只有主动勾选“本次恢复改用上方模型配置”，才覆盖尚未完成调用的模型。页面显示复用哪些阶段、从哪个阶段继续；检查点缺失、结构损坏或阶段产出不完整会明确拒绝恢复，不会静默从头运行。服务重启后可从磁盘恢复失败任务，重复提交恢复请求会被拦截。

完整证据留档，模型上下文另按 URL 去重、跨子问题均衡选取相关片段。免费搜索在每个渠道内复用 MCP 会话与工具列表，不同渠道并行。

## 系统架构

```
┌────────────────────────────────────────────────────────┐
│  前端 static/（深色"指挥大屏"，原生 HTML/CSS/JS）          │
│  流水线进度 · 工具调用流 · 计划/证据/发现/报告 · 人工确认弹窗 │
└───────────────▲──────────────────────┬──────────────────┘
                │ SSE 实时事件          │ POST 建任务 / 确认
┌───────────────┴──────────────────────▼──────────────────┐
│  server.py（FastAPI + SSE）：RunState 状态机、人工确认门     │
│  pipeline.py：规划→收集→分析→推理→报告（事件驱动）          │
└───────────────┬─────────────────────────────────────────┘
                │ MAF Agent
        ┌───────▼────────┐        ┌──────────────────────┐
        │ 远程 MCP        │        │ 进程内 FunctionTool    │
        │ 百炼联网搜索     │        │ 报告模板 / 本地情报     │
        └───────▲────────┘        └──────────▲───────────┘
                │ OpenAI 兼容接口            │
        ┌───────┴────────────────────────────┴───────────┐
        │ 阿里云百炼：Qwen / DeepSeek / GLM / Kimi 多模型    │
        └─────────────────────────────────────────────────┘
```

## 准备步骤

1. Python 3.10+；本次完整验收环境为 Python 3.10，其他版本与全新机器依赖安装需另行验证。
2. 安装依赖：
   ```powershell
   python -m pip install agent-framework-core agent-framework-openai mcp openai python-dotenv fastapi uvicorn
   ```
3. 获取百炼 API Key：<https://help.aliyun.com/zh/model-studio/get-api-key>。
4. 如需使用原有百炼搜索，在“百炼 MCP 广场”开通 **联网搜索 WebSearch**；只选新增免费接口时不需要开通此 MCP。
5. 可复制 `.env.example` 为 `.env` 配置模型、端口等非敏感选项。不要在文件中保存 API Key；程序不再从 `.env` 读取密钥。

## 运行

**Web 版（推荐，可看实时进度与工具调用）：**
```powershell
python launch.py
```
也可双击 `启动项目.bat`。每次启动都要在终端输入百炼 API Key（输入隐藏），服务就绪后自动打开 <http://127.0.0.1:8765>。密钥仅保存在当前服务内存，不写入配置、日志、检查点或浏览器。关闭服务后下次重新输入。端口已占用时停止，不复用旧服务。`--check` 仅检查依赖，不输入密钥、不启动服务。

### 动态指挥台与通用搜索 MCP（2026-10-29）

界面提供中央运行核心、旋转环和呼吸状态、与请求联动的通道数据流、大号累计计时、网页/子问题/请求统计及实时活动。右侧可手动查看各阶段，“返回当前阶段”恢复跟随；查看旧结果不会改变后台执行。支持专注模式、减少动效和窄屏布局。

计划确认后必须手动选择搜索 MCP，再开始第二步。关闭弹窗后可通过全局待办重新打开；刷新页面后点击研究记录恢复查看。模型配置位于左侧折叠区域。手机使用“展开研究记录”访问历史任务。

上一版六个专门资料接口已从项目的搜索实现和选择列表撤下，替换为真正的通用网页搜索 MCP：

| 渠道 | 地址与工具 | 使用范围 |
|---|---|---|
| Parallel | `https://mcp.parallel.ai/mcp` · `web_search` | 免费匿名档，无需 Key，返回通用网页链接及相关正文摘录；有服务端限流 |
| You.com | `https://api.you.com/mcp?profile=free` · `you-search` | 免 Key 免费档，官方标明每日 100 次；返回网页、摘要和片段 |
| 百炼 WebSearch | 原有远程 MCP | 保留为显式可选项，需要权限，额度用尽可能计费 |

新增 MCP 已先独立测试握手、工具发现和中文搜索，再通过正式适配器测试中文旅游、中文网络用语、英文科技三类查询。Open-WebSearch 托管地址测试返回 402，未接入。所有查询保留来源链接与片段，免费指搜索免费档，模型生成另计。真实平台覆盖以检索结果为准。

每任务每个新 MCP 最多 12 次请求，相同查询缓存；单次调用含握手最多 45 秒，含排队最多 60 秒；失败保留其他通道结果，不自动启用百炼。百炼每子问题最多 180 秒，最多 3 个并发会话。未完成收集的恢复会再次要求选择通道，旧接口 ID 不再接受。

任务详情带服务器时间快照，区分累计执行与人工等待；没有可靠历史时间的数据显示“—”。新的关键事件落盘，重连不重复计数。核查穿插阶段时，会显示当前核查动作。

### 报告流式中断恢复

对 `incomplete chunked read` / `APIConnectionError` 等临时故障，最多尝试两次流式生成，再切换一次普通响应；每次请求最多 240 秒，永久鉴权错误不重试。每次重开清空该报告当前版本，避免重复拼接；不完整草稿以 `.partial.md` 保存，不标成最终报告。每份成功报告即时保存检查点，新版任务恢复时复用已成功的报告。

```powershell
# 独立 MCP 握手、工具发现、真实搜索
python scripts/probe_web_mcp.py
# 正式适配器：三类主题 × 两个 MCP，不调用模型
python scripts/test_live_search_sources.py
# 模拟断流、恢复、计时和选择流程，不访问付费服务
python -m unittest discover -s tests -v
```

测试记录见 `搜索接口测试记录.md`、`outputs/web_mcp_probe.json`、`outputs/web_mcp_adapter_probe.json`。浏览器回归脚本为 `scripts/test_search_ui.cjs`，使用本地 Playwright，API 和事件流均为固定测试数据。

Web API：`GET /api/search-sources` 返回候选；详情中的 `pending_sources.request_id` 配合 `POST /api/runs/{run_id}/sources` 提交 `{"request_id":"...","sources":["parallel","you"]}`。空选择、已撤下渠道、过期选择都会拒绝。任务详情的 `timeline` 包含服务器时间、阶段累计耗时和人工等待区间。

**命令行版：**
```powershell
python main.py
```

## Web 界面能看到什么

- **流水线进度**：总进度条 + 5 个阶段卡片，每个阶段标注"当前由哪个模型负责"；
- **工具调用**：实时滚动显示每一次 MCP 搜索 / 本地工具的调用参数与返回结果；
- **分析结果**：计划 / 证据 / 发现 / 报告四个标签页，报告可直接阅读（内置 Markdown 渲染）；
- **人工确认门**：规划完成后弹窗，可"确认执行"或填写修改意见让其重新规划；
- **历史记录**：左侧列出每次运行，可点击回放。

## 目录说明

| 文件 | 作用 |
|---|---|
| `config.py` | 依赖引导、读取 Key / 模型 / 百炼端点、服务 Host/Port |
| `models.py` | 可选模型清单与分级（旗舰/高阶/均衡/轻量） |
| `schemas.py` | 智能体间传递的 Pydantic 结构化模型 |
| `agents.py` | 百炼客户端、MCP / FunctionTool（可观测）、5 个智能体、多模型分工 |
| `pipeline.py` | Web 事件驱动主编排（emit 进度、confirm 人工确认） |
| `server.py` | FastAPI + SSE 后端，全套 REST 接口 |
| `main.py` | 命令行版编排 |
| `local_knowledge.py` | 本地知识：报告模板 + 内部情报（进程内工具调用） |
| `mcp_local_server.py` | 早期 stdio 版自建 MCP（已被进程内工具取代，保留参考） |
| `static/` | 前端 `index.html` / `style.css` / `app.js` |
| `outputs/` | 报告（.md）与中间产物（plan/evidence/analysis 的 JSON） |
| `vendor/` | 离线依赖兜底（置于 `sys.path` 末尾） |

## 早期版本自测记录（不代表当前默认编排）

- 规划官 Qwen3.8-27B：输出 6 个子问题与报告类型；
- 收集官 DeepSeek V4.1 Flash：经 MCP 完成 27 次联网搜索、汇总 27 条带来源资料；
- 分析官 GLM-5.3：别名归一 7 组、归纳观点 31 条；
- 推理官 DeepSeek V4 Pro：形成 10 条核心发现（含证据 / 反证 / 来源）；
- 报告官 Kimi K3：调用 `get_report_template`、`get_local_briefing` 后产出 4000+ 字舆情报告
  （见 `outputs/舆情分析_多模型分工_demo.md`）；
- SSE 实时推送、人工确认门、前端各标签页均正常。

## 费用说明（简）

- 免费 MCP 的匿名额度与限流以服务方当时政策为准；百炼搜索可能计费，仅在勾选后调用；
- 本地工具：免费；
- 模型：按 token 计费，`flash` / 轻量档最省。报告 / 收集优先用 flash，关键推理才用旗舰，可显著降本。

## 关于"国产化"正式版（重要）

需求中甲方要求：**不使用国外框架、大模型部署在国产显卡上**。本项目是"流程可跑通的原型"，
正式交付时可平滑替换：

- **模型**：百炼云端 → 用 vLLM 在国产显卡部署 Qwen / DeepSeek（仍是 OpenAI 兼容端点，改 `base_url` 即可）；
- **框架**：MAF → 国产 Agent 框架，各智能体职责、指令与工具可平移；
- **MCP 与数据层**：保持不变；
- 另需补齐：权限与加密、分析人员人机录入情报经验等。

## 常见问题

- **401 / 鉴权失败**：API Key 无效或填错；
- **MCP 工具为空 / ConnectTimeout**：未开通 WebSearch，或网络瞬时超时（已内置 3 次连接重试与 90 秒超时，重跑即可）；
- **搜索无结果**：关键词过窄，可在确认计划时调整；
- **JSON 解析报错**：同一阶段预算内最多尝试两次；仍失败则保存检查点，恢复时复用已完成步骤。

## 当前版本验证命令

```powershell
python -B -m unittest discover -s tests -v
python -B scripts/probe_role_models.py
python -B scripts/test_live_search_sources.py
python -B scripts/smoke_server.py
node scripts/test_search_ui.cjs
```

真实模型与 MCP 测试结果分别保存于 `outputs/role_model_probe.json` 与 `outputs/web_mcp_adapter_probe.json`。`scripts/smoke_research.py` 会创建一条独立真实研究，调用模型会消耗账户额度，结果保存在 `outputs/research_smoke_result.json`。

### 全面验收

`python -B scripts/run_acceptance.py` 将逐用例日志与结构化结果写入 `outputs/acceptance/`，故障注入日志单独保存，不混入正常服务日志。CLI 可通过 `python main.py --resume RUN_ID` 恢复；启动脚本 `启动项目.bat --check` 只检查解释器与依赖，不打开服务、不自动安装依赖。

`scripts/probe_all_models.py` 对全部候选发送真实小请求。确认的 403 免费额度耗尽结果会在同一账户下缓存一小时，模型列表标注为暂不可选；网络瞬断不作为禁用依据，账户条件变更后可重新实测刷新。脚本不会更改付费开关。

浏览器实际后端联调：先运行 `python scripts/serve_acceptance_fixture.py`，再运行 `node scripts/test_real_web.cjs`。端口为 8890；仅模型和搜索返回固定数据，其余 API、SSE、检查点、报告及浏览器操作均为真实代码。验收后关闭该临时服务。


## 公开源码包说明

本仓库不提供 API Key、个人 `.env`、历史任务、证据、报告或日志。每位使用者应使用自己的账户，启动时隐藏输入 Key。完整安装步骤见 [部署与使用指南](部署与使用指南.md)。

本地回归可以运行：

```powershell
python -B scripts/run_acceptance.py
```

从干净仓库运行时，依赖历史真实 MCP 探测产物的一个用例会明确跳过；其他测试使用临时目录和模拟数据。这不代表已验证当前账户和网络的外部服务可用性。真实搜索可另运行 `scripts/test_live_search_sources.py`。

可选浏览器界面测试需要自行安装 Node.js、Playwright 及相应浏览器。先生成不含账户凭据的配置样本，再执行：

```powershell
python -B scripts/export_ui_fixtures.py
node scripts/test_search_ui.cjs
```

生成的 `outputs/` 仍被 Git 忽略。`tests/test_search_sources.py` 中的历史探测回放与这些界面配置样本是不同数据，不需要分享个人任务来补齐它。
