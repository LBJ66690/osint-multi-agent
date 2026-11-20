# 成员乙 · 贵州文旅前端交接

分支为 `feat/travel-web`，基点为 `4ce6f64d088b832c51cc710c8a7b209e72ee0698`。覆盖乙-1 至乙-4。前端按任务书的文旅 v1 公共接口实现，旧工作台沿用原 API。

## 浏览器复现

安装 Node.js 和 Playwright 后运行以下命令。已有系统 Chromium 时可用 `OSINT_TEST_BROWSER` 指定它。

```sh
OSINT_TEST_BROWSER=/usr/bin/chromium node scripts/ui_travel/run.cjs
```

脚本启动临时本地 HTTP 服务，运行真实 Chromium，将截图写入 `outputs/travel-ui/`。从项目服务进入 `/?demo=travel` 也可手工复现。回放在浏览器内使用固定日期、示例账户及进度，刷新页面会开启新的回放会话。

1. 打开文旅首页，在九市州区划地图点铜仁，或点梵净山实景照片。手机上会打开景点详情。
2. 选「用它规划」，再选「填入梵净山样例」补齐贵阳、1 人、3 天 2 晚和全团 2000 元含往返。
3. 预览并确认。看到接驳查询后切换导游，原草稿和 run 保留。
4. 等服务端状态确认完成，在同一 run 下展开事实来源、查看预约入口、下载报告或追问。也可从旅行记录重开。

自动用例包括九市州区划和照片、键盘选点、底图和图片降级、减少动效、本地中文语音缺失、切换导游、账余额度、模拟订单三种结局、JPEG 导出像素核验及识别纠正、创建重试的幂等键、原生 SSE 重连和重复事件、取消和中断恢复、历史重开。常规工作台回归仍使用现有各浏览器测试。

## 联调交接

- `/` 为文旅首页，`/?workspace=research` 进入既有通用研究工作台。服务模式使用同源账号、HTTP 和 SSE，样例回放使用独立数据。
- 在 `static/travel/config.js` 配置浏览器用的高德 JS Key 和安全代理地址。页面默认展示九市州区划和景点实景照片，配置高德后可手动切换在线地图。配置公开浏览器字段时，服务端凭证由甲在其部署环境维护。
- 甲发布 v1 样例后，用生产响应核对画像字段、`ticket_fields` 纠正路径、商品 SKU、`trip_plan` 子字段与证据引用。前端暂按任务书及本地 fixture 处理这些字段。将服务端 SKU 填入 `demoOrderSku` 后，模拟订单的金额和入账由服务端返回。手机号、账号凭据及原始票据无需提供给前端测试。
- 以同一 `poi_id` 在报告和景点卡检索 `static/travel/data/pois.json`。目录的坐标系和精度在 `coordinates` 中注明。报表媒体通过 `poi_id` 和 `image_id` 关联。入口使用独立 ID，区划地图统一显示九个代表景点。
- 当前通过 HTTP/SSE 测试服务和本地 FastAPI 静态路由验收。真实 MySQL 登录赠额、生产 OCR、实时天气、地图凭证及旅行报告生成由甲在集成分支联调。

## 脱敏截图

- 桌面 [首页](artifacts/desktop-home.png) · [九市州地图](artifacts/desktop-atlas.png) · [切换导游后的进度](artifacts/desktop-guide.png) · [行程报告](artifacts/desktop-report.png)
- 手机 [首页](artifacts/mobile-home.png) · [九市州地图](artifacts/mobile-atlas.png) · [景点详情](artifacts/mobile-poi.png) · [行程报告](artifacts/mobile-report.png)
- [静态素材授权清单](../../static/travel/assets/SOURCES.md)
