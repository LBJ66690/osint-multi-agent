// 真实 HTTP/SSE 后端联调。外部服务由 serve_acceptance_fixture.py 固定数据替代。
const {chromium}=require('playwright'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.OSINT_TEST_BROWSER});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.goto('http://127.0.0.1:8890');await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled);
  await page.locator('#execution-mode').selectOption('deep');await page.locator('#task-input').fill('完整浏览器联调验收');await page.locator('#btn-run').click();
  await page.locator('#confirm-modal').waitFor({state:'visible'});await page.locator('#confirm-feedback').fill('请保持固定测试范围');await page.locator('#btn-confirm-edit').click();
  await page.waitForTimeout(350);await page.locator('#confirm-modal').waitFor({state:'visible'});await page.locator('#btn-confirm-ok').click();
  await page.locator('#sources-modal').waitFor({state:'visible'});await page.locator('#sources-options input[value="parallel"]').check();await page.locator('#btn-sources-start').click();
  await page.locator('#gate-modal').waitFor({state:'visible'});assert.match(await page.locator('#gate-body').textContent(),/测试重写关卡/);await page.locator('#btn-gate-redo').click();
  await page.locator('#report-ready').waitFor({state:'visible'});assert.match(await page.locator('#stage-content').textContent(),/浏览器联调验收报告/);
  const downloaded=page.waitForEvent('download');await page.locator('#btn-download-final').click();assert.match((await downloaded).suggestedFilename(),/\.md$/);
  await page.reload();await page.locator('.history-item').first().click();await page.locator('#report-ready').waitFor({state:'visible'});
  await page.screenshot({path:path.resolve(__dirname,'../outputs/acceptance-real-web.png'),fullPage:true});
  await page.locator('#search-strategy').selectOption('adaptive');await page.locator('#task-input').fill('取消与恢复联调验收');await page.locator('#btn-run').click();await page.locator('#confirm-modal').waitFor({state:'visible'});
  await page.locator('[data-close="confirm-modal"]').click();await page.locator('#btn-cancel').click();await page.locator('#btn-resume').waitFor({state:'visible'});
  await page.waitForTimeout(200);await page.locator('#btn-resume').click();await page.locator('#confirm-modal').waitFor({state:'visible'});await page.locator('#btn-confirm-ok').click();
  await page.locator('#sources-modal').waitFor({state:'visible'});await page.locator('#sources-options input[value="parallel"]').check();await page.locator('#btn-sources-start').click();
  await page.locator('#report-ready').waitFor({state:'visible'});await page.locator('#adaptive-decisions').waitFor({state:'visible'});assert.match(await page.locator('#adaptive-decision-list').textContent(),/补搜|搜索/);await page.screenshot({path:path.resolve(__dirname,'../outputs/comparison/adaptive-real-web.png'),fullPage:true});assert.deepEqual(errors,[]);
  const runDetail=await page.evaluate(async()=>{const runs=await(await fetch('/api/runs')).json();return(await(await fetch('/api/runs/'+runs.runs[0].id)).json());});
  await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  await page.locator('#report-chat-model').selectOption('kimi-k3');await page.locator('#report-chat-question').fill('这份报告的依据是什么？');await page.locator('#report-chat-send').click();
  await page.locator('.chat-assistant:not(.chat-waiting)').waitFor({state:'visible'});assert.match(await page.locator('#report-chat-messages').textContent(),/kimi-k3/);
  await page.locator('#report-chat-close').click();await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);assert.equal(await page.locator('.chat-user').count(),1);
  await page.locator('#report-chat-close').click();
  const unchanged=await page.evaluate(async rid=>(await(await fetch('/api/runs/'+rid)).json()),runDetail.id);
  assert.equal(unchanged.status,'done');assert.deepEqual(unchanged.reports,runDetail.reports);assert.equal(unchanged.events.length,runDetail.events.length);
  assert.deepEqual(errors,[]);
  console.log('REAL WEB PASS: report Q&A selected model/history and research isolation; adaptive create/cancel/resume/decisions, real HTTP/SSE, edit/confirm plan, select sources, report redo gate, report/download/history refresh, cancel and resume to completion');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
