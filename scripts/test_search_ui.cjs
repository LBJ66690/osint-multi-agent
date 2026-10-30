// Browser regressions with fixture API/SSE. No real research or model calls.
const {chromium}=require('playwright');const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');const options=JSON.parse(fs.readFileSync(path.join(root,'outputs/search_source_options.json'),'utf8'));
const modelConfig=JSON.parse(fs.readFileSync(path.join(root,'outputs/model_profile_options.json'),'utf8'));
const resumptions=[];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.OSINT_TEST_BROWSER?{executablePath:process.env.OSINT_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[],submissions=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.__streams=[];window.EventSource=class{constructor(url){this.url=url;window.__streams.push(this);setTimeout(()=>this.onopen?.(),0);}close(){this.closed=true;}send(data){if(!this.closed)this.onmessage?.({data:JSON.stringify(data)});}};});
 const started=Date.now()-128000;
 const plan={goal:'调研贵阳花溪旅游口碑，形成有来源的研究报告',subquestions:[{question:'哪些景点值得关注？',search_queries:['贵阳 花溪 旅游 景点']},{question:'游客主要关注哪些问题？',search_queries:['花溪旅游 游客评价']}],report_types:['舆情分析']};
 const pending={options,selected:[],request_id:'choice',goal:plan.goal};
 const timeline={server_ms:Date.now(),started_ms:started,ended_ms:null,current_stage:'planning',active_stage:null,wait_started_ms:Date.now()-15000,wait_total_ms:0,wait_reason:'等待选择 MCP',stages:{planning:{elapsed_ms:93000,status:'done',active_since_ms:null,label:'规划完成',model:'Qwen · 研究规划'}}};
 const detail={id:'demo',task:plan.goal,status:'awaiting_sources',pending_sources:pending,search_sources:[],timeline,events:[{type:'plan',plan}],reports:[],summary:{}};
 await page.route('http://osint.test/**',async route=>{const url=new URL(route.request().url()),json=body=>route.fulfill({json:body});
  if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/index.html'),'utf8')});
  if(url.pathname.startsWith('/static/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'static',path.basename(url.pathname)),'utf8')});
  if(url.pathname==='/api/models')return json(modelConfig);
  if(url.pathname==='/api/model-recommendation'){const mode=url.searchParams.get('task')?.includes('是什么')?'quick':'standard';return json({mode,label:mode==='quick'?'简明解答':'标准研究',role_models:modelConfig.role_profiles[mode],reasons:modelConfig.profile_reasons[mode]});}
  if(url.pathname==='/api/health')return json({has_key:true});if(url.pathname==='/api/models')return json({models:[{id:'qwen3.8-flash',label:'Qwen3.8 Flash'},{id:'deepseek-v4.1-flash',label:'DeepSeek Flash'},{id:'qwen3.8-27b',label:'Qwen3.8 27B'}],role_defaults:{planner:'qwen3.8-flash',collector:'deepseek-v4.1-flash',analyst:'deepseek-v4.1-flash',reasoner:'deepseek-v4.1-flash',reporter:'qwen3.8-27b',verifier:'qwen3.8-flash'},role_reasons:{planner:'快速规划，避免过度拆解'}});
  if(url.pathname==='/api/runs')return json({runs:[{...detail,report_count:0,created_at:new Date(started).toISOString()}]});
  if(url.pathname==='/api/runs/demo')return json(detail);
  if(url.pathname==='/api/runs/demo/sources'){submissions.push(route.request().postDataJSON());return json({ok:true});}
  if(url.pathname==='/api/runs/demo/resume'){resumptions.push(route.request().postDataJSON());detail.status='running';detail.events.push({type:'resumed'});return json({ok:true});}
  if(url.pathname==='/api/runs/demo/report.md')return route.fulfill({contentType:'text/markdown',headers:{'Content-Disposition':'attachment; filename="report.md"'},body:'# Final report'});
  return route.fulfill({status:404,body:''});
 });
 const send=event=>page.evaluate(e=>window.__streams.at(-1).send({...e,run_id:'demo',ts:new Date().toISOString()}),event);
 try{
  await page.goto('http://osint.test/');
  assert.equal(await page.locator('#topology-modal:visible').count(),0);assert.equal(await page.locator('[data-atlas-node]').count(),0);
  await page.locator('#btn-topology').click();await page.locator('#topology-modal').waitFor({state:'visible'});
  assert.equal(await page.locator('[data-atlas-node]').count(),6);
  assert.equal(await page.locator('.atlas-flow').evaluate(el=>getComputedStyle(el).display),'grid');
  assert.equal(await page.locator('.atlas-flow').evaluate(el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),6);
  await page.locator('#tab-architecture').click();assert.equal(await page.locator('.architecture-stack article').count(),5);assert.equal(await page.locator('#topology-workflow:visible').count(),0);
  await page.locator('#tab-architecture').press('Tab');assert.equal(await page.locator('[aria-label="关闭项目拓扑"]').evaluate(el=>el===document.activeElement),true);
  await page.locator('#tab-architecture').focus();
  await page.locator('#tab-architecture').press('ArrowLeft');assert.equal(await page.locator('#tab-workflow').getAttribute('aria-selected'),'true');
  await page.locator('[data-atlas-node="reasoning"]').click();assert.match(await page.locator('#atlas-inspector').textContent(),/串联证据/);
  await page.locator('#topology-modal').press('Escape');assert.equal(await page.locator('#topology-modal:visible').count(),0);assert.equal(await page.locator('#btn-topology').evaluate(el=>el===document.activeElement),true);
  await page.locator('#btn-run').waitFor({state:'visible'});await page.waitForFunction(()=>!document.querySelector('#btn-run').disabled);
  await page.waitForFunction(()=>document.querySelector('[data-role="planner"]').value==='qwen3.8-27b');
  for(const mode of ['quick','standard','deep']){await page.locator('#execution-mode').selectOption(mode);for(const [role,id] of Object.entries(modelConfig.role_profiles[mode]))assert.equal(await page.locator(`[data-role="${role}"]`).inputValue(),id);}
  await page.locator('#execution-mode').selectOption('auto');await page.locator('#task-input').fill('大肥鱼是什么');await page.waitForFunction(()=>document.querySelector('[data-role="planner"]').value==='qwen3.8-flash');
  assert.equal(await page.locator('#role-grid select:disabled').count(),6);
  assert.equal(await page.locator('[data-role="planner"] option').evaluateAll(options=>options.filter(o=>o.hasAttribute('disabled')).length),modelConfig.models.filter(m=>m.available===false).length);
  await page.locator('#model-mode').selectOption('custom');assert.equal(await page.locator('#role-grid select:disabled').count(),0);
  await page.locator('.advanced summary').click();await page.locator('[data-role="planner"]').selectOption('glm-5.3');await page.locator('#execution-mode').selectOption('deep');assert.equal(await page.locator('[data-role="planner"]').inputValue(),'glm-5.3');await page.locator('#btn-reset-models').click();assert.equal(await page.locator('[data-role="planner"]').inputValue(),'qwen3.8-max');assert.equal(await page.locator('#model-mode').inputValue(),'recommended');await page.locator('.advanced summary').click();
  await page.locator('[data-run="demo"]').click();await page.locator('#sources-modal').waitFor({state:'visible'});
  assert.equal(await page.locator('#sources-options input').count(),3);await page.locator('#btn-sources-start').click();assert.match(await page.locator('#sources-error').textContent(),/至少选择/);assert.equal(submissions.length,0);
  await page.locator('#sources-options input[value="parallel"]').check();await page.locator('#sources-options input[value="you"]').check();await page.locator('#btn-close-sources').click();await page.locator('[data-open-pending]:visible').click();assert.equal(await page.locator('#sources-options input[value="parallel"]').isChecked(),true);
  await page.locator('#btn-sources-start').click();await page.locator('#sources-modal').waitFor({state:'hidden'});assert.deepEqual(submissions,[{request_id:'choice',sources:['parallel','you']}]);
  const liveTimeline={...timeline,server_ms:Date.now(),current_stage:'collecting',active_stage:'collecting',wait_started_ms:null,wait_reason:'',stages:{...timeline.stages,collecting:{elapsed_ms:18000,active_since_ms:Date.now()-18000,status:'running',label:'收集官：通用网页搜索',model:'Parallel + You.com · MCP'}}};
  await send({type:'sources_selected',selected:['parallel','you'],timeline:liveTimeline});await send({type:'stage',stage:'collecting',status:'running',label:'收集官：查询网页，汇入证据',model_label:'Parallel + You.com · MCP',timeline:liveTimeline});
  await send({type:'tool_call',stage:'collecting',tool:'parallel',call_id:'p1',args:{query:'贵阳 花溪 旅游 景点'}});await send({type:'tool_call',stage:'collecting',tool:'you',call_id:'y1',args:{query:'花溪 旅游 评价'}});
  const evidence=[{title:'花溪十里河滩 · 城市与自然之间',content:'网页检索片段：花溪湿地公园与步行线路。以下为测试固定内容，不是实际任务结果。',source:'https://example.com/huaxi',provider:'parallel',subquestion:'哪些景点值得关注？'}];
  await send({type:'tool_result',stage:'collecting',tool:'parallel',call_id:'p1',result_count:1,items:evidence,preview:'返回 1 条带来源的网页片段'});await send({type:'collection_progress',subquestion:'哪些景点值得关注？',count:1});
  assert.equal(await page.locator('.flight-card').count(),1);assert.equal(await page.locator('[data-star="collecting"].star-live').count(),1);
  await page.waitForTimeout(250);assert.equal(await page.locator('#metric-evidence').textContent(),'1');assert.equal(await page.locator('#live-screen.is-running').count(),1);assert.equal(await page.locator('[data-beam="you"].busy').count(),1);assert.notEqual(await page.locator('#stage-time').textContent(),'00:00');
  await send({type:'sources_selected',selected:['parallel','you','bailian']});
  await send({type:'tool_call',stage:'collecting',tool:'bailian',call_id:'b1'});
  await send({type:'tool_result',stage:'collecting',tool:'bailian',call_id:'b1',preview:'历史事件没有统计字段'});
  assert.match(await page.locator('#channels-grid').textContent(),/条数未统计/);
  await send({type:'tool_result',stage:'collecting',tool:'bailian',call_id:'b1',result_count:0});
  assert.match(await page.locator('#channels-grid').textContent(),/未找到网页/);
  await send({type:'tool_result',stage:'collecting',tool:'bailian',call_id:'b1',result_count:2});
  assert.match(await page.locator('#channels-grid').textContent(),/返回 2 条/);
  await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(100);await page.screenshot({path:path.join(root,'outputs/dynamic-workbench-desktop.png'),fullPage:true});await page.screenshot({path:path.join(root,'outputs/dynamic-workbench-preview.png')});
  await page.locator('#btn-topology').click();assert.equal(await page.locator('[data-atlas-node="collecting"].atlas-current').count(),1);
  await send({type:'gate_resolved',stage:'collecting',decision:'continue'});assert.equal(await page.locator('#topology-modal:visible').count(),1);
  await page.screenshot({path:path.join(root,'outputs/project-topology-desktop.png')});
  await page.locator('[data-atlas-node="planning"]').click();await page.locator('#atlas-view-stage').click();assert.equal(await page.locator('#topology-modal:visible').count(),0);assert.match(await page.locator('#view-title').textContent(),/规划官/);
  await page.locator('[data-stage="planning"]').click();await page.waitForTimeout(150);assert.match(await page.locator('#view-title').textContent(),/规划官/);await send({type:'collection_progress',subquestion:'游客主要关注哪些问题？',count:0});await page.waitForTimeout(150);assert.match(await page.locator('#view-title').textContent(),/规划官/);assert.match(await page.locator('#live-stage-label').textContent(),/收集官/);
  await page.locator('#btn-follow').click();await page.waitForTimeout(150);assert.match(await page.locator('#view-title').textContent(),/收集官/);
  await page.locator('[data-stage="reporting"]').click();await send({type:'report_start',stage:'reporting',report_type:'舆情分析',version:'old',attempt:1,mode:'stream'});await send({type:'report_delta',report_type:'舆情分析',version:'old',delta:'不完整旧正文'});await send({type:'report_start',stage:'reporting',report_type:'舆情分析',version:'new',attempt:2,mode:'stream'});await send({type:'report_delta',report_type:'舆情分析',version:'new',delta:'新正文已重新生成'});await send({type:'report_delta',report_type:'舆情分析',version:'old',delta:'过期片段'});await page.waitForTimeout(200);assert.match(await page.locator('#stage-content').textContent(),/新正文/);assert.doesNotMatch(await page.locator('#stage-content').textContent(),/不完整旧正文|过期片段/);
  await page.locator('#btn-follow').click();await page.locator('#btn-motion').click();assert.equal(await page.locator('body.reduced-motion').count(),1);
  await send({type:'tool_result',stage:'collecting',tool:'parallel',call_id:'p2',result_count:2,items:evidence});await page.waitForTimeout(1450);assert.equal(await page.locator('.flight-card').count(),0);
  for(const width of [1366,768,390,320]){await page.setViewportSize({width,height:900});await page.waitForTimeout(150);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,`horizontal overflow at ${width}`);await page.locator('#btn-topology').click();assert.equal(await page.locator('.topology-modal').evaluate(el=>el.scrollWidth<=el.clientWidth+1),true,`topology overflow at ${width}`);if(width===390)await page.screenshot({path:path.join(root,'outputs/project-topology-mobile.png')});await page.locator('#topology-modal').press('Escape');if(width===390)await page.screenshot({path:path.join(root,'outputs/dynamic-workbench-mobile.png'),fullPage:true});}
  await page.reload();await page.locator('#btn-history').click();await page.locator('[data-run="demo"]').click();await page.locator('#sources-modal').waitFor({state:'visible'});assert.notEqual(await page.locator('#stage-time').textContent(),'00:00');assert.deepEqual(errors,[]);
  await page.locator('#btn-close-sources').click();
  const reportEvent={type:'report',stage:'reporting',report_type:'舆情分析',markdown:'# 花溪旅游研究结论\n\n这是固定测试报告，用于验证最终结果的可发现性。\n\n## 主要结论\n\n报告应在任务完成后直接展示。'};
  await send(reportEvent);await send({type:'stage',stage:'verifying',status:'running',label:'核查报告'});await send({type:'done',summary:{}});await page.waitForTimeout(150);
  assert.equal(await page.locator('#report-ready:visible').count(),1);assert.equal(await page.locator('body.report-reading').count(),1);assert.equal(await page.locator('#view-title').textContent(),'最终报告');assert.equal(await page.locator('#live-screen:visible').count(),0);assert.match(await page.locator('#stage-content').textContent(),/花溪旅游研究结论/);
  await page.locator('[data-stage="verifying"]').click();assert.equal(await page.locator('body.report-reading').count(),0);assert.equal(await page.locator('#report-ready:visible').count(),1);await page.locator('#btn-open-report').click();assert.equal(await page.locator('body.report-reading').count(),1);
  await send({type:'verification',stage:'verifying',report_type:'舆情分析',verification:{verdict:'fail',summary:'已接受的测试问题',issues:[]}});assert.equal(await page.locator('#report-ready.has-warnings').count(),1);assert.match(await page.locator('#report-ready-title').textContent(),/需要审阅/);
  const downloadPromise=page.waitForEvent('download');await page.locator('#btn-download-final').click();assert.equal((await downloadPromise).suggestedFilename(),'report.md');
  detail.status='done';detail.pending_sources=null;detail.events=[{type:'plan',plan}];detail.reports=[{type:'舆情分析',markdown:reportEvent.markdown}];detail.summary={plan,evidence,knowledge:{viewpoints:[]},findings:[]};detail.timeline={...timeline,current_stage:'verifying',ended_ms:Date.now(),wait_started_ms:null};
  await page.reload();await page.locator('#btn-history').click();await page.locator('[data-run="demo"]').click();await page.waitForFunction(()=>document.body.classList.contains('report-reading'));assert.equal(await page.locator('#view-title').textContent(),'最终报告');
  for(const width of [1600,768,390,320]){await page.setViewportSize({width,height:1000});await page.waitForTimeout(100);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,`final report overflow at ${width}`);if(width===1600)await page.screenshot({path:path.join(root,'outputs/final-report-desktop.png'),fullPage:true});if(width===390)await page.screenshot({path:path.join(root,'outputs/final-report-mobile.png'),fullPage:true});}
  detail.status='error';detail.events=[{type:'error',message:'推理超时',stage:'reasoning'}];detail.resume_info={reuse_labels:['规划','收集','分析'],next_label:'推理'};
  await page.setViewportSize({width:1600,height:1000});await page.reload();await page.locator('[data-run="demo"]').click();await page.locator('#btn-resume').waitFor({state:'visible'});assert.match(await page.locator('#resume-help').textContent(),/从推理继续/);assert.equal(await page.locator('#resume-change-models').isChecked(),false);
  await page.locator('#btn-resume').click();await page.waitForFunction(()=>document.querySelector('#error-banner').classList.contains('hidden'));assert.deepEqual(resumptions[0],{use_recommended:false,role_models:null});
  assert.deepEqual(errors,[]);
  console.log('UI PASS: loaded topology CSS, lazy manual topology, tabs/focus/Escape, stage navigation, live highlighting, evidence flight, reduced motion, mobile layouts, model profiles, resume, report completion/download');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
