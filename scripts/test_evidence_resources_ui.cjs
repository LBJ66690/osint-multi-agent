// Real browser, local asset bytes and fixture HTTP/SSE only. No paid or external calls.
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'..'),out=path.join(root,'outputs/engineering-review/ui');
const models=JSON.parse(fs.readFileSync(path.join(root,'outputs/model_profile_options.json'),'utf8'));
const marker='<img src=x onerror="window.__injected=true">';
const plan={goal:'核对项目公告与公开调查中的关键差异',subquestions:[{question:'项目时间与经营数据是否一致？',search_queries:['项目 时间 公告']}],report_types:['研究报告']};
const evidence=[{subquestion:plan.subquestions[0].question,title:'原始声明 '+marker,content:'甲项目在2025年完成一期建设。声明尚未明确二期进度，后续调查给出不同启动日期。'+marker,source:'https://original.example/report',provider:'parallel'}];
const quality={raw_count:3,nonduplicate_count:2,source_group_count:1,independent_source_count:null,provenance:[
 {source:'https://copy-one.example/news',independence_status:'shared_origin',origin_type:'secondary',reason:'转引同一份公告 '+marker},
 {source:'https://copy-two.example/news',independence_status:'shared_origin',origin_type:'secondary',reason:'正文高度相似，不能当独立佐证'}]};
const conflicts=[{topic:'项目启动日期',reason:'同一主体出现不同时间，尚需核对统计口径 '+marker,status:'uncertain',values:{'2025':['https://original.example/report'],'2026':['https://another.example/report']}}];
const findings=['supported','inference','uncertain','unconfirmed'].map((status,i)=>({claim:`第${i+1}条结论 `+marker,status,evidence:['给定片段的支持依据 '+marker],counter_evidence:['存在需要保留的不同说法 '+marker],sources:['https://original.example/report','javascript:window.__injected=true'],uncertainty_reason:'时间和统计口径尚未独立确认 '+marker}));
const checks={verdict:'pass',scope:'provided_evidence',summary:'仅完成本次材料对照 '+marker,issues:[{claim:'需要复核的陈述 '+marker,explanation:'引用未覆盖关键属性 '+marker,severity:'中',verdict:'存疑',check_type:'semantic_support'}]};
const report={type:'研究报告',markdown:'# 固定测试研究报告\n\n项目时间仍存在无法确认的差异。\n\n[E1](https://original.example/report)\n\n'+marker};
const timeline=(stage,status='running')=>({server_ms:Date.now(),started_ms:Date.now()-20000,ended_ms:status==='done'?Date.now():null,current_stage:stage,active_stage:status==='running'?stage:null,wait_started_ms:null,wait_total_ms:0,wait_reason:'',stages:{[stage]:{status,elapsed_ms:20000,active_since_ms:null,label:'固定测试阶段'}}});
const partial={model_calls:5,model_retries:1,model_failures:1,parse_failures:1,prompt:null,completion:null,total:null,known_total:120,unknown_usage_calls:2,
 search_calls:4,search_failures:1,search_retries:1,supplemental_searches:1,restored_operations:3,operation_retries:2,
 stop_reasons:['补搜达到预算上限 '+marker],roles:{planner:{model_calls:1,prompt:0,completion:0,model_retries:0,model_failures:0,parse_failures:0},analyst:{model_calls:2,prompt:100,completion:20,model_retries:1,model_failures:0,parse_failures:1},verifier:{model_calls:2,prompt:null,completion:null,model_retries:0,model_failures:1,parse_failures:0}}};
const actualZero={...partial,model_calls:1,total:0,known_total:0,unknown_usage_calls:0,search_calls:0,search_failures:0,search_retries:0,supplemental_searches:0,restored_operations:0,operation_retries:0,stop_reasons:[],roles:{planner:partial.roles.planner}};
const unknown={...actualZero,total:null,unknown_usage_calls:1,roles:{verifier:{...partial.roles.verifier,model_calls:1}}};
const records={
 live:{id:'live',task:'固定数据实时任务',status:'running',events:[],reports:[],summary:{plan},timeline:timeline('collecting'),search_sources:['parallel']},
 snapshot:{id:'snapshot',task:'仅摘要的历史快照',status:'done',events:[],reports:[report],summary:{resource_usage:partial,plan,evidence,findings,evidence_metrics:quality,conflicts,verifications:{'研究报告':checks}},timeline:timeline('verifying','done'),search_sources:['parallel']},
 old:{id:'old',task:'没有逐请求统计的旧任务',status:'done',events:[],reports:[report],summary:{},timeline:timeline('verifying','done')}
};

(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.OSINT_TEST_BROWSER?{executablePath:process.env.OSINT_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[],unexpected=[],posts=[],geometry=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.addInitScript(()=>{window.__streams=[];window.EventSource=class{constructor(url){this.url=url;window.__streams.push(this);setTimeout(()=>this.onopen?.(),0);}close(){this.closed=true;}send(event){if(!this.closed)this.onmessage?.({data:JSON.stringify(event)});}};});
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),json=value=>route.fulfill({json:value});
  if(url.origin!=='http://evidence.test'){unexpected.push(url.href);return route.abort();}
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/workspace.html'),'utf8')});
    if (url.pathname.endsWith('.svg')) return route.fulfill({contentType:'image/svg+xml',body:fs.readFileSync(path.join(root,url.pathname),'utf8')});
  if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'static',path.basename(url.pathname)),'utf8')});
  if (url.pathname === '/api/auth/me') return route.fulfill({status:404,json:{detail:'Not Found'}});
  if(url.pathname==='/api/health')return json({has_key:true});
  if(url.pathname==='/api/models')return json(models);
  if(url.pathname==='/api/model-recommendation')return json({mode:'standard',label:'标准研究',role_models:models.role_profiles.standard,reasons:{}});
  if(url.pathname==='/api/runs')return json({runs:Object.values(records).map(r=>({...r,created_at:'2026-11-05T00:00:00Z',report_count:r.reports.length}))});
  const record=url.pathname.match(/^\/api\/runs\/(live|snapshot|old)$/);
  if(record)return json(records[record[1]]);
  if(url.pathname==='/api/runs/snapshot/report-chat'){
   if(request.method()==='GET')return json({report_type:'研究报告',report_version:'fixture-v1',turns:[]});
   const body=request.postDataJSON();posts.push(body);return json({report_type:'研究报告',report_version:'fixture-v1',turns:[{id:body.request_id,model_id:body.model_id,question:body.question,answer:'固定问答：报告仍有证据缺口。'+marker,context_turns:0,usage:null}]});
  }
  if(url.pathname==='/favicon.ico')return route.fulfill({status:204});
  unexpected.push(request.method()+' '+url.pathname);return route.fulfill({status:404,body:''});
 });
 const send=event=>page.evaluate(e=>window.__streams.at(-1).send({...e,run_id:'live',ts:new Date().toISOString()}),event);
 const select=stage=>page.locator(`#stage-nav [data-stage="${stage}"]`).click();
 const xss=async()=>{
  assert.equal(await page.evaluate(()=>!!window.__injected),false,'untrusted text executed');
  assert.equal(await page.locator('#resource-details img,#stage-content img,#adaptive-decision-list img,#report-chat-messages img').count(),0,'untrusted text created an image node');
  assert.equal(await page.locator('#stage-content a[href^="javascript:"]').count(),0,'unsafe source URL entered a link');
 };
 try{
  await page.goto('http://evidence.test/');
  await page.locator('[data-run="live"]').click();await page.locator('#dashboard-screen').waitFor({state:'visible'});
  await page.locator('#resource-panel summary').click();
  assert.match(await page.locator('#resource-details').textContent(),/没有可靠数据.*不能据此认定零消耗/);
  assert.equal(await page.locator('#metric-tokens').textContent(),'—');
  await send({type:'resource_usage',stats:unknown});
  assert.equal(await page.locator('#metric-tokens').textContent(),'未知');
  assert.match(await page.locator('#resource-details').textContent(),/未知/);
  await send({type:'resource_usage',stats:actualZero});
  assert.equal(await page.locator('#metric-tokens').textContent(),'0');
  assert.equal(await page.locator('#resource-details tbody tr').first().locator('td').nth(1).textContent(),'0');
  await send({type:'resource_usage',stats:partial});
  assert.equal(await page.locator('#metric-tokens').textContent(),'未知');
  assert.match(await page.locator('#metric-token-note').textContent(),/2 次未返回完整用量.*已知 120/);
  assert.match(await page.locator('#resource-brief').textContent(),/5 次已记录模型请求.*4 次搜索.*1 次补搜/);
  assert.equal(await page.locator('#resource-details tbody tr').count(),3);
  const rows=await page.locator('#resource-details tbody tr').allTextContents();
  assert.match(rows[0],/规划官/);assert.match(rows[1],/分析官/);assert.match(rows[2],/核查官.*未知.*1 \/ 0/);
  assert.match(await page.locator('#resource-details').textContent(),/搜索失败 1 次.*搜索重试 1 次.*恢复复用 3 项.*操作重试 2 次/);
  assert.match(await page.locator('#resource-panel').textContent(),/不估算费用/);
  await send({type:'execution_policy',search_strategy:'adaptive',mode:'standard',label:'标准研究',active_seconds:480,message:'固定测试自适应模式'});
  await send({type:'decision',action:'COLLECT_FEEDBACK',subquestion:plan.subquestions[0].question,reason:'关键日期缺少原始依据 '+marker});
  assert.match(await page.locator('#adaptive-decision-list').textContent(),/分析反馈补搜.*关键日期缺少原始依据/);
  await send({type:'evidence',items:evidence});
  await send({type:'evidence_quality',metrics:quality,conflicts});
  await select('collecting');
  assert.match(await page.locator('.evidence-notice').textContent(),/检索资料 ≠ 已证实事实/);
  assert.match(await page.locator('.evidence-notice').textContent(),/来源独立性未确认.*2 条资料存在明显转载/);
  assert.match(await page.locator('.evidence-notice').textContent(),/待核对差异：项目启动日期/);
  await page.locator('.evidence-notice summary').click();
  assert.match(await page.locator('.evidence-notice').textContent(),/转引同一份公告/);
  await xss();
  await page.waitForTimeout(300);
  await page.screenshot({path:path.join(out,'evidence-resources-desktop.png'),fullPage:true});
  await send({type:'findings',findings});await select('reasoning');
  const states=await page.locator('.finding-state').allTextContents();
  assert.equal(states.length,4);assert.match(states[0],/给定材料支持.*尚未独立核实/);assert.match(states[1],/模型推断/);assert.match(states[2],/存在待核对差异/);assert.match(states[3],/无法确认/);
  assert.doesNotMatch(states.join(''),/置信度|中等置信|正确率.*\d/);
  await xss();
  await send({type:'verification',stage:'verifying',report_type:'研究报告',verification:{...checks,issues:[]}});await select('verifying');
  assert.match(await page.locator('.verify-sum').last().textContent(),/材料检查通过/);
  assert.match(await page.locator('.verification-scope').last().textContent(),/不代表已完成外部独立事实核验/);
  await send({type:'verification',stage:'verifying',report_type:'研究报告',verification:checks});await select('verifying');
  assert.match(await page.locator('.verify-sum').last().textContent(),/待复核/,'legacy pass+issues cannot be displayed as clean pass');
  assert.match(await page.locator('#stage-content').textContent(),/引用未覆盖关键属性/);
  await xss();
  // No SSE events in this fixture: all new fields must survive summary-only history loading.
  await page.locator('[data-run="snapshot"]').click();await page.waitForFunction(()=>document.body.classList.contains('report-reading'));
  assert.equal(await page.locator('#report-ready.has-warnings').count(),1,'legacy contradictory report must retain its warning');
  assert.match(await page.locator('#resource-brief').textContent(),/5 次已记录模型请求.*1 次补搜/);
  assert.equal(await page.locator('#metric-tokens').textContent(),'未知');
  await select('collecting');assert.match(await page.locator('.evidence-notice').textContent(),/待核对差异：项目启动日期/);
  await select('reasoning');assert.equal(await page.locator('.finding-state').count(),4);
  await select('verifying');assert.match(await page.locator('.verification-scope').last().textContent(),/不代表已完成/);
  for(const width of [1600,390,320]){
   await page.setViewportSize({width,height:1000});
   for(const stage of ['collecting','reasoning','verifying','reporting']){
    await select(stage);await page.waitForTimeout(80);
    const g=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,resource:document.querySelector('#resource-panel').getBoundingClientRect().width,table:document.querySelector('.resource-table-wrap')?{client:document.querySelector('.resource-table-wrap').clientWidth,scroll:document.querySelector('.resource-table-wrap').scrollWidth}:null}));
    geometry.push({stage,...g});assert.ok(g.scroll<=width+1,`${stage} document overflow at ${width}: ${g.scroll}`);
    assert.ok(g.resource<=width+1,`resource panel exceeds viewport at ${width}`);
   }
   await page.waitForTimeout(300);await page.screenshot({path:path.join(out,`report-resources-${width}.png`),fullPage:true});
  }
  // Existing report-Q&A can still open, select a model and submit one request.
  await page.setViewportSize({width:1600,height:1000});await select('reporting');
  await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  await page.locator('#report-chat-model').selectOption('kimi-k3.1-pro');await page.locator('#report-chat-question').fill('哪些结论仍然无法确认？');await page.locator('#report-chat-send').click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-assistant:not(.chat-waiting)').length===1);
  assert.equal(posts.length,1);assert.equal(posts[0].model_id,'kimi-k3.1-pro');assert.equal(posts[0].report_type,'研究报告');await xss();
  await page.locator('#report-chat-close').click();
  await page.locator('[data-run="old"]').click();
  await page.waitForFunction(()=>document.querySelector('#task-title').textContent==='没有逐请求统计的旧任务');
  assert.equal(await page.locator('#metric-tokens').textContent(),'—');
  assert.match(await page.locator('#resource-details').textContent(),/没有可靠数据.*不能据此认定零消耗/);
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[]);
  for(const name of ['failure.json','failure.png'])if(fs.existsSync(path.join(out,name)))fs.unlinkSync(path.join(out,name));
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify({passed:true,checks:['live resources','unknown versus actual zero','known portion','role request failure retry and supplemental counters','summary-only history','reprint and conflict notices','finding states','verification scope','XSS','320/390 width','report Q&A'],geometry,real_model_calls:0,external_search_calls:0,errors,unexpected},null,2));
  console.log('EVIDENCE / RESOURCE UI PASS: real counters, unknown/zero/partial, roles, failures/retries/supplements, history-only snapshot, provenance/conflict, finding states, verification scope, XSS, 320/390 layouts and report Q&A');
 }catch(error){
  await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
  fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:String(error),geometry,errors,unexpected},null,2));throw error;
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
