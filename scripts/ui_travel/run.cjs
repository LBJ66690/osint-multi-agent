const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const output = process.env.OSINT_TRAVEL_ARTIFACTS || path.join(root, 'outputs/travel-ui');
const mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.webp': 'image/webp', '.png': 'image/png'};
const sendJSON = (res, data, status = 200) => res.writeHead(status, {'Content-Type': 'application/json'}).end(JSON.stringify(data));

async function requestBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks);
}

function mockRuntime() {
  const runs = [];
  const connections = [];
  const recorded = [];
  let uploads;
  let capturedOverrides;
  let status = 'running';
  let weather = {location: '贵阳', kind: 'forecast', condition: '小雨', as_of: '2026-11-19T09:55:00+08:00', stale: false};
  const event = {type: 'tool_result', run_id: 'live-fixture', stage: 'collecting', tool: 'rag.retrieve',
    call_id: 'q1', message: '已取回一条梵净山东门的游览经验。'};
  const detail = () => ({id: 'live-fixture', domain: 'guizhou_travel', task: '梵净山恢复测试', status, events: [event],
    ...(status === 'done' ? {reports: [{type: '旅行报告'}], evidence: [],
      trip_plan: {outcome: 'ready', days: [{date: '2026-11-21', title: '已恢复的行程'}], budget: {known_lower_fen: 50000},
        weather_alternatives: [], media: [{poi_id: 'fanjingshan', image_id: 'fanjingshan-photo'}],
        booking_entries: [{poi_id: 'fanjingshan-east'}], evidence_ids: [], unresolved: []}} : {})});
  function sse(res, id, data) {
    res.write((id === null ? '' : 'id: ' + id + '\n') + 'data: ' + JSON.stringify(data) + '\n\n');
  }
  async function handle(req, res, url) {
    const key = req.method + ' ' + url.pathname;
    recorded.push(key);
    switch (key) {
      case 'GET /api/auth/me': return sendJSON(res, {username: '本地测试游客'});
      case 'GET /api/travel/profile': return sendJSON(res, {origin: '贵阳', travelers: 1, budget_scope: 'group'});
      case 'GET /api/travel/credits': return sendJSON(res, {available: 1, reserved: 0, used: 0});
      case 'GET /api/travel/home-weather': return sendJSON(res, weather);
      case 'GET /api/runs': return sendJSON(res, {runs: []});
      case 'POST /api/travel/attachments': {
        const multipart = await requestBody(req);
        assert.match(req.headers['content-type'], /^multipart\/form-data/);
        assert.match(multipart.toString('latin1'), /filename="redacted-ticket.jpg"/);
        const header = multipart.indexOf('\r\n\r\n') + 4;
        uploads = multipart.subarray(header, multipart.lastIndexOf('\r\n--'));
        return sendJSON(res, {attachment_id: 'ticket-test', suggested_fields: {
          date: '2026-11-20', arrival_time: '10:30', arrival_station: '铜仁站', train_number: 'G123',
        }});
      }
      case 'POST /api/travel/drafts/preview': {
        const body = JSON.parse(await requestBody(req));
        capturedOverrides = body;
        return sendJSON(res, {trip: body.overrides, missing_fields: []});
      }
      case 'POST /api/runs': {
        runs.push({body: JSON.parse(await requestBody(req)), key: req.headers['idempotency-key']});
        if (runs.length === 1) return sendJSON(res, {detail: '固定样例：创建回包丢失，请重试'}, 503);
        return sendJSON(res, {run_id: 'live-fixture', model_id: 'fixture'});
      }
      case 'GET /api/runs/live-fixture': return sendJSON(res, detail());
      case 'GET /api/runs/live-fixture/events': {
        connections.push({last: req.headers['last-event-id'], after: url.searchParams.get('after')});
        res.writeHead(200, {'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache'});
        res.write('retry: 100\n\n');
        if (connections.length === 1) {
          sse(res, 0, event);
        } else if (connections.length === 2) {
          sse(res, 0, event);
          sse(res, 1, event);
          sse(res, 2, {type: 'tool_error', run_id: 'live-fixture', tool: 'weather.forecast',
            call_id: 'failed-weather', message: '江口预报待查。'});
          sse(res, 3, {type: 'error', run_id: 'live-fixture', message: '固定样例中断'});
          status = 'error';
          sse(res, null, {type: 'stream_end'});
        } else {
          sse(res, 4, {type: 'stage_start', run_id: 'live-fixture', stage: 'verifying'});
          sse(res, 5, {type: 'done', run_id: 'live-fixture'});
          status = 'done';
          sse(res, null, {type: 'stream_end'});
        }
        return res.end();
      }
      case 'POST /api/runs/live-fixture/resume':
        status = 'running';
        return sendJSON(res, {ok: true});
      default:
        return sendJSON(res, {detail: '未配置的测试接口'}, 404);
    }
  }
  return {handle, runs, connections, recorded, get upload() { return uploads; }, get preview() { return capturedOverrides; },
    stale() { weather = {...weather, stale: true, kind: 'observation'}; }};
}

async function serve(runtime) {
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname.startsWith('/api/')) return await runtime.handle(req, res, url);
      if (url.pathname === '/favicon.ico') return res.writeHead(204).end();
      const file = url.pathname === '/' ? '/static/index.html' : url.pathname;
      if (!file.startsWith('/static/')) return res.writeHead(404).end();
      const buffer = await fs.readFile(path.join(root, file));
      res.writeHead(200, {'Content-Type': mime[path.extname(file)] || 'application/octet-stream'}).end(buffer);
    } catch (error) {
      if (!res.headersSent) res.writeHead(error.code === 'ENOENT' ? 404 : 500);
      res.end();
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return {server, url: 'http://127.0.0.1:' + server.address().port};
}

async function waitText(page, selector, text) {
  await page.waitForFunction(([sel, expected]) => document.querySelector(sel)?.textContent.includes(expected), [selector, text]);
}
async function waitWidth(page) {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'no horizontal page overflow');
}
async function startSample(page) {
  await page.locator('#example').click();
  await page.locator('#preview').click();
  await page.getByRole('button', {name: '确认并开始 →'}).click();
}
async function screenshot(page, name, selector) {
  if (selector) await page.locator(selector).screenshot({path: path.join(output, name), animations: 'disabled'});
  else await page.screenshot({path: path.join(output, name), animations: 'disabled'});
}

async function waitForAtlas(page) {
  await page.waitForFunction(() => document.querySelectorAll('#map-points .pin[style]').length === 9);
  await page.locator('#poi-list img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
}

async function demoFlow(browser, url, errors) {
  const page = await browser.newPage({viewport: {width: 1400, height: 1200}, acceptDownloads: true});
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.travelEffects = [];
    document.addEventListener('osint:effect', event => window.travelEffects.push(event.detail));
    Object.defineProperty(window, 'speechSynthesis', {configurable: true, value: {getVoices: () => [], cancel() {}}});
  });
  await page.goto(url + '/?demo=travel');
  await waitText(page, '#poi-title', '梵净山');
  await waitForAtlas(page);
  await screenshot(page, 'desktop-home.png');
  assert.equal(await page.locator('#poi-list button').count(), 9);
  assert.equal(await page.locator('#region-labels .region-name').count(), 9);
  assert.equal(await page.locator('#poi-list img').count(), 9);
  assert.equal(await page.locator('#switch-map').isVisible(), false);
  await screenshot(page, 'desktop-atlas.png', '#map-atlas');
  const bijie = await page.locator('#region-labels .region-name').filter({hasText: /^毕节$/}).boundingBox();
  await page.mouse.click(bijie.x + bijie.width / 2, bijie.y + bijie.height / 2);
  await waitText(page, '#poi-title', '织金洞');
  assert.equal(await page.locator('#poi-list [data-poi="zhijin-cave"]').getAttribute('aria-pressed'), 'true');
  await page.locator('#map-points [data-poi="qingyan"]').press('Enter');
  await waitText(page, '#poi-title', '青岩古镇');
  assert.equal(await page.locator('#task').inputValue(), '');
  await page.locator('#poi-list [data-poi="fanjingshan"]').click();
  const booking = page.locator('#poi-card').getByRole('link', {name: /查看官方预约方式/});
  assert.match(await booking.getAttribute('href'), /fanjingshan\.gz\.cn/);
  await page.locator('#poi-card').getByRole('button', {name: /用它规划/}).click();
  assert.match(await page.locator('#task').inputValue(), /梵净山/);
  await waitText(page, '#credits', '可用 1 次');
  await page.locator('#open-profile').click();
  await page.getByRole('button', {name: '跳过'}).click();
  await page.locator('#voice').click();
  await waitText(page, '#voice', '本地中文语音不可用');
  await page.waitForFunction(() => document.querySelector('#guide').dataset.action === 'umbrella');
  await page.locator('#motion').click();
  assert.equal(await page.locator('#motion').getAttribute('aria-pressed'), 'true');
  await startSample(page);
  assert.equal(await page.locator('#activity').count(), 0);
  const runURL = page.url();
  const draft = await page.locator('#task').inputValue();
  await page.locator('#guide-select').selectOption('ahe');
  assert.equal(page.url(), runURL);
  assert.equal(await page.locator('#task').inputValue(), draft);
  await screenshot(page, 'desktop-guide.png', '#progress');
  await waitText(page, '#run-status', '已完成');
  await page.locator('#view-report').click();
  assert.equal(await page.locator('#report-mode').textContent(), '样例回放');
  assert.equal(await page.locator('#guide').getAttribute('data-action'), 'itinerary');
  assert.match(await page.locator('#report-body').textContent(), /江口县|1,890/);
  await page.locator('#report-body').getByText('查看依据与待确认事项', {exact: true}).last().click();
  await page.locator('#report-body').getByRole('link', {name: /E1.*梵净山/}).last().waitFor();
  assert.match(await page.locator('#report-body').getByRole('link', {name: /查看官方预约方式/}).getAttribute('href'), /fanjingshan\.gz\.cn/);
  await screenshot(page, 'desktop-report.png', '#report');
  assert.equal(await page.locator('#download').isVisible(), false);
  await page.locator('#open-chat').click();
  await page.locator('#question').fill('从哪里预约？');
  await page.locator('#ask').click();
  await waitText(page, '#chat-turns', '样例回答');
  await page.locator('#chat-dialog [data-close]').click();
  await page.goBack();
  await page.locator('#history-list button').first().click();
  await waitText(page, '#run-status', '已完成');
  await waitText(page, '#credits', '已用 1 次');
  assert.equal(await page.evaluate(() => window.travelEffects.filter(event => event.call_id === 'q1').length), 1);
  await startSample(page);
  await waitText(page, '#draft-error', '可用额度不足');
  await page.locator('#open-order').click();
  for (const [result, label] of [['failed', '失败'], ['cancelled', '已取消'], ['success', '成功']]) {
    await page.locator('#create-order').click();
    await page.locator(`[data-outcome="${result}"]`).click();
    await waitText(page, '#order-info', label);
  }
  await page.locator('#order-dialog [data-close]').click();
  await waitText(page, '#credits', '可用 1 次');
  await startSample(page);
  await page.locator('#cancel').click();
  await waitText(page, '#run-status', '已取消');
  await page.locator('#resume').click();
  await waitText(page, '#run-status', '已完成');
  await waitWidth(page);
  await page.close();
}

async function mobileFlow(browser, url, errors) {
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, reducedMotion: 'reduce'});
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(url + '/?demo=travel');
  await waitText(page, '#poi-title', '梵净山');
  await waitForAtlas(page);
  await screenshot(page, 'mobile-home.png');
  await screenshot(page, 'mobile-atlas.png', '#map-atlas');
  await page.route('**/travel/assets/qingyan.webp', route => route.abort());
  await page.reload();
  await page.locator('#poi-list [data-poi="qingyan"]').getByText('实景照片暂不可用').waitFor();
  await page.locator('#map-points [data-poi="qingyan"]').tap();
  await waitText(page, '#mobile-poi', '实景图暂不可用');
  await page.locator('#poi-dialog [data-close]').tap();
  await page.locator('#poi-list [data-poi="fanjingshan"]').tap();
  await page.locator('#mobile-poi img').evaluate(image => image.decode());
  await screenshot(page, 'mobile-poi.png', '#poi-dialog');
  await page.locator('#mobile-poi').getByRole('button', {name: /用它规划/}).tap();
  assert.equal(await page.locator('#poi-dialog').evaluate(node => node.open), false);
  await startSample(page);
  await page.locator('#guide-select').selectOption('xiaolan');
  await waitText(page, '#run-status', '已完成');
  await page.locator('#view-report').click();
  await screenshot(page, 'mobile-report.png', '#report');
  await waitWidth(page);
  await page.close();
}
async function liveFixtureFlow(browser, url, runtime, errors) {
  const page = await browser.newPage({viewport: {width: 1320, height: 940}});
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.travelEffects = [];
    document.addEventListener('osint:effect', event => window.travelEffects.push(event.detail));
  });
  await page.route('**/static/travel/config.js', route => route.fulfill({contentType: 'text/javascript',
    body: "export const travelConfig={amapKey:'test-disabled-key',amapServiceHost:'',demoOrderSku:''};"}));
  await page.route('https://webapi.amap.com/**', route => route.abort());
  await page.goto(url);
  await waitText(page, '#weather-time', '建议带伞');
  await page.locator('#switch-map').click();
  await waitText(page, '#map-mode', '底图不可用');
  assert.notEqual(await page.locator('#guide').getAttribute('data-action'), 'umbrella');
  await page.locator('#open-ticket').click();
  const image = await page.evaluate(() => {
    const c = document.createElement('canvas');
    c.width = 120;
    c.height = 100;
    const ctx = c.getContext('2d');
    ctx.fillStyle = 'white';
    ctx.fillRect(0, 0, 120, 100);
    ctx.fillStyle = 'red';
    ctx.fillRect(0, 0, 35, 35);
    return c.toDataURL('image/png').split(',')[1];
  });
  await page.locator('#file').setInputFiles({name: 'synthetic.png', mimeType: 'image/png', buffer: Buffer.from(image, 'base64')});
  await page.locator('#ticket-tools summary').click();
  await page.locator('#rect-w').fill('80');
  await page.locator('#rect-h').fill('80');
  await page.locator('#apply-rect').click();
  await page.locator('#mask').click();
  await page.locator('#rect-w').fill('50');
  await page.locator('#rect-h').fill('50');
  await page.locator('#apply-rect').click();
  await page.locator('#confirm-mask').check();
  await page.locator('#upload').click();
  await waitText(page, '#ticket-feedback', '核对识别内容');
  const exported = await page.evaluate(async base64 => {
    const image = await createImageBitmap(await (await fetch('data:image/jpeg;base64,' + base64)).blob());
    const canvas = document.createElement('canvas');
    canvas.width = image.width;
    canvas.height = image.height;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(image, 0, 0);
    return {width: image.width, height: image.height, pixel: [...ctx.getImageData(15, 15, 1, 1).data]};
  }, runtime.upload.toString('base64'));
  assert.equal(exported.width, 96);
  assert.equal(exported.height, 80);
  assert.ok(exported.pixel.slice(0, 3).every(channel => channel < 35), 'sensitive pixels must be permanently covered');
  await page.locator('#ticket-fields [name="arrival_time"]').fill('14:20');
  await page.getByRole('button', {name: '确认票面信息'}).click();
  await startSample(page);
  await waitText(page, '#draft-error', '请重试');
  assert.equal(runtime.preview.overrides.ticket_fields.arrival_time, '14:20');
  assert.deepEqual(runtime.preview.attachment_ids, ['ticket-test']);
  await page.getByRole('button', {name: '确认并开始 →'}).click();
  await waitText(page, '#run-status', '运行中断');
  assert.deepEqual(runtime.runs[0], runtime.runs[1]);
  assert.match(runtime.runs[0].key, /\w+/);
  assert.equal(runtime.runs[0].body.trip.budget_fen, 200000);
  assert.deepEqual(runtime.connections.slice(0, 2), [{last: undefined, after: '-1'}, {last: '0', after: '-1'}]);
  assert.equal(await page.evaluate(() => window.travelEffects.filter(event => event.call_id === 'q1').length), 1);
  assert.equal(await page.locator('#activity').count(), 0);
  await page.locator('#resume').click();
  await waitText(page, '#run-status', '已完成');
  await page.locator('#view-report').click();
  await waitText(page, '#report-body', '已恢复的行程');
  await waitText(page, '#report-body', '上限待查');
  assert.equal(await page.locator('#download').isVisible(), false);
  await page.goBack();
  const stableRun = page.url();
  runtime.stale();
  await page.reload();
  assert.equal(page.url(), stableRun);
  await waitText(page, '#weather', '天气待查');
  await waitText(page, '#run-status', '已完成');
  assert.equal(await page.evaluate(() => window.travelEffects.length), 0, 'stored task does not replay speech or animations');
  // Older servers serve the research HTML for links that contain both parameters.
  await page.goto(url + '/static/workspace.html?workspace=research&travel_report=live-fixture');
  await waitText(page, '#report-body', '已恢复的行程');
  assert.equal(new URL(page.url()).search, '?travel_report=live-fixture');
  await page.close();
}

async function main() {
  await fs.mkdir(output, {recursive: true});
  const runtime = mockRuntime();
  const {server, url} = await serve(runtime);
  const errors = [];
  let browser;
  try {
    browser = await chromium.launch({headless: true,
      ...(process.env.OSINT_TEST_BROWSER ? {executablePath: process.env.OSINT_TEST_BROWSER} : {})});
    await demoFlow(browser, url, errors);
    assert.deepEqual(runtime.recorded, [], 'demo must not use live account, run or model APIs');
    await mobileFlow(browser, url, errors);
    assert.deepEqual(runtime.recorded, [], 'mobile demo must remain isolated');
    await liveFixtureFlow(browser, url, runtime, errors);
    const atlasFallback = await browser.newPage();
    atlasFallback.on('pageerror', error => errors.push(error.message));
    await atlasFallback.route('**/travel/data/guizhou-prefectures.json', route => route.abort());
    await atlasFallback.goto(url + '/?demo=travel');
    await atlasFallback.locator('#atlas-error').waitFor();
    await atlasFallback.locator('#poi-list [data-poi="huangguoshu"]').click();
    await waitText(atlasFallback, '#poi-title', '黄果树瀑布');
    await atlasFallback.close();
    const workbench = await browser.newPage();
    await workbench.goto(url + '/?workspace=research');
    await workbench.locator('#welcome-screen').waitFor();
    assert.match(workbench.url(), /static\/workspace\.html/);
    await workbench.close();
    assert.deepEqual(errors, []);
    console.log('TRAVEL UI PASS: desktop + mobile playback, fallback map/image, keyboard, guide switch, report navigation and chat, credits/orders, redaction/correction, idempotency, native SSE reconnect/dedup, failure/resume, historical reopen.');
  } finally {
    await browser?.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
