// Real browser + existing app/SSE, with all external traffic blocked/mocked.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),output=path.join(root,'outputs/fish-pet-acceptance');
fs.mkdirSync(output,{recursive:true});
const models=JSON.parse(fs.readFileSync(path.join(root,'outputs/model_profile_options.json'),'utf8'));
const sources=JSON.parse(fs.readFileSync(path.join(root,'outputs/search_source_options.json'),'utf8'));

(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.OSINT_TEST_BROWSER?{executablePath:process.env.OSINT_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1920,height:1200},reducedMotion:'no-preference'});
 const errors=[],unexpected=[],mutations=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  window.__streams=[];window.EventSource=class{constructor(){window.__streams.push(this);setTimeout(()=>this.onopen?.(),0);}close(){this.closed=true;}send(e){if(!this.closed)this.onmessage?.({data:JSON.stringify(e)});}};
  window.__spoken=[];window.__voiceJobs=[];window.__cancels=0;
  window.__voices=[{name:'Remote voice excluded',lang:'zh-CN',localService:false}];
  Object.defineProperty(window,'speechSynthesis',{configurable:true,value:{getVoices:()=>window.__voices,cancel:()=>window.__cancels++,speak:job=>{window.__spoken.push(job.text);window.__voiceJobs.push(job);}}});
  window.SpeechSynthesisUtterance=class{constructor(text){this.text=text;}};
 });
 const plan={goal:'宠物浏览器验收（固定数据）',subquestions:[{question:'公开资料',search_queries:['资料']}],report_types:['研究报告']};
 const timeline=(stage,status='running')=>({server_ms:Date.now(),started_ms:Date.now()-20000,ended_ms:status==='done'?Date.now():null,current_stage:stage,active_stage:status==='running'?stage:null,wait_started_ms:null,wait_total_ms:0,wait_reason:'',stages:{[stage]:{status,elapsed_ms:20000,active_since_ms:status==='running'?Date.now():null,label:'固定数据执行阶段'}}});
 const report={type:'研究报告',markdown:'# 宠物测试报告\n\n这是一份固定测试报告。'};
 const run={id:'fish-live',task:plan.goal,status:'running',timeline:timeline('collecting'),search_sources:['parallel'],reports:[],events:[{type:'plan',plan}],summary:{plan}};
 const old={id:'fish-old',task:'另一个已完成任务',status:'done',timeline:timeline('verifying','done'),search_sources:[],reports:[report],events:[],summary:{}};
 const routeHandler=route=>{
  const request=route.request(),u=new URL(request.url()),json=data=>route.fulfill({json:data});
  if(request.method()!=='GET')mutations.push(request.method()+' '+u.pathname);
  if(u.origin!=='http://fish.test'){unexpected.push(u.href);return route.abort();}
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/workspace.html'),'utf8')});
  if(u.pathname.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,u.pathname),'utf8')});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:u.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'static',path.basename(u.pathname)),'utf8')});
  if (u.pathname === '/api/auth/me') return route.fulfill({status:404,json:{detail:'Not Found'}});
  if(u.pathname==='/api/health')return json({has_key:true});
  if(u.pathname==='/api/models')return json(models);
  if(u.pathname==='/api/model-recommendation')return json({mode:'standard',label:'标准研究',role_models:models.role_profiles.standard,reasons:{}});
  if(u.pathname==='/api/runs')return json({runs:[run,old].map(r=>({...r,report_count:r.reports.length,created_at:'2026-11-02T04:00:00Z'}))});
  if(u.pathname==='/api/runs/fish-live')return json(run);
  if(u.pathname==='/api/runs/fish-old')return json(old);
  if(u.pathname==='/favicon.ico')return route.fulfill({status:204});
  unexpected.push(u.pathname);return route.fulfill({status:404,body:''});
 };
 await page.route('**/*',routeHandler);
 const send=event=>page.evaluate(e=>window.__streams.at(-1).send({...e,run_id:'fish-live',ts:new Date().toISOString()}),event);
 const bubble=()=>page.locator('#fish-bubble').textContent();
 const spoken=()=>page.evaluate(()=>window.__spoken.length);
 try{
  await page.goto('http://fish.test/');await page.locator('#btn-preview-workspace').click();await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled&&document.querySelectorAll('.history-item').length===2);
  assert.equal(await page.locator('#fish-collapse').getAttribute('aria-expanded'),'true');
  assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'idle');
  assert.equal(await spoken(),0,'voice must be opt-in');
  await page.locator('#fish-stroke').click();assert.equal(await page.locator('#task-input:focus').count(),1);
  await page.locator('#fish-stroke').press('Enter');assert.equal(await page.locator('#task-input:focus').count(),1);
  assert.match(await bubble(),/小岚/);assert.equal(await page.locator('#fish-affection').count(),0);
  await page.locator('#fish-voice').click();assert.match(await page.locator('#fish-note').textContent(),/未找到本地中文语音/);assert.equal(await spoken(),0,'remote voices must never be used');
  await page.evaluate(()=>window.__voices.push({name:'Fixture local Chinese voice',lang:'zh-CN',localService:true}));
  await page.locator('#fish-voice').click();assert.equal(await page.locator('#fish-voice').getAttribute('aria-pressed'),'true');assert.equal(await spoken(),1);
  await page.locator('[data-run="fish-live"]').click();
  await page.waitForFunction(()=>document.querySelector('#research-pet').dataset.mood==='searching');
  assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'searching');
  assert.match(await bubble(),/收集官/);assert.equal(await spoken(),1,'switching runs must not replay history aloud');
  await page.locator('[data-stage="planning"]').click();assert.match(await page.locator('#view-title').textContent(),/规划官/);assert.equal(await page.locator('#fish-status').textContent(),'收集中','narrate execution, not manually viewed stage');
  await send({type:'tool_result',stage:'collecting',tool:'parallel',call_id:'test-result',result_count:3,items:[]});
  await page.waitForFunction(()=>document.querySelector('#fish-bubble').textContent.includes('3 条网页资料'));
  assert.equal(await spoken(),2);
  await send({type:'tool_result',stage:'collecting',tool:'parallel',call_id:'test-result',result_count:3,items:[]});await page.waitForTimeout(100);assert.equal(await spoken(),2,'same result must not be spoken twice');
  await page.locator('#research-pet').screenshot({path:path.join(output,'pet-searching.png')});
  await send({type:'tool_result',stage:'collecting',tool:'you',call_id:'queued-old',result_count:2});
  await send({type:'stage',stage:'reasoning',status:'running',timeline:timeline('reasoning')});
  await page.waitForTimeout(2350);assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'thinking');assert.match(await bubble(),/推理官/);assert.doesNotMatch(await bubble(),/2 条网页资料/,'stale queued collection notice must be dropped');
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('osint:effect',{detail:{type:'tool_result',stage:'reasoning',tool:'<img src=x onerror=alert(1)>',run_id:'another-run',result_count:999}})));
  assert.match(await bubble(),/推理官/);assert.equal(await page.locator('#fish-bubble img').count(),0);
  await page.locator('#fish-announcements').click();assert.match(await bubble(),/暂停/);
  const muted=await spoken();await send({type:'stage',stage:'reporting',status:'running',timeline:timeline('reporting')});
  assert.equal(await spoken(),muted);assert.equal(await page.locator('#fish-status').textContent(),'写报告');assert.match(await bubble(),/暂停/);
  await page.locator('#fish-announcements').click();assert.match(await bubble(),/报告官/);
  await page.locator('#research-pet').screenshot({path:path.join(output,'pet-writing.png')});
  await send({type:'sources_required',stage:'collecting',options:sources,request_id:'manual-sources',selected:[],timeline:timeline('collecting')});
  await page.locator('#sources-modal').waitFor({state:'visible'});assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'waiting');
  assert.match(await bubble(),/搜索通道等你选择/);
  await page.locator('#btn-close-sources').click();await page.locator('#fish-go').click();await page.locator('#sources-modal').waitFor({state:'visible'});await page.locator('#btn-close-sources').click();
  await send({type:'sources_selected',selected:['parallel'],timeline:timeline('collecting')});
  await send({type:'error',stage:'collecting',message:'固定测试中断',can_resume:true,timeline:timeline('collecting','error')});
  assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'worried');
  await page.locator('#fish-stroke').click();assert.match(await bubble(),/错误提示/,'pet interaction must not hide important interruption notice');
  await page.locator('#fish-go').click();assert.equal(await page.locator('#error-banner:visible').count(),1);
  await send({type:'resumed',timeline:timeline('reasoning')});assert.match(await bubble(),/断点/);
  await send({type:'report',stage:'reporting',report_type:report.type,markdown:report.markdown});
  await send({type:'done',timeline:timeline('verifying','done')});
  assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'celebrate');assert.match(await bubble(),/报告送到/);
  await page.locator('#fish-go').click();assert.equal(await page.locator('body.report-reading').count(),1);assert.match(await page.locator('#stage-content').textContent(),/宠物测试报告/);
  await page.locator('#research-pet').screenshot({path:path.join(output,'pet-complete.png')});
  const beforeSwitch=await spoken();await page.locator('[data-run="fish-old"]').click();await page.waitForFunction(()=>document.querySelector('#task-subtitle').textContent.includes('fish-old'));assert.equal(await spoken(),beforeSwitch);
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('osint:effect',{detail:{type:'resumed',run_id:'fish-live'}})));assert.match(await bubble(),/报告送到/);
  await page.locator('#fish-collapse').click();assert.equal(await page.locator('#fish-details:visible').count(),0);
  await page.reload();await page.locator('#btn-preview-workspace').click();await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled);
  assert.equal(await page.locator('#fish-details:visible').count(),0,'collapsed preference persists');assert.equal(await page.locator('#fish-voice').getAttribute('aria-pressed'),'false','voice never auto-enables after refresh');assert.equal(await spoken(),0);
  await page.locator('#fish-collapse').click();
  await page.locator('#fish-announcements').click();await page.reload();await page.locator('#btn-preview-workspace').click();await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled);
  assert.equal(await page.locator('#fish-announcements').getAttribute('aria-pressed'),'false','quiet preference persists');await page.locator('#fish-announcements').click();
  for(const width of [1920,1600,1366,1024,950,768,390,320]){
   await page.setViewportSize({width,height:1000});await page.waitForTimeout(40);
   const geometry=await page.evaluate(()=>{const p=document.querySelector('#research-pet').getBoundingClientRect(),n=document.querySelector('.stage-sidebar').getBoundingClientRect();return {scroll:document.documentElement.scrollWidth,width:innerWidth,pet:{x:p.x,right:p.right,y:p.y},nav:{bottom:n.bottom},petOverflow:document.querySelector('#research-pet').scrollWidth>document.querySelector('#research-pet').clientWidth+1};});
   checks.push(geometry);assert.ok(geometry.scroll<=width+1,`page overflow ${width}`);assert.ok(geometry.pet.x>=0&&geometry.pet.right<=width+1,`pet outside viewport ${width}`);assert.ok(geometry.pet.y>=geometry.nav.bottom-1,`pet overlaps stage controls ${width}`);assert.equal(geometry.petOverflow,false,`pet content overflow ${width}`);
  }
  await page.setViewportSize({width:1920,height:1200});await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(output,'pet-desktop.png'),fullPage:true});
  await page.locator('#btn-motion').click();assert.equal(await page.locator('.fish-float').evaluate(e=>getComputedStyle(e).animationName),'none');
  await page.locator('#btn-motion').click();await page.emulateMedia({reducedMotion:'reduce'});assert.equal(await page.locator('.fish-float').evaluate(e=>getComputedStyle(e).animationName),'none');await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(()=>document.body.classList.add('page-hidden'));assert.equal(await page.locator('.fish-float').evaluate(e=>getComputedStyle(e).animationPlayState),'paused');await page.evaluate(()=>document.body.classList.remove('page-hidden'));
  await page.setViewportSize({width:390,height:844});await page.locator('#research-pet').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,'pet-mobile.png'),fullPage:true});
  const mobileContext=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  try{
   const mobile=await mobileContext.newPage();mobile.on('pageerror',e=>errors.push(e.message));await mobile.route('**/*',routeHandler);
   await mobile.goto('http://fish.test/');await mobile.waitForFunction(()=>!document.querySelector('#btn-run').disabled);
   assert.equal(await mobile.locator('#fish-details:visible').count(),1,'welcome always shows the greeting guide');
   await mobile.locator('#btn-preview-workspace').tap();assert.equal(await mobile.locator('#fish-details:visible').count(),0,'mobile dashboard defaults to compact');
   await mobile.locator('#fish-collapse').tap();await mobile.locator('#fish-stroke').tap();assert.equal(await mobile.locator('#task-input:focus').count(),1);
   await mobile.setViewportSize({width:320,height:568});assert.equal(await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  }finally{await mobileContext.close();}
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);assert.deepEqual(mutations,[],'pet controls never submit task/model/search requests');
  fs.writeFileSync(path.join(output,'ui_results.json'),JSON.stringify({passed:true,checks,errors,unexpected,mutations,model_calls:0,external_search_calls:0,voice:'mocked local voice; remote voice excluded; real system availability varies'},null,2));
  console.log('RESEARCH GUIDE UI PASS: click/keyboard, live execution vs viewed stage, coalescing/stale updates/dedup/isolation, quiet/collapse preferences, opt-in local voice/fallback, pending/report links, errors/resume/completion, mobile/desktop non-overlap, reduced motion; 0 model/search or task mutations.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
