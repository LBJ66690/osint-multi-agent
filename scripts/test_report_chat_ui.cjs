// Actual UI, controlled HTTP responses. No external model calls or credentials.
const {chromium}=require('playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
const modelConfig=JSON.parse(fs.readFileSync(path.join(root,'outputs/model_profile_options.json'),'utf8'));
const reports=[{type:'报告甲',markdown:'# 报告甲\n甲报告的完整正文。'},{type:'报告乙',markdown:'# 报告乙\n另一份独立报告的正文。'}];
const threads=new Map(),posts=[],errors=[];let failNext=false,hold=false,release=null,blocked=false;
const version=t=>crypto.createHash('sha256').update(t).digest('hex');
function data(run,type){const report=reports.find(r=>r.type===type);if(!report)return null;const key=JSON.stringify([run,type]);if(!threads.has(key))threads.set(key,{report_type:type,report_version:version(report.markdown),turns:[]});return threads.get(key);}
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.OSINT_TEST_BROWSER?{executablePath:process.env.OSINT_TEST_BROWSER}:{})});
 const page=await browser.newPage({viewport:{width:1500,height:1000}});page.on('pageerror',e=>errors.push(e.message));
 await page.route('http://chat.test/**',async route=>{
  const u=new URL(route.request().url()),method=route.request().method(),json=(value,status=200)=>route.fulfill({status,json:value});
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/index.html'),'utf8')});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:u.pathname.endsWith('.js')?'application/javascript':'text/css',body:fs.readFileSync(path.join(root,'static',path.basename(u.pathname)),'utf8')});
  if(u.pathname==='/api/health')return json({has_key:true});
  if(u.pathname==='/api/models')return json(modelConfig);
  if(u.pathname==='/api/model-recommendation')return json({mode:'quick',label:'简明解答',role_models:modelConfig.role_profiles.quick,reasons:{}});
  if(u.pathname==='/api/runs')return json({runs:['runA','runB'].map(id=>({id,task:id,status:'done',report_count:2,created_at:new Date().toISOString()}))});
  const info=u.pathname.match(/^\/api\/runs\/(runA|runB)$/);
  if(info)return json({id:info[1],task:info[1],status:'done',events:[],reports,summary:{},search_sources:[]});
  const chat=u.pathname.match(/^\/api\/runs\/(runA|runB)\/report-chat$/);
  if(chat){
   if(method==='GET'){
    if(blocked)return json({detail:'报告超过 40000 字符，暂不支持整份问答'},413);
    return json(data(chat[1],u.searchParams.get('report_type')));
   }
   const b=route.request().postDataJSON();posts.push(b);
   if(failNext){failNext=false;return json({detail:'问答模型暂时不可用，请换模型重试。'},502);}
   if(hold)await new Promise(resolve=>{release=resolve;});
   const d=data(chat[1],b.report_type),old=d.turns.find(t=>t.id===b.request_id);
   if(!old)d.turns.push({id:b.request_id,model_id:b.model_id,question:b.question,answer:`模型 ${b.model_id}：${b.question}\n<img src=x onerror=alert(1)> 只作为文字显示`,context_turns:Math.min(d.turns.length,3),usage:null});
   return json({...d,reused:!!old});
  }
  return route.fulfill({status:404,body:''});
 });
 try{
  await page.goto('http://chat.test/');await page.locator('[data-run="runA"]').click();await page.locator('#report-ready').waitFor({state:'visible'});
  assert.equal(await page.locator('#report-chat-modal:visible').count(),0);
  await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  assert.match(await page.locator('#report-chat-title').textContent(),/报告甲/);
  assert.match(await page.locator('#report-chat-note').textContent(),/消耗.*额度/);
  await page.locator('#report-chat-send').click();assert.match(await page.locator('#report-chat-error').textContent(),/请输入/);assert.equal(posts.length,0);
  await page.locator('#report-chat-model').selectOption('kimi-k3');await page.locator('#report-chat-question').fill('报告最重要的依据是什么？');
  await page.evaluate(()=>{document.querySelector('#report-chat-form').requestSubmit();document.querySelector('#report-chat-form').requestSubmit();});
  await page.waitForFunction(()=>document.querySelectorAll('.chat-assistant:not(.chat-waiting)').length===1);
  assert.equal(posts.length,1);assert.equal(posts[0].model_id,'kimi-k3');assert.equal(posts[0].report_type,'报告甲');assert.equal('report' in posts[0],false);
  assert.equal(await page.locator('#report-chat-messages img').count(),0);assert.match(await page.locator('#report-chat-messages').textContent(),/kimi-k3/);
  await page.locator('#report-chat-modal').press('Escape');assert.equal(await page.locator('#btn-ask-report').evaluate(e=>e===document.activeElement),true);
  await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  assert.equal(await page.locator('.chat-user').count(),1);
  failNext=true;await page.locator('#report-chat-question').fill('失败后请保留这个问题');await page.locator('#report-chat-send').click();
  await page.waitForFunction(()=>document.querySelector('#report-chat-error').textContent.includes('暂时不可用'));
  const failedId=posts.at(-1).request_id;assert.equal(await page.locator('#report-chat-question').inputValue(),'失败后请保留这个问题');
  await page.locator('#report-chat-close').click();await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  assert.equal(await page.locator('#report-chat-question').inputValue(),'失败后请保留这个问题');await page.locator('#report-chat-send').click();
  await page.waitForFunction(()=>document.querySelectorAll('.chat-assistant:not(.chat-waiting)').length===2);assert.equal(posts.at(-1).request_id,failedId);
  await page.locator('#report-chat-close').click();await page.locator('[data-report="报告乙"]').click();await page.locator('[data-chat-report]').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  assert.match(await page.locator('#report-chat-title').textContent(),/报告乙/);assert.equal(await page.locator('.chat-user').count(),0);
  hold=true;await page.locator('#report-chat-question').fill('这条延迟回答只属于报告乙');await page.locator('#report-chat-send').click();
  await page.locator('.chat-waiting').waitFor({state:'visible'});assert.equal(await page.locator('#report-chat-model').isDisabled(),true);
  await page.locator('#report-chat-close').click();await page.locator('#btn-ask-report').click();await page.locator('.chat-waiting').waitFor({state:'visible'});
  await page.locator('#report-chat-close').click();await page.locator('[data-run="runB"]').click();await page.locator('#btn-ask-report').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  hold=false;assert.ok(release);release();await page.waitForTimeout(150);
  assert.doesNotMatch(await page.locator('#report-chat-messages').textContent(),/延迟回答/);assert.equal(await page.locator('.chat-user').count(),0);
  for(const width of [1500,390,320]){
   await page.setViewportSize({width,height:800});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
   assert.equal(await page.locator('.report-chat-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth+1),true);
   assert.equal(await page.locator('.report-chat-dialog').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1&&r.height<=innerHeight;}),true);
  }
  await page.setViewportSize({width:320,height:568});assert.equal(await page.locator('.report-chat-dialog').evaluate(e=>e.getBoundingClientRect().height<=innerHeight),true);
  await page.setViewportSize({width:1500,height:1000});await page.locator('#report-chat-close').click();await page.locator('[data-run="runA"]').click();await page.locator('[data-report="报告乙"]').click();await page.locator('[data-chat-report]').click();await page.waitForFunction(()=>!document.querySelector('#report-chat-question').disabled);
  assert.match(await page.locator('#report-chat-messages').textContent(),/延迟回答/);
  await page.screenshot({path:path.join(root,'outputs/report-chat-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:800});await page.screenshot({path:path.join(root,'outputs/report-chat-mobile.png'),fullPage:true});
  await page.locator('#report-chat-close').click();blocked=true;await page.locator('#btn-ask-report').click();
  await page.waitForFunction(()=>document.querySelector('#report-chat-error').textContent.includes('40000'));assert.equal(await page.locator('#report-chat-send').isDisabled(),true);
  assert.deepEqual(errors,[]);
  console.log('REPORT CHAT UI PASS: entry, model choice, double-click guard, history, retry ID, XSS escaping, report/run isolation, late replies, close/reopen, keyboard and narrow layouts');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
