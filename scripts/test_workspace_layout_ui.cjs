// Welcome -> dashboard integration in a real browser. Every request is a fixture;
// no cloud model/search credentials, calls, or mutations outside fake create.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),out=path.join(root,'outputs/workspace-layout-acceptance');
fs.mkdirSync(out,{recursive:true});
const models=JSON.parse(fs.readFileSync(path.join(root,'outputs/model_profile_options.json'),'utf8'));
const sources=JSON.parse(fs.readFileSync(path.join(root,'outputs/search_source_options.json'),'utf8'));

(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.OSINT_TEST_BROWSER?{executablePath:process.env.OSINT_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1920,height:1100},reducedMotion:'no-preference'});
 const errors=[],unexpected=[],creates=[],mutations=[],geometry=[];
 let failNext=false,holdCreate=false,releaseCreate;
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  window.__streams=[];
  window.EventSource=class{constructor(url){this.url=url;window.__streams.push(this);setTimeout(()=>this.onopen?.(),0);}close(){this.closed=true;}send(event){if(!this.closed)this.onmessage?.({data:JSON.stringify(event)});}};
 });
 const plan={goal:'布局联调固定研究',subquestions:[{question:'公开资料有哪些？',search_queries:['公开资料']}],report_types:['研究报告']};
 const report={type:'研究报告',markdown:'# 布局测试最终报告\n\n这是一份浏览器固定测试报告。'};
 const timeline=(stage,status='running')=>({server_ms:Date.now(),started_ms:Date.now()-20000,ended_ms:status==='done'?Date.now():null,current_stage:stage,active_stage:status==='running'?stage:null,wait_started_ms:null,wait_total_ms:0,wait_reason:'',stages:{[stage]:{status,elapsed_ms:20000,active_since_ms:null,label:'固定测试阶段'}}});
 const pending={request_id:'fixture-sources',options:sources,selected:[],goal:plan.goal};
 const history={
  'layout-complete':{id:'layout-complete',task:'已完成的历史研究',status:'done',reports:[report],events:[],summary:{},timeline:timeline('verifying','done')},
  'layout-waiting':{id:'layout-waiting',task:'等待选择通道的历史研究',status:'awaiting_sources',reports:[],events:[{type:'plan',plan}],summary:{plan},pending_sources:pending,timeline:timeline('collecting')}
 };
 await page.route('**/*',async route=>{
  const request=route.request(),u=new URL(request.url()),json=(data,status=200)=>route.fulfill({status,json:data});
  if(request.method()!=='GET')mutations.push(request.method()+' '+u.pathname);
  if(u.origin!=='http://layout.test'){unexpected.push(u.href);return route.abort();}
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/workspace.html'),'utf8')});
  if(u.pathname.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,u.pathname),'utf8')});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:u.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'static',path.basename(u.pathname)),'utf8')});
  if (u.pathname === '/api/auth/me') return route.fulfill({status:404,json:{detail:'Not Found'}});
  if(u.pathname==='/api/health')return json({has_key:true});
  if(u.pathname==='/api/models')return json(models);
  if(u.pathname==='/api/model-recommendation')return json({mode:'standard',label:'标准研究',role_models:models.role_profiles.standard,reasons:{}});
  if(u.pathname==='/api/runs'){
   if(request.method()==='POST'){
    const body=request.postDataJSON();creates.push(body);
    if(failNext){failNext=false;return json({detail:'固定测试：创建失败，请稍后再试'},502);}
    if(holdCreate)await new Promise(resolve=>{releaseCreate=resolve;});
    return json({run_id:'layout-created'});
   }
   return json({runs:Object.values(history).map(h=>({...h,created_at:'2026-11-02T04:00:00Z',report_count:h.reports.length}))});
  }
  const match=u.pathname.match(/^\/api\/runs\/(layout-complete|layout-waiting)$/);
  if(match)return json(history[match[1]]);
  if(u.pathname==='/favicon.ico')return route.fulfill({status:204});
  unexpected.push(request.method()+' '+u.pathname);return route.fulfill({status:404,body:''});
 });
 const send=event=>page.evaluate(e=>window.__streams.at(-1).send({...e,run_id:'layout-created',ts:new Date().toISOString()}),event);
 const welcome=async()=>{
  await page.waitForFunction(()=>document.body.classList.contains('welcome-view'));
  assert.equal(await page.locator('#welcome-screen:visible').count(),1);
  assert.equal(await page.locator('#dashboard-screen:visible').count(),0);
  assert.equal(await page.locator('#welcome-input-slot #task-input').count(),1);
  assert.equal(await page.locator('#welcome-send-slot #btn-run').count(),1);
  assert.equal(await page.locator('#welcome-pet-host #research-pet').count(),1);
 };
 const dashboard=async()=>{
  await page.waitForFunction(()=>!document.body.classList.contains('welcome-view')&&document.querySelector('#dashboard-screen').getClientRects().length>0,null,{timeout:5000});
  assert.equal(await page.locator('#welcome-screen:visible').count(),0);
  assert.equal(await page.locator('.sidebar #task-input').count(),1);
  assert.equal(await page.locator('.sidebar #btn-run').count(),1);
  assert.equal(await page.locator('#dashboard-pet-slot #research-pet').count(),1);
  assert.equal(await page.evaluate(()=>window.__input===document.querySelector('#task-input')&&window.__send===document.querySelector('#btn-run')&&window.__pet===document.querySelector('#research-pet')),true,'move the original interactive elements, never clone/recreate them');
 };
 try{
  await page.goto('http://layout.test/');
  await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled&&document.querySelectorAll('.history-item').length===2);
  await welcome();
  await page.evaluate(()=>{window.__input=document.querySelector('#task-input');window.__send=document.querySelector('#btn-run');window.__pet=document.querySelector('#research-pet');});
  for(const id of ['task-input','btn-run','research-pet'])assert.equal(await page.locator('#'+id).count(),1,`duplicate ${id}`);
  await page.locator('#fish-stroke').click();assert.equal(await page.locator('#fish-name').textContent(),'小岚 进度导游');
  for(const width of [1920,1366,768,390,320]){
   await page.setViewportSize({width,height:1000});await page.waitForTimeout(50);
   const v=await page.evaluate(()=>{const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};return {width:innerWidth,scroll:document.documentElement.scrollWidth,input:rect('#task-input'),pet:rect('#research-pet'),send:rect('#btn-run')};});
   geometry.push({view:'welcome',...v});assert.ok(v.scroll<=width+1,`welcome overflow at ${width}`);
   assert.ok(v.input.x>=0&&v.input.right<=width+1,`welcome input outside viewport at ${width}`);
   assert.ok(v.pet.bottom<=v.input.y+1,`fish must sit above input at ${width}`);
   assert.ok(v.send.x>=0&&v.send.right<=width+1,`send button outside viewport at ${width}`);
  }
  await page.setViewportSize({width:1920,height:1100});await page.screenshot({path:path.join(out,'welcome-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'welcome-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1920,height:1100});
  await page.locator('#btn-preview-workspace').click();await dashboard();
  assert.equal(await page.locator('#btn-new-research').isDisabled(),false);await page.locator('#btn-new-research').click();await welcome();
  await page.locator('#task-input').fill('   ');await page.locator('#btn-run').click();await welcome();assert.equal(creates.length,0,'blank input must not create a run');
  const draft='分析公开资料的可信度与来源覆盖，形成一份有依据、有局限说明的研究报告（固定测试）';
  await page.locator('#task-input').fill(draft);
  failNext=true;await page.locator('#btn-run').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('固定测试：创建失败'));
  await welcome();assert.equal(await page.locator('#task-input').inputValue(),draft);assert.equal(creates.length,1);assert.equal(await page.locator('#btn-run').isDisabled(),false);
  await page.locator('#execution-mode').selectOption('deep');await page.locator('#search-strategy').selectOption('adaptive');
  await page.locator('#task-input').dispatchEvent('keydown',{key:'Enter',ctrlKey:true,isComposing:true});await page.waitForTimeout(80);assert.equal(creates.length,1,'IME composing Enter must not submit');
  holdCreate=true;await page.locator('#task-input').press('Control+Enter');
  await page.waitForFunction(()=>document.querySelector('#btn-run').disabled);await page.locator('#task-input').press('Control+Enter');
  assert.equal(creates.length,2,'Ctrl+Enter while pending must submit only once');
  assert.equal(typeof releaseCreate,'function');holdCreate=false;releaseCreate();
  await dashboard();assert.equal(await page.locator('#task-input').inputValue(),draft);assert.equal(await page.locator('#fish-name').textContent(),'小岚 进度导游');
  assert.equal(creates.at(-1).execution_mode,'deep');assert.equal(creates.at(-1).search_strategy,'adaptive');
  assert.equal(await page.locator('#btn-new-research').isDisabled(),true,'active task cannot be silently abandoned');
  assert.equal(await page.locator('#execution-mode').inputValue(),'deep');assert.equal(await page.locator('#search-strategy').inputValue(),'adaptive');
  await send({type:'plan',stage:'planning',plan});await send({type:'confirm_required',stage:'planning',plan,timeline:timeline('planning')});await page.locator('#confirm-modal').waitFor({state:'visible'});
  assert.equal(await page.locator('#btn-new-research').isDisabled(),true);await page.locator('[data-close="confirm-modal"]').click();
  for(const width of [1920,1366,768,390,320]){
   await page.setViewportSize({width,height:1000});await page.waitForTimeout(50);
   const v=await page.evaluate(()=>{const rect=node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};};const heading=document.querySelector('.workspace-heading');let following=heading.nextElementSibling;while(following&&!following.getClientRects().length)following=following.nextElementSibling;return {width:innerWidth,scroll:document.documentElement.scrollWidth,heading:rect(heading),hud:rect(document.querySelector('.tactical-hud-bar')),following:rect(following)};});
   geometry.push({view:'dashboard',...v});assert.ok(v.scroll<=width+1,`dashboard overflow at ${width}`);
   assert.ok(v.heading.height<=(width>=1366?200:310),`task header is still too tall at ${width}: ${v.heading.height}`);
   const gap=v.following.y-v.heading.bottom;assert.ok(gap>=-1&&gap<=32,`large empty gap below task bar at ${width}: ${gap}`);
  }
  await page.locator('#toast').waitFor({state:'hidden'});await page.setViewportSize({width:1920,height:1100});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'dashboard-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'dashboard-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1920,height:1100});
  await send({type:'error',stage:'planning',message:'固定测试中断',can_resume:true,timeline:timeline('planning','error')});
  assert.equal(await page.locator('#btn-new-research').isDisabled(),false);await page.locator('#btn-focus').click();assert.equal(await page.locator('body.focus-mode').count(),1);await page.locator('#btn-new-research').evaluate(b=>b.click());await welcome();assert.equal(await page.locator('body.focus-mode').count(),0,'new welcome restores the settings/history sidebar');
  const closed=await page.evaluate(()=>window.__streams.every(s=>s.closed));assert.equal(closed,true,'new research must detach the terminal run stream');
  await page.locator('[data-run="layout-complete"]').click();await page.waitForFunction(()=>document.querySelector('#task-subtitle').textContent.includes('layout-complete'));await dashboard();
  assert.equal(await page.locator('body.report-reading').count(),1);assert.match(await page.locator('#stage-content').textContent(),/布局测试最终报告/);
  await page.locator('#btn-new-research').click();await welcome();
  await page.locator('[data-run="layout-waiting"]').click();await page.locator('#sources-modal').waitFor({state:'visible'});await dashboard();
  assert.equal(await page.locator('#btn-new-research').isDisabled(),true);assert.equal(await page.locator('#research-pet').getAttribute('data-mood'),'waiting');await page.locator('#btn-close-sources').click();
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  assert.deepEqual(mutations,['POST /api/runs','POST /api/runs'],'layout/preview/home/pet/history must not mutate a task');
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({passed:true,creates:creates.length,viewports:geometry,errors,unexpected,mutations,network:'all fixture; 0 model/search calls'},null,2));
  console.log('WORKSPACE LAYOUT UI PASS: centered welcome, single input/send/pet DOM preserved, blank/failed submit, CtrlEnter single create/IME guard, transition only after success, preview/new/history/pending/report navigation, live-task guard, pet state, compact dashboard header and 5 widths; 0 model/search calls.');
 }catch(error){
  const failure={message:error.message,errors,unexpected,creates,mutations,page:await page.evaluate(()=>({body:document.body.className,toast:document.querySelector('#toast')?.textContent,subtitle:document.querySelector('#task-subtitle')?.textContent,runDisabled:document.querySelector('#btn-run')?.disabled,streams:window.__streams?.map(s=>({url:s.url,closed:s.closed}))}))};
  fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify(failure,null,2));console.error(JSON.stringify(failure));throw error;
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
