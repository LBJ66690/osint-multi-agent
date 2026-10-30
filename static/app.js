/* 情报指挥台：事件数据与视图选择分离，动画由真实状态驱动。 */
(() => {
  'use strict';
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const ROLES = [
    ['planning','规划官','◎','PLANNING','理解目标，拆解研究问题'],['collecting','收集官','⌕','WEB RESEARCH','查询公开网页，汇入相关来源'],
    ['analyzing','分析官','▤','ANALYSIS','归并实体，提炼观点与案例'],['reasoning','推理官','◇','SYNTHESIS','对照支持依据与反证，形成发现'],
    ['reporting','最终报告','▧','FINAL REPORT','阅读研究结论、依据与来源'],['verifying','核查记录','✓','QUALITY REVIEW','对照资料与发现，检查输出质量']
  ];
  let modelDefaults={}, modelReasons={}, modelProfiles={}, profileReasons={}, modelsLoaded=false, recommendationRequest=0, recommendationTimer;
  const LABELS = {pending:'尚未开始',running:'执行中',ready:'已产出',done:'已完成',error:'执行失败',cancelled:'已终止',waiting:'等待决策',awaiting_confirm:'等待确认计划',awaiting_sources:'等待选择 MCP',awaiting_gate:'等待核查决策'};
  const PROVIDERS = {parallel:'Parallel',you:'You.com',bailian:'百炼'};
  const FINAL = new Set(['done','error','cancelled']);
  let S, es = null, renderTimer, viewRequest = 0, clockOffset = 0, openTrigger;
  const fresh = () => ({id:null,task:'',status:'pending',view:'planning',follow:true,timeline:null, stages:Object.fromEntries(ROLES.map(r=>[r[0],{status:'pending',label:'',model:''}])),
    plan:null,evidence:[],knowledge:null,findings:[],reports:{},reportTab:null,verifications:{},gates:{},calls:{},activities:[],logs:[],usage:{},sources:[],completedQuestions:new Set(),pending:null,scroll:{},dirty:false,lastTs:null,error:null});
  S = fresh();
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function safeURL(s){try{const u=new URL(s);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}}
  function duration(ms){if(ms==null)return '—';const n=Math.max(0,Math.floor(ms/1000));return n>=3600?`${String(Math.floor(n/3600)).padStart(2,'0')}:${String(Math.floor(n/60)%60).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;}
  function tok(n){return n>=1000?(n/1000).toFixed(1)+'k':String(n||0);}
  function toast(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.add('hidden'),4500);}
  async function api(url,body){const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});let data={};try{data=await r.json();}catch{}if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:`请求失败 (${r.status})`);return data;}
  function health(ok,text){$('#health-pill').classList.toggle('ok',ok);$('#health-pill').classList.toggle('bad',!ok);$('#health-text').textContent=text;}
  function runningUI(){const active=!!S.id&&!FINAL.has(S.status), resumable=['error','cancelled'].includes(S.status);$('#btn-run').disabled=active||!modelsLoaded;$('#btn-run-text').textContent=active?'研究进行中':modelsLoaded?'开始研究':'模型配置载入中';$('#btn-cancel').classList.toggle('hidden',!active);$('#btn-resume').classList.toggle('hidden',!resumable);$('#btn-resume').disabled=!modelsLoaded||!!S.resumeInfo?.unavailable;$('#resume-help').classList.toggle('hidden',!resumable);$('#resume-model-option').classList.toggle('hidden',!resumable);$('#btn-resume').textContent=$('#resume-change-models').checked?'↻ 改用上方模型，从断点继续':'↻ 从中断处继续（保留原模型）';if(resumable)$('#resume-help').textContent=S.resumeInfo?.unavailable?S.resumeInfo.message:S.resumeInfo?`复用：${S.resumeInfo.reuse_labels.join('、')||'已保存操作'}。从${S.resumeInfo.next_label}继续，成功产出不重做。`:'从失败步骤继续，复用成功产出；本次工作预算重新计算，历史计时保留。';}
  function setTimeline(t){if(!t)return;S.timeline=structuredClone(t);clockOffset=t.server_ms-Date.now();}
  function current(){return S.timeline?.current_stage||S.current||'planning';}
  function elapsed(stage,now){const row=S.timeline?.stages?.[stage];if(!row)return null;return row.elapsed_ms+(row.active_since_ms!=null&&!FINAL.has(S.status)?Math.max(0,now-row.active_since_ms):0);}
  function tick(){
    const t=S.timeline,now=Date.now()+clockOffset,wait=!!t?.wait_started_ms;
    $('#total-time').textContent=t?.started_ms?duration((t.ended_ms||now)-t.started_ms):'—';
    const waitStage=S.pending?.kind==='sources'?'collecting':S.pending?.kind==='plan'?'planning':S.pending?.payload.stage;
    const waitingHere=wait&&S.view===waitStage;
    $('#stage-time').textContent=waitingHere?duration(now-t.wait_started_ms):elapsed(S.view,now)==null?'—':duration(elapsed(S.view,now));
    $('#clock-label').textContent=waitingHere?'等待您的操作':'阶段累计执行';
    $('#wait-time').textContent=wait?`${t.wait_reason}${waitingHere?'':` · ${duration(now-t.wait_started_ms)}`}`:'';
    $('#clock-caption').textContent=!S.id?'运行后开始计时':S.view!==current()?'正在查看历史阶段':FINAL.has(S.status)?'本次阶段耗时已固定':wait?'人工等待单独计时':'累计执行 · 包含网络等待';
    for(const r of ROLES){const node=$(`[data-stage="${r[0]}"] .nav-time`);if(node)node.textContent=duration(elapsed(r[0],now));}
    $('#event-age').textContent=S.lastTs?`${duration(now-S.lastTs)} 前更新`:'—';
  }
  function buildNav(){ $('#stage-nav').innerHTML=ROLES.map((r,i)=>`<button class="nav-stage" data-stage="${r[0]}" aria-label="查看${r[1]}"><span class="nav-icon">${r[2]}</span><span><span class="nav-name">${r[1]}</span><span class="nav-status">尚未开始</span></span><span class="nav-time">—</span></button>`).join('');
    $$('#stage-nav button').forEach(b=>b.addEventListener('click',()=>selectStage(b.dataset.stage,false))); }
  function selectStage(stage,follow){S.scroll[S.view]=$('#stage-content').scrollTop;S.view=stage;S.follow=follow;S.dirty=false;renderScreen();renderContent(true);$('#stage-content').scrollTop=S.scroll[stage]||0;}
  function renderScreen(){
    const delivered=S.status==='done'&&Object.keys(S.reports).length>0;
    if(delivered&&!S.completionPresented){S.completionPresented=true;if(S.follow){S.view='reporting';S.follow=false;}}
    const readingReport=delivered&&S.view==='reporting';
    document.body.classList.toggle('report-reading',readingReport);
    $('#report-ready').classList.toggle('hidden',!delivered);
    const warningCount=Object.values(S.verifications).filter(v=>v.verdict==='fail').length;
    $('#report-ready').classList.toggle('has-warnings',delivered&&warningCount>0);
    $('#report-ready-title').textContent=warningCount?'报告已生成，核查问题需要审阅':'研究完成，最终报告已就绪';
    $('#report-ready-note').textContent=delivered?`${Object.keys(S.reports).length} 份报告 · ${Object.keys(S.reports).join(' / ')}${warningCount?` · ${warningCount} 份含已接受的核查问题，请查看附录`:' · 点击阅读结论与引用来源'}`:'';
    const reportPanel=$('.result-panel'),main=$('.main-column');
    if(readingReport&&main.firstElementChild!==reportPanel)main.prepend(reportPanel);
    else if(!readingReport&&main.firstElementChild===reportPanel)main.insertBefore(reportPanel,$('.activity-panel'));
    const role=ROLES.find(r=>r[0]===S.view),live=ROLES.find(r=>r[0]===current()),active=!!S.id&&!FINAL.has(S.status)&&!S.pending;
    $('#task-title').textContent=S.task||'每一次研究，都看得见进展。';
    $('#task-subtitle').textContent=S.id?`研究 ${S.id} · ${LABELS[S.status]||S.status}${S.policy?` · ${S.policy.label} · 工作预算 ${S.policy.active_seconds/60} 分钟（人工等待除外）`:''}`:'创建一个研究任务，在这里查看来源、发现与报告。';
    $('#hero-title').textContent=S.id?role[1]:'研究引擎';$('#hero-description').textContent=S.stages[S.view].label||role[4];
    $('#hero-model').textContent=S.stages[S.view].model||(!S.id?'按真实执行事件更新':'等待本阶段启动');
    $('#stage-number').textContent=role[3];$('#core-symbol').textContent=role[2];$('#core-label').textContent=role[3].split(' ')[0];
    $('#screen-state').textContent=S.pending?'等待您操作':S.id?(FINAL.has(S.status)?LABELS[S.status]:'LIVE · 协作运行中'):'SYSTEM READY';
    $('#live-stage-label').textContent=S.id?`实际执行：${live[1]}${S.view!==current()?' · 正在浏览其他阶段':''}`:'等待新任务';
    $('#live-screen').classList.toggle('is-running',active&&S.view===current());
    $('#view-title').textContent=readingReport?'最终报告':S.id?role[1]+' · 阶段产出':'研究工作区';
    $('#view-kicker').textContent=role[3];$('#view-badge').textContent=readingReport?'报告已完成':S.view===current()&&active?'当前阶段':LABELS[S.stages[S.view].status]||'尚未开始';
    $('#btn-follow').textContent=delivered?'查看最终报告':S.follow?'跟随执行':'返回当前阶段';$('#btn-follow').setAttribute('aria-pressed',String(S.follow));
    for(const r of ROLES){const b=$(`[data-stage="${r[0]}"]`),status=S.stages[r[0]].status;b.classList.toggle('selected',S.view===r[0]);b.classList.toggle('live',r[0]===current()&&active);b.classList.toggle('done',status==='done');b.setAttribute('aria-pressed',String(S.view===r[0]));b.querySelector('.nav-status').textContent=(r[0]===current()&&S.pending)?'等待您操作':LABELS[status]||status;}
    const p=S.pending;$('#pending-action').classList.toggle('hidden',!p);$('#pending-text').textContent=p?({plan:'计划已生成，等待您确认',sources:'规划已确认，等待您选择网页搜索 MCP',gate:'核查发现问题，等待您决定是否重做'}[p.kind]):'';
    tick();runningUI();renderChannels();
    document.dispatchEvent(new CustomEvent('osint:view',{detail:{id:S.id,status:S.status,current:current(),view:S.view,pending:!!S.pending,stages:S.stages,mode:S.policy?.mode,sources:S.sources,roles:S.policy?.role_models||{},reports:Object.keys(S.reports).length}}));
  }
  function changeMetric(id,value){const el=$(id);if(el.textContent!==String(value)){el.textContent=value;el.classList.remove('updated');void el.offsetWidth;el.classList.add('updated');}}
  function renderMetrics(){const calls=Object.values(S.calls),done=calls.filter(c=>c.status!=='busy').length,failed=calls.filter(c=>c.status==='error').length;changeMetric('#metric-evidence',S.evidence.length);changeMetric('#metric-questions',S.plan?`${S.completedQuestions.size} / ${S.plan.subquestions?.length||0}`:'—');changeMetric('#metric-requests',done);$('#metric-request-note').textContent=`${calls.filter(c=>c.status==='busy').length} 个等待 · ${failed} 个失败`;const n=Object.values(S.usage).reduce((a,b)=>a+(b.total||0),0);changeMetric('#metric-tokens',n?tok(n):'—');}
  function renderChannels(){const ids=S.sources.filter(p=>PROVIDERS[p]);$('#channels-panel').classList.toggle('hidden',!ids.length);$('#channels-grid').innerHTML=ids.map(id=>{const calls=Object.values(S.calls).filter(c=>c.tool===id),busy=calls.some(c=>c.status==='busy'),fails=calls.filter(c=>c.status==='error').length,success=calls.filter(c=>c.status==='success'),unknown=success.some(c=>!Number.isInteger(c.result_count)),count=success.reduce((a,c)=>a+(Number.isInteger(c.result_count)?c.result_count:0),0),status=busy?'busy':fails?'error':calls.length?'success':'';const text=busy?'请求 / 等待响应中':!calls.length?'等待请求':count?`返回${unknown?'至少 ': ' '}${count} 条 · ${fails} 次失败`:unknown?`已返回内容 · 条数未统计${fails?` · ${fails} 次失败`:''}`:success.length?`已返回 · 未找到网页${fails?` · ${fails} 次失败`:''}`:fails?'接口请求失败':'请求已中断';const node=$(`[data-source="${id}"]`),beam=$(`[data-beam="${id}"]`);node.className='source-node '+status;beam.classList.add('selected');beam.classList.toggle('busy',busy);return `<div class="channel ${status}"><strong>${PROVIDERS[id]}</strong><span>${text}</span></div>`;}).join('');for(const id of Object.keys(PROVIDERS)){if(!ids.includes(id)){$(`[data-source="${id}"]`).className='source-node hidden';$(`[data-beam="${id}"]`).setAttribute('class','');}}}
  function addActivity(ev,text,error=false){S.lastTs=Date.parse(ev.ts)||Date.now();S.activities.unshift({text,ts:ev.ts,error});S.activities=S.activities.slice(0,40);$('#latest-activity').textContent=text;$('#activity-list').innerHTML=S.activities.slice(0,4).map((a,i)=>`<div class="activity-row ${a.error?'error':''} ${i===0?'new':''}"><span class="activity-dot"></span><span>${esc(a.text)}</span><time>${a.ts?new Date(a.ts).toLocaleTimeString('zh-CN',{hour12:false}):''}</time></div>`).join('');}
  function logEvent(ev){S.logs.push(ev);if(S.logs.length>300)S.logs.shift();if($('#logs-panel').open)renderLogs();}
  function renderLogs(){$('#full-logs').innerHTML=S.logs.slice(-60).reverse().map(e=>`<div><strong>${esc(e.tool||e.type)}</strong><pre>${esc(JSON.stringify(e,null,2))}</pre></div>`).join('');}
  function queueContent(stage){if(S.view!==stage)return;clearTimeout(renderTimer);renderTimer=setTimeout(()=>renderContent(),100);}
  function renderContent(force=false){const box=$('#stage-content'),near=box.scrollHeight-box.scrollTop-box.clientHeight<100,reading=box.contains(document.activeElement)&&document.activeElement?.closest('details');if(!force&&(!near||reading)){S.dirty=true;$('#btn-new-content').classList.remove('hidden');return;}const previous=box.scrollTop;box.innerHTML=contentFor(S.view);if(force){box.classList.remove('enter');void box.offsetWidth;box.classList.add('enter');}if(!force&&near&&S.view==='reporting')box.scrollTop=box.scrollHeight;else box.scrollTop=previous;S.dirty=false;$('#btn-new-content').classList.add('hidden');box.querySelectorAll('[data-report]').forEach(b=>b.addEventListener('click',()=>{S.reportTab=b.dataset.report;renderContent(true);}));box.querySelector('[data-open-pending]')?.addEventListener('click',openPending);box.querySelector('[data-download]')?.addEventListener('click',downloadReport);}
  function tags(items){return `<div class="tags">${(items||[]).map(x=>`<span class="tag">${esc(x)}</span>`).join('')}</div>`;}
  function renderPlan(p){return p?`<div class="plan-goal">${esc(p.goal)}</div>${(p.subquestions||[]).map((q,i)=>`<div class="subq" style="animation-delay:${Math.min(i,5)*55}ms"><div class="subq-q">${i+1}. ${esc(q.question)}</div>${tags(q.search_queries)}</div>`).join('')}${tags(p.report_types)}`:'<div class="empty">计划尚未生成。</div>';}
  function verification(v){return `<div class="verify-sum"><span class="verdict ${v.verdict==='pass'?'pass':'fail'}">${v.verdict==='pass'?'通过':'待复核'}</span>${esc(v.summary||'')}</div>${(v.issues||[]).map(i=>`<div class="verify-issue"><span class="verdict">${esc(i.severity)} · ${esc(i.verdict)}</span><strong>${esc(i.claim)}</strong><p>${esc(i.explanation)}</p></div>`).join('')}`;}
  function contentFor(stage){
    let html='';const pending=S.pending&&((stage==='planning'&&S.pending.kind==='plan')||(stage==='collecting'&&S.pending.kind==='sources')||(S.pending.kind==='gate'&&(S.pending.payload.stage===stage||stage==='verifying')));
    if(pending)html+='<div class="source-pending"><strong>等待您的操作</strong><p>执行已在此等待，您可以先查看已有结果。</p><button class="primary-button" data-open-pending>打开待办</button><button class="hidden" id="btn-reopen-sources" data-open-pending>选择搜索通道</button></div>';
    if(stage==='planning')html+=renderPlan(S.plan);
    if(stage==='collecting')html+=S.evidence.map(e=>`<article class="evidence-item"><div class="evidence-meta">${esc(PROVIDERS[e.provider]||e.provider||'公开来源')} · ${esc(e.subquestion||'')}</div><a class="evidence-title" href="${esc(safeURL(e.source))}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a><p>${esc((e.content||'').slice(0,240))}</p><details><summary>查看检索片段与来源</summary><p>${esc(e.content)}</p><a href="${esc(safeURL(e.source))}" target="_blank" rel="noopener noreferrer">${esc(e.source)}</a></details></article>`).join('')||'<div class="empty">搜索结果将在通道返回后汇入这里。</div>';
    if(stage==='analyzing'&&S.knowledge){const k=S.knowledge;html+=`<div class="section-kicker">实体与别名归并</div><div class="alias-list">${Object.entries(k.aliases||{}).map(([a,b])=>`<span>${esc(a)} → ${esc(b)}</span>`).join('')||'暂无别名映射'}</div>${(k.viewpoints||[]).map(v=>`<article class="finding"><h3>${esc(v.topic)} <span class="tag">${esc(v.sentiment)}</span></h3><p>${esc(v.summary)}</p>${tags(v.examples)}</article>`).join('')}`;}
    if(stage==='reasoning')html+=S.findings.map((f,i)=>`<article class="finding"><h3>${i+1}. ${esc(f.claim)}</h3><details open><summary>支持依据</summary><ul>${(f.evidence||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details><details><summary>反证 / 不同声音</summary><ul>${(f.counter_evidence||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details><div class="tags">${(f.sources||[]).map(x=>`<a class="tag" href="${esc(safeURL(x))}" target="_blank" rel="noopener noreferrer">${esc(x)}</a>`).join('')}</div></article>`).join('');
    if(stage==='reporting'){const types=Object.keys(S.reports);if(types.length){if(!types.includes(S.reportTab))S.reportTab=types[0];const r=S.reports[S.reportTab];html+=`<div class="report-tabs">${types.map(t=>`<button data-report="${esc(t)}" class="${t===S.reportTab?'selected':''}">${esc(t)}</button>`).join('')}${S.status==='done'?'<button data-download>↓ 下载报告</button>':''}</div>${r.notice?`<div class="report-notice">${esc(r.notice)}</div>`:''}<div class="report-md">${markdown(r.markdown)}${r.status==='streaming'?'<span class="write-cursor"></span>':''}</div>`;}}
    if(stage==='verifying')html+=Object.entries(S.gates).map(([key,v])=>`<article class="verify-item"><h3>${esc(ROLES.find(r=>r[0]===key)?.[1]||key)} · 阶段质量关卡</h3>${verification(v)}</article>`).join('')+Object.entries(S.verifications).map(([type,v])=>`<article class="verify-item"><h3>${esc(type)} · 报告核查</h3>${verification(v)}</article>`).join('');
    if(stage!=='verifying'&&S.gates[stage])html+=`<div class="verify-item">${verification(S.gates[stage])}</div>`;
    return html||`<div class="welcome"><span class="welcome-orbit">${ROLES.find(r=>r[0]===stage)[2]}</span><h3>等待${ROLES.find(r=>r[0]===stage)[1]}产出</h3><p>${esc(ROLES.find(r=>r[0]===stage)[4])}。结果返回后会在此呈现。</p></div>`;
  }
  function setPending(kind,payload,replay){S.pending={kind,payload};if(!replay)openPending();}
  function clearPending(kind){if(!kind||S.pending?.kind===kind)S.pending=null;['confirm-modal','sources-modal','gate-modal'].forEach(id=>$('#'+id).classList.add('hidden'));}
  function ingest(ev,replay=false){
    if(ev.run_id&&ev.run_id!==S.id)return;
    if(ev.timeline)setTimeline(ev.timeline);
    let changed=ev.stage||'planning';
    switch(ev.type){
      case 'phase':S.stages[ev.stage].label=ev.label;addActivity(ev,ev.label);break;
      case 'stage':S.stages[ev.stage]={...S.stages[ev.stage],status:ev.status,label:ev.label||'',model:ev.model_label||S.stages[ev.stage].model};if(ev.status==='running'){S.current=ev.stage;S.status='running';if(S.follow)S.view=ev.stage;}addActivity(ev,ev.label||'阶段状态更新');break;
      case 'plan':S.plan=ev.plan;break;
      case 'confirm_required':S.status='awaiting_confirm';setPending('plan',ev,replay);break;
      case 'confirm_resolved':clearPending('plan');S.status='running';break;
      case 'sources_required':changed='collecting';S.status='awaiting_sources';if(S.follow)S.view='collecting';setPending('sources',ev,replay);break;
      case 'sources_selected':S.sources=ev.selected||[];clearPending('sources');S.status='running';changed='collecting';addActivity(ev,'搜索通道已选择，开始查询公开网页');break;
      case 'tool_call':{const id=ev.call_id||`${ev.tool}-${ev.call_index||Object.keys(S.calls).length}`;S.calls[id]={...ev,status:'busy'};addActivity(ev,`${PROVIDERS[ev.tool]||ev.tool} · 开始请求`);logEvent(ev);break;}
      case 'tool_result':case 'tool_error':{const id=ev.call_id||Object.keys(S.calls).reverse().find(k=>S.calls[k].tool===ev.tool&&S.calls[k].status==='busy');if(id)S.calls[id]={...S.calls[id],...ev,status:ev.type==='tool_error'?'error':'success'};if(ev.items)mergeEvidence(ev.items);addActivity(ev,`${PROVIDERS[ev.tool]||ev.tool} · ${ev.type==='tool_error'?'请求失败':ev.result_count===0?'未找到结果':ev.preview||'返回资料'}`,ev.type==='tool_error');logEvent(ev);break;}
      case 'collection_progress':S.completedQuestions.add(ev.subquestion);changed='collecting';addActivity(ev,`子问题完成 · 返回 ${ev.count||0} 条资料`);break;
      case 'evidence':S.evidence=ev.items||[];changed='collecting';break;
      case 'knowledge':S.knowledge=ev.knowledge;changed='analyzing';break;
      case 'findings':S.findings=ev.findings||[];changed='reasoning';break;
      case 'usage':{const u=S.usage[ev.stage]||(S.usage[ev.stage]={total:0});u.total+=Number(ev.total)||0;break;}
      case 'gate_check':S.gates[ev.stage]=ev;changed=ev.stage;if(ev.verdict==='fail'&&ev.interactive){S.status='awaiting_gate';setPending('gate',ev,replay);}addActivity(ev,`${ROLES.find(r=>r[0]===ev.stage)?.[1]||'阶段'} · ${ev.verdict==='pass'?'核查通过':'核查需复核'}`);break;
      case 'gate_resolved':clearPending('gate');S.status='running';break;
      case 'verification':S.verifications[ev.report_type]=ev.verification;changed='verifying';break;
      case 'report_start':S.reports[ev.report_type]={version:ev.version,markdown:'',status:ev.mode==='stream'?'streaming':'generating',notice:ev.attempt>1?`第 ${ev.attempt} 次生成 · ${ev.mode==='nonstream'?'普通响应兜底':'重新生成整份报告'}`:''};S.reportTab=ev.report_type;changed='reporting';break;
      case 'report_delta':{let r=S.reports[ev.report_type];if(!r)r=S.reports[ev.report_type]={markdown:'',status:'streaming',version:ev.version};if(!ev.version||!r.version||r.version===ev.version)r.markdown+=ev.delta||'';changed='reporting';break;}
      case 'report_interrupted':{const r=S.reports[ev.report_type];if(r){r.status='partial';r.notice=`${ev.message}（${ev.partial_chars||0} 字符）${ev.retrying?'，正在准备重试。':'。'} `;}changed='reporting';addActivity(ev,'报告响应中断 · 已保留草稿',true);break;}
      case 'report_retry':changed='reporting';addActivity(ev,ev.message);break;
      case 'report':S.reports[ev.report_type]={markdown:ev.markdown||'',status:'generated'};changed='reporting';break;
      case 'resumed':S.status='running';S.error=null;$('#error-banner').classList.add('hidden');clearPending();Object.values(S.calls).forEach(c=>{if(c.status==='busy')c.status='interrupted';});addActivity(ev,'已从检查点恢复执行，保留成功产出');break;
      case 'execution_policy':S.policy=ev;addActivity(ev,ev.message);break;
      case 'evidence_context':addActivity(ev,ev.message);break;
      case 'operation_restored':addActivity(ev,`${operationLabel(ev.operation)} · 复用成功产出`);break;
      case 'operation_start':addActivity(ev,`${operationLabel(ev.operation)} · 等待预算 ${Math.ceil(ev.timeout_seconds)} 秒`);break;
      case 'operation_done':addActivity(ev,`${operationLabel(ev.operation)}完成 · ${ev.elapsed_seconds} 秒`);break;
      case 'model_attempt':addActivity(ev,`${operationLabel(ev.operation)} · 第 ${ev.attempt} 次请求 · ${{running:'等待模型返回',returned:`已返回（${ev.elapsed_seconds} 秒）`,error:`请求失败（${ev.elapsed_seconds} 秒）`}[ev.status]||ev.status}`);break;
      case 'done':S.status='done';clearPending();Object.values(S.calls).forEach(c=>{if(c.status==='busy')c.status='interrupted';});addActivity(ev,'研究完成 · 报告与核查记录已保存');if(!replay)loadHistory();break;
      case 'cancelled':S.status='cancelled';clearPending();Object.values(S.calls).forEach(c=>{if(c.status==='busy')c.status='interrupted';});addActivity(ev,'任务已终止');if(!replay)loadHistory();break;
      case 'error':S.status='error';S.error=ev;clearPending();Object.values(S.calls).forEach(c=>{if(c.status==='busy')c.status='interrupted';});showError(ev);if(!replay)loadHistory();break;
      case 'stream_end':es?.close();es=null;return;
    }
    if(S.timeline)for(const [k,row]of Object.entries(S.timeline.stages||{}))S.stages[k]={...S.stages[k],...row};
    if(!replay){renderScreen();renderMetrics();document.dispatchEvent(new CustomEvent('osint:effect',{detail:ev}));if(ev.type==='done'){renderContent(true);$('#stage-content').scrollTop=0;}else queueContent(changed);}
  }
  function mergeEvidence(items){const keys=new Set(S.evidence.map(e=>e.subquestion+'|'+e.source));for(const e of items){const key=e.subquestion+'|'+e.source;if(!keys.has(key)){keys.add(key);S.evidence.push(e);}}}
  function operationLabel(key){const parts=String(key||'').split(':');return ({planning:'研究规划',quick:'分析与结论整理',analyzing:'资料分析',reasoning:'证据推理',search:'网页查询',report:'报告处理'})[parts[0]]+(parts.includes('check')?'核查':'')||'当前步骤';}
  function showError(ev){$('#error-banner').classList.remove('hidden');$('#error-banner-title').textContent='研究暂时中断';$('#error-banner-msg').textContent=ev.message||'请求未完成，可从检查点恢复。';$('#error-banner-trace').textContent=ev.traceback||'';}
  function connect(after=-1){es?.close();const rid=S.id,source=new EventSource(`/api/runs/${rid}/events?after=${after}`);es=source;source.onopen=()=>{if(es===source)health(true,'实时连接正常');};source.onmessage=e=>{if(S.id!==rid||es!==source)return;try{ingest(JSON.parse(e.data));}catch(err){console.error(err);}};source.onerror=()=>{if(es===source&&!FINAL.has(S.status))health(false,'连接恢复中 · 后台可能仍在运行');};}
  async function viewRun(rid){const request=++viewRequest;es?.close();es=null;closeAll();try{const d=await api(`/api/runs/${rid}`);if(request!==viewRequest)return;S=fresh();S.id=rid;S.task=d.task;$('#error-banner').classList.add('hidden');for(const e of d.events||[])ingest(e,true);S.status=d.status;if(S.status==='done')$('#error-banner').classList.add('hidden');for(const r of d.reports||[])if(FINAL.has(d.status)||!S.reports[r.type])S.reports[r.type]={markdown:r.markdown,status:'generated'};S.verifications={...(d.summary?.verifications||{}),...S.verifications};S.plan=S.plan||d.summary?.plan;S.knowledge=S.knowledge||d.summary?.knowledge;if(!S.evidence.length)S.evidence=d.summary?.evidence||[];if(!S.findings.length)S.findings=d.summary?.findings||[];S.sources=d.search_sources||S.sources;S.resumeInfo=d.resume_info;setTimeline(d.timeline);if(!d.events?.length){S.plan=d.summary?.plan;S.evidence=d.summary?.evidence||[];S.knowledge=d.summary?.knowledge;S.findings=d.summary?.findings||[];for(const r of d.reports||[])S.reports[r.type]={markdown:r.markdown,status:'generated'};S.verifications=d.summary?.verifications||{};}if(d.pending_sources)S.pending={kind:'sources',payload:d.pending_sources};else if(d.pending_gate)S.pending={kind:'gate',payload:d.pending_gate};else if(d.pending_plan)S.pending={kind:'plan',payload:{plan:d.pending_plan}};else if(FINAL.has(S.status))S.pending=null;S.view=S.pending?.kind==='sources'?'collecting':current();renderScreen();renderMetrics();renderContent(true);if(!FINAL.has(S.status))connect((d.events||[]).length-1);if(S.pending)openPending();loadHistory();}catch(error){toast(error.message);}}
  function selectedModels(){return Object.fromEntries($$('#role-grid select').map(s=>[s.dataset.role,s.value]));}
  async function startRun(){if(!modelsLoaded||S.id&&!FINAL.has(S.status))return;const task=$('#task-input').value.trim();if(!task){toast('请输入研究任务');return;}const roles=selectedModels();$('#btn-run').disabled=true;try{const d=await api('/api/runs',{task,model_id:roles.planner,role_models:$('#model-mode').value==='recommended'?null:roles,execution_mode:$('#execution-mode').value,auto_confirm:false});++viewRequest;S=fresh();S.id=d.run_id;S.task=task;S.status='running';$('#error-banner').classList.add('hidden');renderScreen();renderMetrics();renderContent(true);connect();loadHistory();}catch(error){toast(error.message);runningUI();}}
  async function cancelRun(){if(!S.id)return;try{await api(`/api/runs/${S.id}/cancel`,{});await viewRun(S.id);}catch(error){toast(error.message);}}
  async function resumeRun(){if(!S.id||!modelsLoaded)return;$('#btn-resume').disabled=true;try{const change=$('#resume-change-models').checked;await api(`/api/runs/${S.id}/resume`,{use_recommended:false,role_models:change?selectedModels():null});await viewRun(S.id);}catch(error){toast(error.message);}finally{runningUI();}}
  async function loadHistory(){try{const d=await api('/api/runs');$('#history-list').innerHTML=(d.runs||[]).map(h=>`<button class="history-item ${h.id===S.id?'active':''}" data-run="${esc(h.id)}"><div class="history-task">${esc(h.task)}</div><div class="history-meta"><i class="status-dot ${esc(h.status)}"></i>${esc(LABELS[h.status]||h.status)} · ${h.report_count||0} 报告 · ${esc(new Date(h.created_at).toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'}))}</div></button>`).join('')||'<div class="empty">暂无研究记录</div>';$$('[data-run]').forEach(b=>b.addEventListener('click',()=>viewRun(b.dataset.run)));}catch{toast('研究记录读取失败');}}
  function closeAll(){$$('.modal-overlay').forEach(m=>m.classList.add('hidden'));}
  function openModal(id){openTrigger=document.activeElement;closeAll();const modal=$('#'+id);modal.classList.remove('hidden');modal.querySelector('button,input,textarea')?.focus();}
  function closeModal(id){$('#'+id).classList.add('hidden');if(openTrigger?.isConnected)openTrigger.focus();}
  function openPending(){if(!S.pending)return;const {kind,payload}=S.pending;if(kind==='plan'){$('#confirm-plan-body').innerHTML=renderPlan(payload.plan||S.plan);openModal('confirm-modal');}if(kind==='sources'){$('#sources-options').innerHTML=(payload.options||[]).map(p=>`<label class="source-option"><input type="checkbox" value="${esc(p.id)}" ${(payload.selected||[]).includes(p.id)?'checked':''}><span><strong>${esc(p.name)}<span class="source-cost ${p.free?'':'paid'}">${p.free?'免 Key · 免费档':'可能计费'}</span></strong><small>${esc(p.description)}</small></span></label>`).join('');$('#sources-error').textContent='';openModal('sources-modal');}if(kind==='gate'){$('#gate-modal-sub').textContent='请检查问题与影响，再选择重做或接受问题继续。';$('#gate-body').innerHTML=verification(payload);openModal('gate-modal');}}
  async function submit(button,url,body,modal){const rid=S.id;button.disabled=true;try{await api(`/api/runs/${rid}/${url}`,body);if(S.id===rid)closeModal(modal);}catch(error){if(modal==='sources-modal')$('#sources-error').textContent=error.message;else toast(error.message);}finally{button.disabled=false;}}
  function downloadReport(){if(S.id)window.location.assign(`/api/runs/${S.id}/report.md`);}
  function inline(text){return esc(text).replace(/\[([^\]]+)\]\(([^)]+)\)/g,(_,label,url)=>safeURL(url)?`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`:label).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
  function markdown(src){const lines=String(src||'').split(/\r?\n/),out=[];let list='',code=false,buffer=[];const close=()=>{if(list){out.push(`</${list}>`);list='';}};for(let i=0;i<lines.length;i++){const t=lines[i].trim();if(t.startsWith('```')){close();if(code){out.push('<pre><code>'+esc(buffer.join('\n'))+'</code></pre>');buffer=[];}code=!code;continue;}if(code){buffer.push(lines[i]);continue;}if(!t){close();continue;}if(t.startsWith('|')&&i+1<lines.length&&/^\|?[\s:|\-]+\|$/.test(lines[i+1].trim())){close();const cells=x=>x.replace(/^\||\|$/g,'').split('|');out.push('<table><thead><tr>'+cells(t).map(x=>'<th>'+inline(x)+'</th>').join('')+'</tr></thead><tbody>');i+=2;for(;i<lines.length&&lines[i].trim().startsWith('|');i++)out.push('<tr>'+cells(lines[i].trim()).map(x=>'<td>'+inline(x)+'</td>').join('')+'</tr>');i--;out.push('</tbody></table>');continue;}const h=t.match(/^(#{1,6})\s+(.*)$/);if(h){close();out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);continue;}const li=t.match(/^([-*+]\s+|\d+[.)]\s+)(.*)$/);if(li){const kind=/^\d/.test(li[1])?'ol':'ul';if(kind!==list){close();out.push(`<${kind}>`);list=kind;}out.push('<li>'+inline(li[2])+'</li>');continue;}close();out.push(t.startsWith('>')?'<blockquote>'+inline(t.slice(1))+'</blockquote>':/^---+$/.test(t)?'<hr>':'<p>'+inline(t)+'</p>');}close();if(buffer.length)out.push('<pre><code>'+esc(buffer.join('\n'))+'</code></pre>');return out.join('');}
  function bind(){
    $('#btn-history').onclick=()=>{const open=document.body.classList.toggle('mobile-history-open');$('#btn-history').setAttribute('aria-expanded',String(open));$('#btn-history').textContent=open?'收起研究记录 ↑':'展开研究记录 ↓';};
    $('#btn-run').onclick=startRun;$('#btn-cancel').onclick=cancelRun;$('#btn-resume').onclick=resumeRun;$('#btn-refresh-history').onclick=loadHistory;
    $('#resume-change-models').onchange=runningUI;
    $('#btn-topology').onclick=()=>{document.dispatchEvent(new CustomEvent('osint:topology'));openModal('topology-modal');};
    document.addEventListener('osint:select-stage',event=>{if(ROLES.some(r=>r[0]===event.detail)){closeModal('topology-modal');selectStage(event.detail,false);}});
    $$('.chip').forEach(b=>b.onclick=()=>{$('#task-input').value=b.dataset.example;$('#task-input').dispatchEvent(new Event('input',{bubbles:true}));});
    $('#btn-follow').onclick=()=>S.status==='done'?openFinalReport():selectStage(current(),true);$('#btn-focus').onclick=()=>{const yes=document.body.classList.toggle('focus-mode');$('#btn-focus').setAttribute('aria-pressed',String(yes));$('#btn-focus').textContent=yes?'退出专注':'专注大屏';};
    $('#btn-open-report').onclick=openFinalReport;$('#btn-download-final').onclick=downloadReport;
    $('#btn-motion').onclick=()=>{const yes=document.body.classList.toggle('reduced-motion');$('#btn-motion').setAttribute('aria-pressed',String(yes));localStorage.setItem('osint-reduced-motion',String(yes));};
    if(localStorage.getItem('osint-reduced-motion')==='true'){document.body.classList.add('reduced-motion');$('#btn-motion').setAttribute('aria-pressed','true');}
    document.addEventListener('visibilitychange',()=>document.body.classList.toggle('page-hidden',document.hidden));
    $('#btn-pending').onclick=openPending;$('#btn-new-content').onclick=()=>{renderContent(true);$('#stage-content').scrollTop=$('#stage-content').scrollHeight;};$('#logs-panel').ontoggle=()=>{if($('#logs-panel').open)renderLogs();};
    $('#btn-confirm-ok').onclick=()=>submit($('#btn-confirm-ok'),'confirm',{feedback:''},'confirm-modal');$('#btn-confirm-edit').onclick=()=>{const feedback=$('#confirm-feedback').value.trim();if(!feedback){toast('请输入修改意见');return;}submit($('#btn-confirm-edit'),'confirm',{feedback},'confirm-modal');};
    $('#btn-close-sources').onclick=()=>{if(S.pending?.kind==='sources')S.pending.payload.selected=$$('#sources-options input:checked').map(e=>e.value);closeModal('sources-modal');};
    $('#btn-sources-start').onclick=()=>{const sources=$$('#sources-options input:checked').map(e=>e.value);if(!sources.length){$('#sources-error').textContent='请至少选择一个搜索 MCP。';return;}submit($('#btn-sources-start'),'sources',{request_id:S.pending?.payload.request_id,sources},'sources-modal');};
    $('#btn-gate-redo').onclick=()=>submit($('#btn-gate-redo'),'gate',{decision:'redo',feedback:$('#gate-feedback').value.trim()},'gate-modal');$('#btn-gate-continue').onclick=()=>submit($('#btn-gate-continue'),'gate',{decision:'continue',feedback:$('#gate-feedback').value.trim()},'gate-modal');
    $$('[data-close]').forEach(b=>b.onclick=()=>closeModal(b.dataset.close));
    $$('.modal-overlay').forEach(m=>m.addEventListener('keydown',e=>{if(e.key==='Escape'){if(m.id==='sources-modal')$('#btn-close-sources').click();else closeModal(m.id);}if(e.key==='Tab'){const items=[...m.querySelectorAll('button:not(:disabled),input,textarea,select,a[href]')].filter(el=>el.getClientRects().length&&el.tabIndex!==-1),first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}}));
    $('#btn-copy-error').onclick=async()=>{try{await navigator.clipboard.writeText(JSON.stringify(S.error,null,2));toast('诊断信息已复制');}catch{toast('复制失败，请展开诊断信息手动复制');}};
  }
  function applyModelMode(reset=false){const recommended=$('#model-mode').value==='recommended';$$('#role-grid select').forEach(sel=>{if(reset&&modelDefaults[sel.dataset.role])sel.value=modelDefaults[sel.dataset.role];sel.disabled=recommended;sel.title=modelReasons[sel.dataset.role]||'';const note=sel.parentElement.querySelector('small');if(note)note.textContent=recommended?modelReasons[sel.dataset.role]||'':'手动配置：使用您选择的模型。';});}
  async function updateRecommendation(){
    const request=++recommendationRequest,mode=$('#execution-mode').value;
    try{
      let d;
      if(mode!=='auto'&&modelProfiles[mode])d={mode,label:({quick:'简明解答',standard:'标准研究',deep:'深入研究'})[mode],role_models:modelProfiles[mode],reasons:profileReasons[mode]};
      else d=await api(`/api/model-recommendation?task=${encodeURIComponent($('#task-input').value.trim())}&execution_mode=${mode}`);
      if(request!==recommendationRequest)return;
      modelDefaults=d.role_models;modelReasons=d.reasons||{};
      applyModelMode($('#model-mode').value==='recommended');
      $('#mode-description').textContent=`${mode==='auto'?'自动匹配：':''}${d.label} · 已配置对应岗位模型${$('#model-mode').value==='custom'?'（当前保留手动选择）':''}。`;
    }catch{if(request===recommendationRequest)toast('模型推荐更新失败，请重新选择研究深度后重试');}
  }
  function openFinalReport(){selectStage('reporting',false);$('#stage-content').scrollTop=0;$('.result-panel').scrollIntoView({behavior:'instant',block:'start'});}
  async function init(){buildNav();bind();setInterval(tick,1000);renderScreen();$('#model-mode').onchange=()=>applyModelMode($('#model-mode').value==='recommended');$('#btn-reset-models').onclick=()=>{$('#model-mode').value='recommended';applyModelMode(true);toast('已恢复推荐岗位模型');};$('#execution-mode').onchange=()=>{$('#mode-description').textContent=({auto:'简单释义合并分析与推理；复杂任务保留完整研究流程。',quick:'工作预算 5 分钟，1 份简明报告，保留最终事实核查。',standard:'工作预算 8 分钟，按需生成报告，避免逐阶段重复核查。',deep:'工作预算 12 分钟，更多证据与定向核查。人工等待不计入预算。'})[$('#execution-mode').value];};await Promise.allSettled([api('/api/health').then(d=>health(d.has_key,d.has_key?'服务已连接':'缺少模型 API Key')).catch(()=>health(false,'服务离线')),api('/api/models').then(d=>{modelDefaults=d.role_defaults||{};modelReasons=d.role_reasons||{};modelProfiles=d.role_profiles||{};profileReasons=d.profile_reasons||{};$$('#role-grid select').forEach(sel=>{sel.innerHTML=(d.models||[]).map(m=>`<option value="${esc(m.id)}" ${m.available===false?'disabled':''}>${esc(m.label)}${m.available===false?' · 当前额度不可用':''}</option>`).join('');const note=document.createElement('small');note.textContent=modelReasons[sel.dataset.role]||'';sel.parentElement.append(note);});$('#model-sources').innerHTML=(d.sources||[]).map(s=>`<a href="${esc(safeURL(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join(' · ');applyModelMode(true);modelsLoaded=true;runningUI();updateRecommendation();}).catch(()=>toast('无法载入岗位配置，请刷新后重试'))]);loadHistory();}
  document.addEventListener('DOMContentLoaded',init);
  document.addEventListener('DOMContentLoaded',()=>{
    $('#execution-mode').addEventListener('change',updateRecommendation);
    $('#task-input').addEventListener('input',()=>{clearTimeout(recommendationTimer);if($('#execution-mode').value==='auto')recommendationTimer=setTimeout(updateRecommendation,250);});
  });
})();
