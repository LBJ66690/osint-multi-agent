// Theme regressions against fixture HTTP/SSE only. Never sends model/search requests.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const output = path.join(root, 'outputs', 'tactical-ui');
const modelConfig = JSON.parse(fs.readFileSync(path.join(root, 'outputs', 'model_profile_options.json'), 'utf8'));
fs.mkdirSync(output, {recursive:true});

(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.OSINT_TEST_BROWSER ? {executablePath:process.env.OSINT_TEST_BROWSER} : {})});
  const page = await browser.newPage({viewport:{width:1600, height:1100}});
  const errors = [], unexpected = [], checks = [], contrast = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    window.__streams = [];
    window.EventSource = class {
      constructor(url) { this.url = url; window.__streams.push(this); setTimeout(() => this.onopen?.(), 0); }
      close() { this.closed = true; }
      send(event) { if (!this.closed) this.onmessage?.({data:JSON.stringify(event)}); }
    };
  });
  const now = Date.now();
  const plan = {goal:'固定测试：多渠道资料研究',subquestions:[{question:'公开资料有哪些？',search_queries:['公开资料']}],report_types:['研究报告']};
  const timeline = {server_ms:now,started_ms:now-3670000,current_stage:'collecting',active_stage:'collecting',ended_ms:null,wait_started_ms:null,wait_total_ms:0,wait_reason:'',stages:{planning:{status:'done',elapsed_ms:8000,active_since_ms:null,label:'规划完成'},collecting:{status:'running',elapsed_ms:3661000,active_since_ms:now,label:'收集公开资料',model:'Parallel + You.com · MCP'}}};
  const detail = {id:'tactical-fixture',task:plan.goal,status:'running',timeline,search_sources:['parallel','you'],reports:[],events:[{type:'plan',plan},{type:'stage',stage:'planning',status:'done',label:'规划完成'},{type:'stage',stage:'collecting',status:'running',label:'收集公开资料',model_label:'Parallel + You.com · MCP'}],summary:{plan}};
  const history = [
    {id:detail.id,task:'这是一个较长的研究任务标题，用于确认历史卡片的两行截断、状态标签和日期均能在窄栏中清晰显示',status:'running',report_count:0,created_at:'2026-11-02T03:00:00+00:00'},
    {id:'finished-fixture',task:'已完成的研究任务 <img src=x onerror=alert(1)>',status:'done',report_count:1,created_at:'2026-11-01T02:00:00+00:00'},
    {id:'failed-fixture',task:'已中断的研究任务',status:'error',report_count:0,created_at:'2026-10-30T02:00:00+00:00'},
  ];
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const json = data => route.fulfill({json:data});
    if (url.origin !== 'http://osint.test') { unexpected.push(url.origin + url.pathname); return route.abort(); }
    if (url.pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static','workspace.html'),'utf8')});
    if (url.pathname.endsWith('.svg')) return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,url.pathname),'utf8')});
    if (url.pathname.startsWith('/static/')) return route.fulfill({contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'static',path.basename(url.pathname)),'utf8')});
    if (url.pathname === '/api/auth/me') return route.fulfill({status:404,json:{detail:'Not Found'}});
    if (url.pathname === '/api/health') return json({has_key:true});
    if (url.pathname === '/api/models') return json(modelConfig);
    if (url.pathname === '/api/model-recommendation') return json({mode:'standard',label:'标准研究',role_models:modelConfig.role_profiles.standard,reasons:modelConfig.profile_reasons.standard});
    if (url.pathname === '/api/runs') return json({runs:history});
    if (url.pathname === '/api/runs/tactical-fixture') return json(detail);
    if (url.pathname === '/favicon.ico') return route.fulfill({status:204});
    unexpected.push(route.request().method() + ' ' + url.pathname);
    return route.fulfill({status:404,body:'fixture route missing'});
  });

  // Composites CSS solid/alpha backgrounds and disabled opacity; gradients are
  // separately inspected visually in the saved screenshots, not claimed as WCAG scans.
  async function checkContrast(selectors) {
    const values = await page.evaluate(selectors => {
      const rgba = value => {
        const nums = value.match(/[\d.]+/g)?.map(Number) || [0,0,0,0];
        return [nums[0]||0,nums[1]||0,nums[2]||0,nums.length>3?nums[3]:1];
      };
      const over = (top, bottom) => top.slice(0,3).map((channel,i) => channel*top[3] + bottom[i]*(1-top[3]));
      const luminance = color => color.map(channel => channel/255).map(channel => channel<=.04045 ? channel/12.92 : ((channel+.055)/1.055)**2.4).reduce((sum,channel,i)=>sum+channel*[.2126,.7152,.0722][i],0);
      return selectors.map(selector => {
        const node = document.querySelector(selector);
        if (!node || !node.getClientRects().length) return {selector,missing:true};
        const ancestors = []; for (let parent=node;parent;parent=parent.parentElement) ancestors.unshift(parent);
        let background = [5,9,18], opacity = 1;
        for (const ancestor of ancestors) {
          const style = getComputedStyle(ancestor), layer = rgba(style.backgroundColor);
          opacity *= Number(style.opacity);
          layer[3] *= Number(style.opacity);
          background = over(layer,background);
        }
        const style = getComputedStyle(node), foreground = rgba(style.color); foreground[3] *= opacity;
        const visibleColor = over(foreground,background), a=luminance(visibleColor),b=luminance(background);
        return {selector,ratio:Number(((Math.max(a,b)+.05)/(Math.min(a,b)+.05)).toFixed(2)),color:style.color,background:background.map(Math.round),opacity};
      });
    },selectors);
    contrast.push(...values);
    for (const value of values) {
      assert.equal(value.missing,undefined,`missing visible control ${value.selector}`);
      assert.ok(value.ratio>=4.5,`text contrast <4.5: ${JSON.stringify(value)}`);
    }
  }

  async function geometry(width,label) {
    await page.setViewportSize({width,height:1000});
    await page.waitForTimeout(60);
    const value = await page.evaluate(() => {
      const rect = selector => { const r=document.querySelector(selector).getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}; };
      const clock=document.querySelector('.hero-clock'),bounds=clock.getBoundingClientRect();
      // The masked rotating scan has an intentionally larger transformed box;
      // assert painted ring geometry and actual text bounds independently.
      const clockOverflow=[...clock.children].filter(node=>!node.classList.contains('clock-orbit')).some(node=>{const r=node.getBoundingClientRect();return r.width>0&&(r.left<bounds.left-1||r.right>bounds.right+1||node.scrollWidth>node.clientWidth+1);});
      return {viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,hero:rect('#live-screen'),clock:rect('.hero-clock'),orbit:rect('.clock-orbit'),time:rect('#stage-time'),label:rect('#clock-label'),clockOverflow};
    });
    checks.push({label,width,...value});
    assert.ok(value.scrollWidth<=width+1,`${label}: page overflow at ${width}: ${value.scrollWidth}`);
    assert.ok(Math.abs(value.clock.width-value.clock.height)<=2,`${label}: clock is not square at ${width}`);
    assert.ok(value.clock.width>=120,`${label}: clock too small at ${width}`);
    for (const boundary of ['left','top']) assert.ok(value.orbit[boundary]>=value.hero[boundary]-1,`${label}: orbit clipped ${boundary} at ${width}`);
    for (const boundary of ['right','bottom']) assert.ok(value.orbit[boundary]<=value.hero[boundary]+1,`${label}: orbit clipped ${boundary} at ${width}`);
    assert.ok(Math.abs((value.orbit.left+value.orbit.right-value.clock.left-value.clock.right)/2)<=2,`${label}: orbit off-center at ${width}`);
    assert.ok(Math.abs((value.time.left+value.time.right-value.clock.left-value.clock.right)/2)<=3,`${label}: time off-center at ${width}`);
    assert.ok(Math.abs((value.label.left+value.label.right-value.clock.left-value.clock.right)/2)<=3,`${label}: clock label off-center at ${width}`);
    assert.ok(value.time.left>=value.clock.left-1&&value.time.right<=value.clock.right+1,`${label}: long time overflows at ${width}`);
    assert.equal(value.clockOverflow,false,`${label}: clock text overflows at ${width}: ${JSON.stringify(value)}`);
  }

  try {
    await page.goto('http://osint.test/');
    await page.locator('#btn-preview-workspace').click();
    await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled && document.querySelectorAll('.history-item').length===3);
    assert.equal(await page.locator('.tactical-hud-bar').count(),1);
    assert.equal(await page.locator('[data-capability-stage]').count(),6);
    assert.equal(await page.locator('#topology-modal:visible').count(),0);
    assert.equal(await page.locator('[data-atlas-node]').count(),0,'topology must stay lazy');
    assert.equal(await page.locator('.history-status').count(),3);
    assert.equal(await page.locator('.history-meta time').count(),3);
    assert.equal(await page.locator('.history-list img').count(),0,'history text must be escaped');
    assert.match(await page.locator('#readiness-list').textContent(),/未探测|尚未探测|未检测|任务.*连接|请求.*连接|以实际检索为准/,'do not label MCP channels as tested before any search');
    assert.doesNotMatch(await page.locator('#readiness-list').textContent(),/本地知识库就绪|免费 MCP 检索通道就绪/);
    await checkContrast(['#health-text','.total-clock','#total-time','#btn-motion','#btn-follow','#btn-focus','#btn-topology','.advanced summary','.chip','#task-input','#execution-mode','#search-strategy','#model-mode','.metric-row > div > span','.metric-row small','.history-task','.history-status','.history-meta time']);
    const inputStyle = await page.locator('#task-input').evaluate(node => {const s=getComputedStyle(node);return {border:s.borderTopWidth,borderStyle:s.borderTopStyle,borderColor:s.borderTopColor,background:s.backgroundColor};});
    assert.ok(parseFloat(inputStyle.border)>=1&&inputStyle.borderStyle!=='none','task input needs a visible border');
    assert.notEqual(inputStyle.background,'rgba(0, 0, 0, 0)','task input needs a surface');
    for (const width of [1920,1600,1366,1220,1024,950,768,680,390,320]) await geometry(width,'idle');
    await page.setViewportSize({width:1600,height:1100});
    await page.locator('.advanced summary').click();
    await checkContrast(['#role-grid select:disabled','#role-grid label small']);
    await page.locator('.advanced summary').click();
    await page.screenshot({path:path.join(output,'idle-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:1000});
    await page.screenshot({path:path.join(output,'idle-mobile.png'),fullPage:true});
    await page.locator('#btn-history').click();
    await page.locator('[data-run="tactical-fixture"]').click();
    await page.waitForFunction(()=>document.querySelector('#live-screen').classList.contains('is-running')&&document.querySelector('#stage-time').textContent.length===8);
    assert.equal(await page.locator('[data-star="collecting"].star-live').count(),1);
    assert.equal(await page.locator('#readiness-list').count(),0,'idle readiness must not describe an existing run');
    for (const width of [1920,1600,1366,1220,1024,950,768,680,390,320]) await geometry(width,'hour-duration');
    const send = event=>page.evaluate(event=>window.__streams.at(-1).send({...event,run_id:'tactical-fixture',ts:new Date().toISOString()}),event);
    await send({type:'tool_call',stage:'collecting',tool:'parallel',call_id:'p1',args:{query:'固定资料'}});
    assert.equal(await page.locator('[data-beam="parallel"].busy').count(),1);
    await send({type:'tool_result',stage:'collecting',tool:'parallel',call_id:'p1',result_count:1,items:[{title:'固定浏览器测试资料',content:'仅用于浏览器布局回归的固定内容。',source:'https://example.com/fixture',provider:'parallel'}]});
    await page.waitForFunction(()=>document.querySelector('#metric-evidence').textContent==='1');
    await page.setViewportSize({width:1600,height:1100});
    await page.screenshot({path:path.join(output,'running-desktop.png'),fullPage:true});
    await page.locator('#btn-motion').click();
    const animations = await page.evaluate(()=>['.clock-orbit span','.hero-grid::after','.orbit-one'].map(selector=>{const pseudo=selector.includes('::')?'::'+selector.split('::')[1]:null;const node=document.querySelector(selector.split('::')[0]);return {selector,name:getComputedStyle(node,pseudo).animationName};}));
    for (const animation of animations) assert.equal(animation.name,'none',`reduced motion: ${animation.selector}`);
    assert.equal(await page.locator('body.reduced-motion').count(),1);
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.locator('#btn-motion').click();
    assert.equal(await page.locator('.orbit-one').evaluate(node=>getComputedStyle(node).animationName),'none','OS reduced-motion must apply independently');
    await page.setViewportSize({width:390,height:1000});
    await page.screenshot({path:path.join(output,'running-mobile.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    assert.deepEqual(unexpected,[],'all network must be mocked, with no model/search requests');
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:true,viewports:checks,contrast,errors,unexpected,network:'all mocked; 0 model or search calls'},null,2));
    console.log('TACTICAL UI PASS: controls/disabled model contrast, 6-stage idle matrix, truthful readiness, history labels/XSS, HUD, square contained centered hour timer across 10 widths, viewport overflow, real-event animation, user/OS reduced motion; 0 paid API calls.');
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
