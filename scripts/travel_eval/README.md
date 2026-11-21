# 甲分支复测记录

工作基点为 main 的 4ce6f64，开发分支为 feat/travel-core。联调时读取乙分支 ea37cfd 的静态页面和 POI 目录。合并后两端共用仓库中的 static/travel/data/pois.json。

## 环境与启动

Python 3.12、Node 24、npm。完整账号模式使用 MySQL 8，本地无账号模式可以检查路线、天气和报告。PowerShell 在甲的工作区运行以下命令；此机器已有 Python 虚拟环境。

```powershell
Set-Location 'D:\华为云-项目\travel-core'
$py = '..\osint-multi-agent\.venv\Scripts\python.exe'
& $py -m pip install -r requirements.txt
npm ci --prefix scripts/travel_eval/mcp
$env:TRAVEL_STATIC_DIR = (Resolve-Path '..\osint-multi-agent\static').Path
& $py -m uvicorn server:app --host 127.0.0.1 --port 8768
```

本机没有 npm 启动器时，本次使用的等价命令为 `node "$env:TEMP/travel-core-npm/package/bin/npm-cli.js" ci --prefix scripts/travel_eval/mcp`。浏览器打开根路径使用实际后端；带 `?demo=travel` 的页面由乙的前端回放驱动。

| 配置 | 用途 |
|---|---|
| TRAVEL_STATIC_DIR / TRAVEL_POI_CATALOG | 指定乙的静态目录，或单独指定经过核验的 POI JSON 文件 |
| TRAVEL_NODE / TRAVEL_MCP_TIMEOUT | Node 可执行文件、每次含握手的调用超时秒数，默认 node 和 35 |
| HTTP_PROXY、HTTPS_PROXY、NO_PROXY | 从服务进程传入 Node 子进程。本机铁路官网直连成功，按网络情况将 .12306.cn 加入已有 NO_PROXY |
| NODE_EXTRA_CA_CERTS、SSL_CERT_FILE、SSL_CERT_DIR | 证书文件须在运行环境内可读。Node 24 子进程启用 NODE_USE_ENV_PROXY=1 |
| TRAVEL_KB_BASE_URL、TRAVEL_KB_TOKEN | 丙的 HTTP 检索服务与服务端 Bearer 令牌 |
| DASHSCOPE_API_KEY、VARIFLIGHT_API_KEY | 分别供实际模型与按需航班查询使用 |
| TRAVEL_OCR_BINARY | 可选本地 Tesseract，识别语言 chi_sim+eng；缺少引擎时允许手填、确认票面信息 |
| OSINT_STORAGE、OSINT_DB_* | 沿用原项目 MySQL 登录模式；仅专用测试实例设置 OSINT_TEST_MYSQL=1 |

迁移空库依次执行 deploy/mysql/001_initial.sql 、002_travel.sql 和 003_account_security.sql。已有数据库按版本升至 v3。Compose 新数据卷自动按顺序执行，已有数据卷需由迁移账号执行增量 SQL。

## 复测命令

在上节设置的 PowerShell 中执行。接口和冻结时钟样例在 contracts/travel/v1/fixtures。天气参数应换成复测当天预报窗口内的出游日。

```powershell
& $py scripts/travel_eval/probe.py --start 2026-11-21
& $py scripts/travel_eval/run_demo.py
& $py scripts/travel_eval/run_demo.py --unavailable
& $py scripts/travel_eval/scenic_card.py
& $py -m unittest discover -s tests/travel_core -v
& $py scripts/travel_eval/evaluate.py
& $py -m unittest discover -s tests
```

数据库验证命令为 `python scripts/travel_eval/mysql_verify.py`。为该命令提供专用 MySQL 测试账号、主机、端口与 OSINT_TEST_MYSQL=1；它会创建两个随机命名的测试库并保留以供检查。普通应用账号只需 DML 权限。对迁移完成的专用库设置 OSINT_DB_NAME 可运行账号与隔离测试。

系统评测默认固定工具响应，输出 outputs/travel_eval/replay.jsonl。配置可调用模型后执行 `python scripts/travel_eval/evaluate.py --real-model --split dev` 可生成逐题的选用技能、全技能、完整证据三组记录与实际模型用量。模型调用会消耗账户额度。

## 2026-11-20 验证

| 类别 | 实际结果 |
|---|---|
| 真实 OSM MCP | osm-mcp@0.3.2 的 initialize、tools/list、route 均通过。铜仁站至东门 66,725 m、3,286 s，至西门 114,558 m、5,642 s。西门首次上游报错，随后复测成功。仅为驾车估算 |
| 真实天气 MCP | open-meteo-mcp-server@2.5.6 的 initialize、tools/list、weather_forecast 均通过。江口方向东门坐标，Asia/Shanghai，11 月 21、22、23 日最大降雨概率分别为 0%、12%、95%。首页观测状态 unknown |
| 实际故障注入 | 在测试进程把 OSRM_BASE_URL 指向不可用域名。route.leg 发出 tool_call、tool_error；任务继续查询天气并完成。SSE 可读取，下载件包含待查项 |
| 真实铁路 | 12306-mcp@0.3.10 在本机直连时查询到 11 月 21 日贵阳北至铜仁四趟候选，含 G3370。代理连接曾在初始化时中断 |
| 真实 MySQL 8.0.41 | 空库和保留历史账号、报告的 v1 库均通过迁移与重跑。六个并发相同请求只预留一次；失败释放、再次预留、重复模拟订单终态通过。账号 API 测试覆盖附件隔离、幂等冲突、完成结算、报告隔离及删除附件 |
| 实际本地景点卡 | 原项目 MCP 已列出 get_scenic_card，实调 fanjingshan 返回梵净山和官方小程序预约类型。第三方智能体平台尚待团队选定和登记 |
| 回放 | 12 个开发场景和 36 个九市州合成验收场景的程序断言通过。知识库客户端覆盖命中、空结果、相互冲突的来源、超时和 503。合成题面与事实仍待人工复核 |
| 待外部联调 | 丙的真实语料与服务、飞常准凭证、第三方平台、云 HTTPS / RDS、真实用户试走、真实模型 Token 对照。没有测得 Token 节省率。票价来源缺失时保留待查，当前没有实时途牛回执 |

[路线服务](https://github.com/ni-c/osm-mcp) · [天气服务](https://github.com/cmer81/open-meteo-mcp) · [铜仁站点位](https://www.openstreetmap.org/node/1681825151) · [铜仁南站点位](https://www.openstreetmap.org/node/2953807111)
