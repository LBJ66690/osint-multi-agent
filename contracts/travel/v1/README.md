## 并行开发共用的 v1 接口

公共契约由甲维护，乙、丙分别验证自己实现的一端。仓库中现有的 `/api/runs`、SSE、报告、报告追问和账号入口继续使用。下表中 `/api/travel/`、任务的文旅扩展字段和知识库 v1 接口是**本次约定的新开发内容**。公共示例可直接用于三方本地测试，固定时间为 `2026-11-19T10:00:00+08:00`，示例数据标为 `fixture`。

### 乙调用甲的主程序

| 接口／数据 | 双方约定 |
|---|---|
| 景点目录 | 乙维护 `static/travel/data/pois.json`。页面与主程序读同一文件。地图显示和待联调期间使用固定样例；私有附件通过带账号校验的接口获取 |
| 账号、偏好和余额 | 沿用 `/api/auth/register`、`/api/auth/login` 和 `/api/auth/me`。新增 `GET/PATCH /api/travel/profile` 及 `GET /api/travel/credits`。余额含 `available`、`reserved`、`used`。已有会话在服务端判断首次赠额 |
| 预览和附件 | `POST /api/travel/attachments` 传图片表单，返回 `attachment_id`、`suggested_fields` 供游客修正。`POST /api/travel/drafts/preview` 接收 `task`、`overrides`、`attachment_ids`，返回 `trip` 与 `missing_fields`。预览不占任务额度，游客确认后才创建 run |
| 开始、恢复和取消 | 扩展 `POST /api/runs`，保留 `task`，增加 `domain`、`trip` 和 `attachment_ids`。文旅创建时乙发送稳定的 `Idempotency-Key`，重试沿用同一键。响应保留 `run_id`、`model_id`。恢复／取消走现有 `/api/runs/{id}/resume`、`/cancel` |
| 进度与报告 | `GET /api/runs/{id}/events` 继续用 SSE，按 `Last-Event-ID` 或 `after` 恢复。文旅工具事件含 `run_id`、`type`、`stage`、`tool`、`call_id`；结果／错误提供可展示的脱敏说明和 `data_mode`。`GET /api/runs/{id}` 的完成详情新增 `trip_plan`、`evidence`。`GET /api/runs/{id}/report.md` 下载，`GET/POST /api/runs/{id}/report-chat` 追问 |
| 模拟支付 | `POST /api/travel/demo-orders` 接收服务端已有 `sku`；`POST /api/travel/demo-orders/{id}/complete` 接收 `outcome=success/cancelled/failed`，返回订单状态与最新余额。同一订单同一结果重复提交不重复入账。取消／失败订单不可通过迟到的成功回报入账 |
| 首页天气 | `GET /api/travel/home-weather` 返回 `location`、`kind=observation/forecast/unknown`、`condition`、`as_of`、`stale` 及 `data_mode`。乙按类型、地点和新鲜度选动作，甲负责数据查询、归一化和缓存 |

任务创建示例。预算字段使用人民币分，UI 输入「2000 元」对应 `budget_fen=200000`。示例只固定需求，未来输出取决于真实数据与可行性判断。

```json
{
  "domain": "guizhou_travel",
  "task": "帮我拟一个明天从贵阳去梵净山的三天、不超过2000元的旅游计划。",
  "trip": {
    "origin": "贵阳", "poi_ids": ["fanjingshan"], "start_date": "2026-11-20",
    "end_date": "2026-11-22", "timezone": "Asia/Shanghai", "travelers": 1,
    "budget_fen": 200000, "budget_scope": "group", "include_round_trip": true
  },
  "attachment_ids": []
}
```

`trip_plan` 至少含 `outcome`（枚举 `ready`、`needs_confirmation`、`infeasible`）、`days`、`budget`、`weather_alternatives`、`booking_entries`、`media`、`evidence_ids`、`unresolved`。行程是否可行与 run 是否执行完成分别表达。甲发布一份完成详情和一段有序的 SSE 样例，其中覆盖工具失败、最终完成、断线恢复；另给 `401` 会话失效、`409` 余额不足（错误码 `insufficient_credit`）及同一幂等键携带不同内容（`idempotency_conflict`）样例。`data_mode` 使用 `live`、`cache` 或 `fixture`。乙凭此即可完成 UI 异常态。

前端首日可直接用创建回包 `{"run_id":"demo-fjs","model_id":"fixture"}` 和下面这条 SSE 记录驱动人物翻笔记。重连时重复发送同一个 `id`，气泡和语音都只应出现一次。收到 `stream_end` 时从 run 详情确认最终状态。

```text
id: 6
data: {"run_id":"demo-fjs","type":"tool_result","stage":"collecting","tool":"rag.retrieve","call_id":"q1","message":"已取回一条梵净山东门的游览经验。","data_mode":"fixture"}
```

### 甲调用丙的知识库

丙提供 `POST /v1/retrieve`，甲从服务端读取 `TRAVEL_KB_BASE_URL` 与 `TRAVEL_KB_TOKEN`。联调采用 `Authorization: Bearer <token>`。如果内部产品的字段或接口不同，由丙在 `knowledge/` 中适配。浏览器经主程序取证据，不持有检索凭证。

```json
{
  "query": "梵净山东门雨天游览，游客提到哪些候车问题？",
  "poi_ids": ["fanjingshan-east"], "region": "铜仁",
  "trip_dates": ["2026-11-20", "2026-11-22"], "k": 4
}
```

```json
{
  "corpus_version": "demo-v1",
  "hits": [{
    "doc_id": "demo-note-01", "chunk_id": "c2", "poi_id": "fanjingshan-east",
    "title": "东门雨天游览记录（回放样例）", "excerpt": "该游客称雨后候车时间较长。",
    "source_type": "traveler_note", "source_url": null, "author_display": "团队样例",
    "published_at": "2026-10-01", "retrieved_at": "2026-11-19T10:00:00+08:00",
    "score": 0.78, "rights": "team_fixture"
  }]
}
```

- `trip_dates` 的两个值是包含首尾的日期区间，`poi_ids` 可以是景区或入口。`k` 为请求的最多条数。`source_type` 至少区分 `official_notice`、`traveler_note` 和 `field_note`；未知发表时间返回 `null`。有时效边界的公告可增加 `valid_from`、`valid_to`。相关性分数只参与检索排序。
- 正常及无命中均返回 `200`，无命中是 `hits: []` 且保留 `corpus_version`。上游故障返回 `503`，超时返回 `504` 或由甲记录客户端超时；鉴权错误返回 `401/403`。遇到冲突时返回两条独立来源，附各自片段和日期，甲据此决定如何撰写报告。
- `GET /v1/health` 返回 `status` 和 `corpus_version`。正式命中需有真实 URL，或有权限回看的 `doc_id+chunk_id` 与允许展示的快照。甲保存本次获准展示的片段和来源映射；知识库更新后，历史报告仍能打开当时使用的证据。
- **三方当日即可自测。** 甲用上述响应验证证据入报告，乙用任务和进度回放验证页面，丙直接针对同一请求结构验证真实检索。公共 fixture 由甲保存在 `contracts/travel/v1/fixtures/`；乙的浏览器模拟器与丙的检索测试读取同一份标准样例，各自另存本模块独有的场景数据。

