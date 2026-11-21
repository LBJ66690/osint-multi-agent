/* 情报指挥台：事件数据与视图选择分离，动画由真实状态驱动。 */
(() => {
  'use strict';
  const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
  const ROLES = [
    ['planning','规划官','◎','PLANNING','理解目标，拆解研究问题'],['collecting','收集官','⌕','WEB RESEARCH','查询公开网页，汇入相关来源'],
    ['analyzing','分析官','▤','ANALYSIS','归并实体，提炼观点与案例'],['reasoning','推理官','◇','SYNTHESIS','对照支持依据与反证，形成发现'],
    ['reporting','最终报告','▧','FINAL REPORT','阅读研究结论、依据与来源'],['verifying','核查记录','✓','QUALITY REVIEW','对照资料与发现，检查输出质量']
  ];
  let modelDefaults={}, modelReasons={}, modelProfiles={}, profileReasons={}, modelsLoaded=false, modelsLoadFailed=false, startPending=false, recommendationRequest=0, recommendationTimer;
  let sagEnabled=false, sagDraft=null;
  const idleWorkspace=$('#stage-content').innerHTML,idleActivity=$('#activity-list').innerHTML;
  let serviceState={ok:null,text:'正在检查本地服务'};
  const LABELS = {pending:'尚未开始',running:'执行中',ready:'已产出',done:'已完成',error:'执行失败',cancelled:'已终止',waiting:'等待决策',awaiting_confirm:'等待确认计划',awaiting_sources:'等待选择 MCP',awaiting_gate:'等待核查决策'};
  const PROVIDERS = {parallel:'Parallel',you:'You.com',bailian:'百炼'};
  const FINAL = new Set(['done','error','cancelled']);
  const handoff = import('/static/travel/workspace-draft.js');
  let draftId = new URLSearchParams(location.search).get('draft');
  let travelDraft = null;
  let readerRun = null;
  let readerVersion = 0;
  let S, es = null, renderTimer, viewRequest = 0, clockOffset = 0, openTrigger;
  const fresh = () => ({id:null,task:'',status:'pending',view:'planning',follow:true,timeline:null, stages:Object.fromEntries(ROLES.map(r=>[r[0],{status:'pending',label:'',model:''}])),
    strategy:'legacy',decisions:[],plan:null,evidence:[],knowledge:null,findings:[],reports:{},reportTab:null,verifications:{},gates:{},calls:{},activities:[],logs:[],usage:{},resources:null,quality:null,conflicts:[],sources:[],completedQuestions:new Set(),pending:null,scroll:{},dirty:false,lastTs:null,error:null});
  S = fresh();
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  function checkPassed(v){return v?.verdict==='pass'&&!(v.issues||[]).length;}
  function safeURL(s){try{const u=new URL(s);return ['http:','https:'].includes(u.protocol)?u.href:'';}catch{return '';}}
  function duration(ms){if(ms==null)return '—';const n=Math.max(0,Math.floor(ms/1000));return n>=3600?`${String(Math.floor(n/3600)).padStart(2,'0')}:${String(Math.floor(n/60)%60).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`:`${String(Math.floor(n/60)).padStart(2,'0')}:${String(n%60).padStart(2,'0')}`;}
  function tok(n){return n>=1000?(n/1000).toFixed(1)+'k':String(n||0);}
  function toast(text){$('#toast').textContent=text;$('#toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').classList.add('hidden'),4500);}
  async function api(url,body){const r=await fetch(url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(r.status===401)window.OSINTAuth?.expired();let data={};try{data=await r.json();}catch{}if(!r.ok)throw new Error(typeof data.detail==='string'?data.detail:`请求失败 (${r.status})`);return data;}
  function renderReadiness(){
    if(S.activities.length)return;
    if(S.id){$('#activity-list').innerHTML=`<div class="empty">${FINAL.has(S.status)?'此任务没有保存活动记录，可查看阶段产出与报告。':'等待当前任务的第一条执行事件。'}</div>`;return;}
    if(!$('#activity-list .readiness-list'))$('#activity-list').innerHTML=idleActivity;
    $('#readiness-service').textContent=serviceState.text;
    $('#readiness-service').closest('.readiness-row').querySelector('.readiness-symbol').textContent=serviceState.ok===null?'◎':serviceState.ok?'✓':'!';
    $('#readiness-models').textContent=modelsLoaded?'模型配置已载入 · 尚未调用':modelsLoadFailed?'模型配置载入失败 · 请刷新重试':'正在读取模型配置';
  }
  function health(ok,text){serviceState={ok,text};$('#health-pill').classList.toggle('ok',ok);$('#health-pill').classList.toggle('bad',!ok);$('#health-text').textContent=text;renderReadiness();}
  function runningUI(){const active=!!S.id&&!FINAL.has(S.status), resumable=['error','cancelled'].includes(S.status);$('#btn-new-research').disabled=active||startPending;$('#btn-run').disabled=active||startPending||!modelsLoaded||(!!draftId&&!travelDraft);$('#btn-run-text').textContent=startPending?'正在创建研究…':active?'研究进行中':modelsLoaded?'开始研究':'模型配置载入中';$('#btn-cancel').classList.toggle('hidden',!active);$('#btn-resume').classList.toggle('hidden',!resumable);$('#btn-resume').disabled=!modelsLoaded||!!S.resumeInfo?.unavailable;$('#resume-help').classList.toggle('hidden',!resumable);$('#resume-model-option').classList.toggle('hidden',!resumable);$('#btn-resume').textContent=$('#resume-change-models').checked?'↻ 改用上方模型，从断点继续':'↻ 从中断处继续（保留原模型）';if(resumable)$('#resume-help').textContent=S.resumeInfo?.unavailable?S.resumeInfo.message:S.resumeInfo?`复用：${S.resumeInfo.reuse_labels.join('、')||'已保存操作'}。从${S.resumeInfo.next_label}继续，成功产出不重做。`:'从失败步骤继续，复用成功产出；本次工作预算重新计算，历史计时保留。';}
  function setTimeline(t){if(!t)return;S.timeline=structuredClone(t);clockOffset=t.server_ms-Date.now();}
  function current(){return S.timeline?.current_stage||S.current||'planning';}
  function elapsed(stage,now){const row=S.timeline?.stages?.[stage];if(!row)return null;return row.elapsed_ms+(row.active_since_ms!=null&&!FINAL.has(S.status)?Math.max(0,now-row.active_since_ms):0);}
  function tick(){
    const t=S.timeline,now=Date.now()+clockOffset,wait=!!t?.wait_started_ms;
    $('#total-time').textContent=t?.started_ms?duration((t.ended_ms||now)-t.started_ms):'—';
    const waitStage=S.pending?.kind==='sources'?'collecting':S.pending?.kind==='plan'?'planning':S.pending?.payload.stage;
    const waitingHere=wait&&S.view===waitStage;
    const stageTime=waitingHere?duration(now-t.wait_started_ms):elapsed(S.view,now)==null?'—':duration(elapsed(S.view,now));
    $('#stage-time').textContent=stageTime;
    $('.hero-clock').classList.toggle('has-hours',stageTime.length>5);
    $('#clock-label').textContent=waitingHere?'等待您的操作':'阶段累计执行';
    $('#wait-time').textContent=wait?`${t.wait_reason}${waitingHere?'':` · ${duration(now-t.wait_started_ms)}`}`:'';
    $('#clock-caption').textContent=!S.id?'运行后开始计时':S.view!==current()?'正在查看历史阶段':FINAL.has(S.status)?'本次阶段耗时已固定':wait?'人工等待单独计时':'累计执行 · 包含网络等待';
    for(const r of ROLES){const node=$(`#stage-nav [data-stage="${r[0]}"] .nav-time`);if(node)node.textContent=duration(elapsed(r[0],now));}
    $('#event-age').textContent=S.lastTs?`${duration(now-S.lastTs)} 前更新`:'—';
  }
  function buildNav(){ $('#stage-nav').innerHTML=ROLES.map((r,i)=>`<button class="nav-stage" data-stage="${r[0]}" aria-label="查看${r[1]}"><span class="nav-icon">${r[2]}</span><span><span class="nav-name">${r[1]}</span><span class="nav-status">尚未开始</span></span><span class="nav-time">—</span></button>`).join('');
    $$('#stage-nav button').forEach(b=>b.addEventListener('click',()=>selectStage(b.dataset.stage,false))); }
  function selectStage(stage,follow){S.scroll[S.view]=$('#stage-content').scrollTop;S.view=stage;S.follow=follow;S.dirty=false;renderScreen();renderContent(true);$('#stage-content').scrollTop=S.scroll[stage]||0;}
  function renderAdaptive(){
    $('#adaptive-decisions').classList.toggle('hidden',S.strategy!=='adaptive');
    const actions={ADD_SEARCH:'继续补搜',REFINE_QUERY:'调整搜索词',STOP_SEARCH:'停止搜索',SWITCH_SOURCE:'切换已选渠道',VERIFY_CONFLICT:'定向查证',RETRY_QUERY:'重试未成功查询',COLLECT_FEEDBACK:'分析反馈补搜',FEEDBACK_RESULT:'补搜资料返回',FEEDBACK_FAILED:'补搜未成功'};
    const levels={insufficient:'不足',weak:'较弱',sufficient:'检索覆盖较充分 · 独立性仍待确认'};
    $('#adaptive-decision-list').innerHTML=S.decisions.slice(-20).reverse().map(e=>{const state=e.metrics?.coverage;return `<article class="decision-card"><b>${esc(actions[e.action]||e.action)}</b><time>${esc(e.ts?new Date(e.ts).toLocaleTimeString():'')}</time><p>子问题：${esc(e.subquestion)}</p>${typeof state==='string'?`<p>证据状态：${esc(levels[state]||state)}</p>`:''}<p>${esc(e.reason)}</p></article>`;}).join('')||'<p>开始检索后显示每个自动动作和原因。</p>';
  }
  function renderScreen(){
    renderAdaptive();
    const delivered=S.status==='done'&&Object.keys(S.reports).length>0;
    if(delivered&&!S.completionPresented){S.completionPresented=true;if(S.follow){S.view='reporting';S.follow=false;}}
    const readingReport=delivered&&S.view==='reporting';
    document.body.classList.toggle('report-reading',readingReport);
    $('#report-ready').classList.toggle('hidden',!delivered);
    const warningCount=Object.values(S.verifications).filter(v=>!checkPassed(v)).length;
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
    for(const r of ROLES){const b=$(`#stage-nav [data-stage="${r[0]}"]`),status=S.stages[r[0]].status;b.classList.toggle('selected',S.view===r[0]);b.classList.toggle('live',r[0]===current()&&active);b.classList.toggle('done',status==='done');b.setAttribute('aria-pressed',String(S.view===r[0]));b.querySelector('.nav-status').textContent=(r[0]===current()&&S.pending)?'等待您操作':LABELS[status]||status;}
    const p=S.pending;$('#pending-action').classList.toggle('hidden',!p);$('#pending-text').textContent=p?({plan:'计划已生成，等待您确认',sources:'规划已确认，等待您选择网页搜索 MCP',gate:'核查发现问题，等待您决定是否重做'}[p.kind]):'';
    tick();runningUI();renderChannels();renderReadiness();
    document.dispatchEvent(new CustomEvent('osint:view',{detail:{id:S.id,task:S.task,status:S.status,current:current(),view:S.view,pending:!!S.pending,stages:S.stages,mode:S.policy?.mode,strategy:S.strategy,sources:S.sources,roles:S.policy?.role_models||{},reports:Object.keys(S.reports).length}}));
  }
  function changeMetric(id,value){const el=$(id);if(el.textContent!==String(value)){el.textContent=value;el.classList.remove('updated');void el.offsetWidth;el.classList.add('updated');}}
  function renderMetrics(){
    const calls=Object.values(S.calls),done=calls.filter(c=>c.status!=='busy').length,failed=calls.filter(c=>c.status==='error').length;
    changeMetric('#metric-evidence',S.evidence.length);changeMetric('#metric-questions',S.plan?`${S.completedQuestions.size} / ${S.plan.subquestions?.length||0}`:'—');changeMetric('#metric-requests',done);
    $('#metric-request-note').textContent=`${calls.filter(c=>c.status==='busy').length} 个等待 · ${failed} 个失败`;
    const records=Object.values(S.usage), unknown=records.some(u=>u.unknown), n=records.reduce((a,b)=>a+(b.total||0),0), r=S.resources;
    changeMetric('#metric-tokens',r?(r.total==null?((r.model_calls||r.history_incomplete)?'未知':'—'):tok(r.total)):(unknown?'未知':records.length?tok(n):'—'));
    $('#metric-token-note').textContent=r?.history_incomplete?'旧任务此前用量未记录 · 总量未知':r?.unknown_usage_calls?`${r.unknown_usage_calls} 次未返回完整用量 · 已知 ${tok(r.known_total||0)}`:'供应商上报 · 缺失显示未知';
    renderResources();
  }
  function renderResources(){
    const r=S.resources, names={planner:'规划官',collector:'收集官',analyst:'分析官',reasoner:'推理官',reporter:'报告官',verifier:'核查官',unknown:'未标注岗位'};
    $('#resource-brief').textContent=r?`${r.model_calls} 次已记录模型请求 · ${r.search_calls} 次搜索 · ${r.supplemental_searches} 次补搜${r.history_incomplete?' · 历史统计不完整':''}`:'尚无逐请求统计记录';
    const count=n=>n==null?'未知':String(n);
    $('#resource-details').innerHTML=r?`<div class="resource-table-wrap"><table><thead><tr><th>岗位</th><th>请求</th><th>输入 Token</th><th>输出 Token</th><th>请求内重试</th><th>失败 / 格式错误</th></tr></thead><tbody>${Object.entries(r.roles||{}).map(([role,v])=>`<tr><th>${esc(names[role]||role)}</th><td>${count(v.model_calls)}</td><td>${count(v.prompt)}</td><td>${count(v.completion)}</td><td>${count(v.model_retries)}</td><td>${count(v.model_failures)} / ${count(v.parse_failures)}</td></tr>`).join('')}</tbody></table></div><p>搜索失败 ${count(r.search_failures)} 次；搜索重试 ${count(r.search_retries)} 次；恢复复用 ${count(r.restored_operations)} 项；操作重试 ${count(r.operation_retries)} 次（含手动恢复）。各阶段耗时见右侧阶段导航。</p>${(r.stop_reasons||[]).map(x=>`<p>停止原因：${esc(x)}</p>`).join('')}`:'旧任务或尚未发起请求：没有可靠数据，不能据此认定零消耗。';
  }
  function evidenceNotice(){
    const q=S.quality;if(!q)return '';
    const origins=q.provenance||[], shared=origins.filter(p=>p.independence_status==='shared_origin');
    return `<div class="evidence-notice"><strong>检索资料 ≠ 已证实事实</strong><p>${esc(q.raw_count)} 条资料 · ${esc(q.nonduplicate_count)} 条非重复内容；来源独立性未确认。${shared.length?`${shared.length} 条资料存在明显转载、同页或重复关系，不能当作独立佐证。`:''}</p>${S.conflicts.map(c=>`<p>待核对差异：${esc(c.topic||'关键事实')} · ${esc(c.reason)}</p>`).join('')}${shared.length?`<details><summary>查看重复或同源原因</summary>${shared.slice(0,12).map(p=>`<p>${esc(p.source)}：${esc(p.reason)}</p>`).join('')}</details>`:''}</div>`;
  }

  function renderChannels(){const ids=S.sources.filter(p=>PROVIDERS[p]);$('#channels-panel').classList.toggle('hidden',!ids.length);$('#channels-grid').innerHTML=ids.map(id=>{const calls=Object.values(S.calls).filter(c=>c.tool===id),busy=calls.some(c=>c.status==='busy'),fails=calls.filter(c=>c.status==='error').length,success=calls.filter(c=>c.status==='success'),unknown=success.some(c=>!Number.isInteger(c.result_count)),count=success.reduce((a,c)=>a+(Number.isInteger(c.result_count)?c.result_count:0),0),status=busy?'busy':fails?'error':calls.length?'success':'';const text=busy?'请求 / 等待响应中':!calls.length?'等待请求':count?`返回${unknown?'至少 ': ' '}${count} 条 · ${fails} 次失败`:unknown?`已返回内容 · 条数未统计${fails?` · ${fails} 次失败`:''}`:success.length?`已返回 · 未找到网页${fails?` · ${fails} 次失败`:''}`:fails?'接口请求失败':'请求已中断';const node=$(`[data-source="${id}"]`),beam=$(`[data-beam="${id}"]`);node.className='source-node '+status;beam.classList.add('selected');beam.classList.toggle('busy',busy);return `<div class="channel ${status}"><strong>${PROVIDERS[id]}</strong><span>${text}</span></div>`;}).join('');for(const id of Object.keys(PROVIDERS)){if(!ids.includes(id)){$(`[data-source="${id}"]`).className='source-node hidden';$(`[data-beam="${id}"]`).setAttribute('class','');}}}
  function addActivity(ev,text,error=false){S.lastTs=Date.parse(ev.ts)||Date.now();S.activities.unshift({text,ts:ev.ts,error});S.activities=S.activities.slice(0,40);$('#latest-activity').textContent=text;$('#activity-list').innerHTML=S.activities.slice(0,4).map((a,i)=>`<div class="activity-row ${a.error?'error':''} ${i===0?'new':''}"><span class="activity-dot"></span><span>${esc(a.text)}</span><time>${a.ts?new Date(a.ts).toLocaleTimeString('zh-CN',{hour12:false}):''}</time></div>`).join('');}
  function logEvent(ev){S.logs.push(ev);if(S.logs.length>300)S.logs.shift();if($('#logs-panel').open)renderLogs();}
  function renderLogs(){$('#full-logs').innerHTML=S.logs.slice(-60).reverse().map(e=>`<div><strong>${esc(e.tool||e.type)}</strong><pre>${esc(JSON.stringify(e,null,2))}</pre></div>`).join('');}
  function queueContent(stage){if(S.view!==stage)return;clearTimeout(renderTimer);renderTimer=setTimeout(()=>renderContent(),100);}
  function renderContent(force=false){const box=$('#stage-content'),near=box.scrollHeight-box.scrollTop-box.clientHeight<100,reading=box.contains(document.activeElement)&&document.activeElement?.closest('details');if(!force&&(!near||reading)){S.dirty=true;$('#btn-new-content').classList.remove('hidden');return;}const previous=box.scrollTop;box.innerHTML=contentFor(S.view);if(force){box.classList.remove('enter');void box.offsetWidth;box.classList.add('enter');}if(!force&&near&&S.view==='reporting')box.scrollTop=box.scrollHeight;else box.scrollTop=previous;S.dirty=false;$('#btn-new-content').classList.add('hidden');box.querySelectorAll('[data-report]').forEach(b=>b.addEventListener('click',()=>{S.reportTab=b.dataset.report;renderContent(true);}));box.querySelector('[data-open-pending]')?.addEventListener('click',openPending);box.querySelector('[data-download]')?.addEventListener('click',downloadReport);box.querySelector('[data-chat-report]')?.addEventListener('click',askReport);box.querySelector('[data-sag-import]')?.addEventListener('click',openSagImport);}
  function tags(items){return `<div class="tags">${(items||[]).map(x=>`<span class="tag">${esc(x)}</span>`).join('')}</div>`;}
  function renderPlan(p){return p?`<div class="plan-goal">${esc(p.goal)}</div>${(p.subquestions||[]).map((q,i)=>`<div class="subq" style="animation-delay:${Math.min(i,5)*55}ms"><div class="subq-q">${i+1}. ${esc(q.question)}</div>${tags(q.search_queries)}</div>`).join('')}${tags(p.report_types)}`:'<div class="empty">计划尚未生成。</div>';}
  function verification(v){return `<div class="verify-sum"><span class="verdict ${checkPassed(v)?'pass':'fail'}">${checkPassed(v)?'材料检查通过':'待复核'}</span>${esc(v.summary||'')}</div><p class="verification-scope">结构、引用匹配及给定片段对照；不代表已完成外部独立事实核验。</p>${(v.issues||[]).map(i=>`<div class="verify-issue"><span class="verdict">${esc(i.severity)} · ${esc(i.verdict)}</span><strong>${esc(i.claim)}</strong><p>${esc(i.explanation)}</p></div>`).join('')}`;}
  function contentFor(stage){
    if(!S.id)return idleWorkspace;
    let html='';const pending=S.pending&&((stage==='planning'&&S.pending.kind==='plan')||(stage==='collecting'&&S.pending.kind==='sources')||(S.pending.kind==='gate'&&(S.pending.payload.stage===stage||stage==='verifying')));
    if(pending)html+='<div class="source-pending"><strong>等待您的操作</strong><p>执行已在此等待，您可以先查看已有结果。</p><button class="primary-button" data-open-pending>打开待办</button><button class="hidden" id="btn-reopen-sources" data-open-pending>选择搜索通道</button></div>';
    if(stage==='planning')html+=renderPlan(S.plan);
    if(stage==='collecting')html+=evidenceNotice()+S.evidence.map(e=>`<article class="evidence-item"><div class="evidence-meta">${esc(PROVIDERS[e.provider]||e.provider||'公开来源')} · ${esc(e.subquestion||'')}</div><a class="evidence-title" href="${esc(safeURL(e.source))}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a><p>${esc((e.content||'').slice(0,240))}</p><details><summary>查看检索片段与来源</summary><p>${esc(e.content)}</p><a href="${esc(safeURL(e.source))}" target="_blank" rel="noopener noreferrer">${esc(e.source)}</a></details></article>`).join('')||'<div class="empty">搜索结果将在通道返回后汇入这里。</div>';
    if(stage==='analyzing'&&S.knowledge){const k=S.knowledge;html+=`<div class="section-kicker">实体与别名归并</div><div class="alias-list">${Object.entries(k.aliases||{}).map(([a,b])=>`<span>${esc(a)} → ${esc(b)}</span>`).join('')||'暂无别名映射'}</div>${(k.viewpoints||[]).map(v=>`<article class="finding"><h3>${esc(v.topic)} <span class="tag">${esc(v.sentiment)}</span></h3><p>${esc(v.summary)}</p>${tags(v.examples)}</article>`).join('')}`;}
    if(stage==='reasoning')html+=S.findings.map((f,i)=>`<article class="finding"><h3>${i+1}. ${esc(f.claim)}</h3><p class="finding-state">${esc({supported:'给定材料支持 · 尚未独立核实',inference:'模型推断',uncertain:'存在待核对差异',unconfirmed:'无法确认'}[f.status]||'结论状态未标注')} · ${esc(f.uncertainty_reason||'来源匹配不代表事实已核实')}</p><details open><summary>支持依据</summary><ul>${(f.evidence||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details><details><summary>反证 / 不同声音</summary><ul>${(f.counter_evidence||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul></details><div class="tags">${(f.sources||[]).map(x=>`<a class="tag" href="${esc(safeURL(x))}" target="_blank" rel="noopener noreferrer">${esc(x)}</a>`).join('')}</div></article>`).join('');
    if(stage==='reporting'){const types=Object.keys(S.reports);if(types.length){if(!types.includes(S.reportTab))S.reportTab=types[0];const r=S.reports[S.reportTab];html+=`<div class="report-tabs">${types.map(t=>`<button data-report="${esc(t)}" class="${t===S.reportTab?'selected':''}">${esc(t)}</button>`).join('')}${S.status==='done'?'<button data-chat-report>就这份报告提问 ↗</button><button data-download>↓ 下载报告</button>':''}${sagEnabled&&S.status==='done'?'<button data-sag-import>保存片段到 SAG</button>':''}</div>${r.notice?`<div class="report-notice">${esc(r.notice)}</div>`:''}<div class="report-md">${markdown(r.markdown)}${r.status==='streaming'?'<span class="write-cursor"></span>':''}</div>`;}}
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
    if(S.domain==='guizhou_travel'){
      const stage=ev.type==='report'?'reporting':ev.stage;
      if(stage && S.stages[stage] && stage!==S.current){
        if(S.current && S.stages[S.current].status==='running')S.stages[S.current].status='done';
        S.current=stage;S.stages[stage].status='running';
        if(S.follow)S.view=stage;
      }
      if(ev.type==='done' && S.current)S.stages[S.current].status='done';
    }
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
      case 'usage':{const u=S.usage[ev.stage]||(S.usage[ev.stage]={total:0,unknown:false});if(ev.total==null)u.unknown=true;else u.total+=Number(ev.total)||0;break;}
      case 'resource_usage':S.resources=ev.stats;break;
      case 'evidence_quality':S.quality=ev.metrics;S.conflicts=ev.conflicts||S.conflicts;changed='collecting';break;
      case 'gate_check':S.gates[ev.stage]=ev;changed=ev.stage;if(ev.verdict==='fail'&&ev.interactive){S.status='awaiting_gate';setPending('gate',ev,replay);}addActivity(ev,`${ROLES.find(r=>r[0]===ev.stage)?.[1]||'阶段'} · ${checkPassed(ev)?'材料检查通过':'核查需复核'}`);break;
      case 'gate_resolved':clearPending('gate');S.status='running';break;
      case 'verification':S.verifications[ev.report_type]=ev.verification;changed='verifying';break;
      case 'report_start':S.reports[ev.report_type]={version:ev.version,markdown:'',status:ev.mode==='stream'?'streaming':'generating',notice:ev.attempt>1?`第 ${ev.attempt} 次生成 · ${ev.mode==='nonstream'?'普通响应兜底':'重新生成整份报告'}`:''};S.reportTab=ev.report_type;changed='reporting';break;
      case 'report_delta':{let r=S.reports[ev.report_type];if(!r)r=S.reports[ev.report_type]={markdown:'',status:'streaming',version:ev.version};if(!ev.version||!r.version||r.version===ev.version)r.markdown+=ev.delta||'';changed='reporting';break;}
      case 'report_interrupted':{const r=S.reports[ev.report_type];if(r){r.status='partial';r.notice=`${ev.message}（${ev.partial_chars||0} 字符）${ev.retrying?'，正在准备重试。':'。'} `;}changed='reporting';addActivity(ev,'报告响应中断 · 已保留草稿',true);break;}
      case 'report_retry':changed='reporting';addActivity(ev,ev.message);break;
      case 'report':S.reports[ev.report_type]={markdown:ev.markdown||'',status:'generated'};changed='reporting';break;
      case 'resumed':S.status='running';S.error=null;$('#error-banner').classList.add('hidden');clearPending();Object.values(S.calls).forEach(c=>{if(c.status==='busy')c.status='interrupted';});addActivity(ev,'已从检查点恢复执行，保留成功产出');break;
      case 'decision':S.decisions.push(ev);S.strategy='adaptive';addActivity(ev,ev.reason);break;
      case 'execution_policy':S.strategy=ev.search_strategy||'legacy';S.policy=ev;addActivity(ev,ev.message);break;
      case 'evidence_context':addActivity(ev,ev.message);break;
      case 'resource_stop':addActivity(ev,ev.reason||'达到本次请求预算，保留进度');break;
      case 'cache_invalidated':addActivity(ev,ev.message);break;
      case 'operation_cache_invalid':addActivity(ev,ev.message,true);break;
      case 'operation_error':addActivity(ev,`${operationLabel(ev.operation)} · 请求失败，已保留恢复进度`,true);break;
      case 'operation_restored':addActivity(ev,`${operationLabel(ev.operation)} · 复用成功产出`);break;
      case 'operation_start':addActivity(ev,`${operationLabel(ev.operation)} · 等待预算 ${Math.ceil(ev.timeout_seconds)} 秒`);break;
      case 'operation_done':addActivity(ev,`${operationLabel(ev.operation)}完成 · ${ev.elapsed_seconds} 秒`);break;
      case 'model_attempt':addActivity(ev,`${operationLabel(ev.operation)} · 第 ${ev.attempt} 次请求 · ${{running:'等待模型返回',returned:`已返回（${ev.elapsed_seconds} 秒）`,error:`请求失败（${ev.elapsed_seconds} 秒）`}[ev.status]||ev.status}`);break;
      case 'done':S.status='done';clearPending();Object.values(S.calls).forEach(c=>{if(c.status==='busy')c.status='interrupted';});addActivity(ev,'研究完成 · 报告与核查记录已保存');if(!replay){const id=S.id, revision=viewRequest;queueMicrotask(()=>{if(S.id===id && viewRequest===revision)void viewRun(id,true);});}break;
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
  function connect(after=-1){
    es?.close();
    const rid=S.id, source=new EventSource(`/api/runs/${rid}/events?after=${after}`);
    es=source;
    source.onopen=()=>{if(es===source)health(true,'实时连接正常');};
    source.onmessage=e=>{
      if(S.id!==rid || es!==source)return;
      try{
        const event=JSON.parse(e.data);
        if(event.type!=='stream_end' && e.lastEventId && /^\d+$/.test(e.lastEventId)){
          const seq=Number(e.lastEventId);
          if(seq<=after)return;
          after=seq;
        }
        if(event.type==='tool_result' && event.call_id && S.calls[event.call_id]?.status==='success')return;
        ingest(event);
      }catch(err){console.error(err);}
    };
    source.onerror=()=>{if(es===source&&!FINAL.has(S.status)){health(false,'连接恢复中 · 后台可能仍在运行');void window.OSINTAuth?.check();}};
  }
  async function viewRun(rid,completed=false){if(startPending && !completed)return;const request=++viewRequest;es?.close();es=null;closeAll();try{let d=await api(`/api/runs/${rid}`);
    for(let retry=0;completed && retry<6 && (d.status!=='done'||!d.reports?.length);retry++){
      if(request!==viewRequest)return;
      await new Promise(resolve=>setTimeout(resolve,300));
      d=await api(`/api/runs/${rid}`);
    }
    if(request!==viewRequest)return;forgetDraft();rememberWorkspace(rid);S=fresh();S.id=rid;S.domain=d.domain;S.detail=d;S.task=d.task;S.strategy=d.search_strategy||'legacy';$('#error-banner').classList.add('hidden');for(const e of d.events||[])ingest(e,true);if(!S.decisions.length)S.decisions=d.summary?.decision_history||[];S.status=d.status;if(S.status==='done')$('#error-banner').classList.add('hidden');for(const r of d.reports||[])if(FINAL.has(d.status)||!S.reports[r.type])S.reports[r.type]={markdown:r.markdown,status:'generated'};S.verifications={...(d.summary?.verifications||{}),...S.verifications};S.resources=d.summary?.resource_usage||S.resources;S.quality=d.summary?.evidence_metrics||S.quality;S.conflicts=d.summary?.conflicts||S.conflicts;S.plan=S.plan||d.summary?.plan;S.knowledge=S.knowledge||d.summary?.knowledge;if(!S.evidence.length)S.evidence=d.summary?.evidence||[];if(!S.findings.length)S.findings=d.summary?.findings||[];S.sources=d.search_sources||S.sources;S.resumeInfo=d.resume_info;setTimeline(d.timeline);if(!d.events?.length){S.plan=d.summary?.plan;S.evidence=d.summary?.evidence||[];S.knowledge=d.summary?.knowledge;S.findings=d.summary?.findings||[];for(const r of d.reports||[])S.reports[r.type]={markdown:r.markdown,status:'generated'};S.verifications=d.summary?.verifications||{};}if(d.pending_sources)S.pending={kind:'sources',payload:d.pending_sources};else if(d.pending_gate)S.pending={kind:'gate',payload:d.pending_gate};else if(d.pending_plan)S.pending={kind:'plan',payload:{plan:d.pending_plan}};else if(FINAL.has(S.status))S.pending=null;S.view=S.pending?.kind==='sources'?'collecting':current();renderScreen();renderMetrics();renderContent(true);if(!FINAL.has(S.status))connect((d.events||[]).length-1);if(S.pending)openPending();loadHistory();if(d.status==='done' && d.reports?.length)void openFinalReport();}catch(error){toast(error.message);}}
  const welcomeTitle=$('#welcome-title').textContent;
  const welcomeLabel=$('.welcome-compose label').textContent;
  function rememberWorkspace(runId) {
    const url=new URL(location.href);
    url.searchParams.delete('draft');
    if(url.pathname==='/')url.searchParams.set('workspace','research');
    if(runId)url.searchParams.set('record',runId);
    else url.searchParams.delete('record');
    history.replaceState(null,'',url);
  }
  function forgetDraft() {
    $('#welcome-title').textContent=welcomeTitle;
    $('.welcome-compose label').textContent=welcomeLabel;
    document.body.classList.remove('travel-draft-mode');
    if (draftId) sessionStorage.removeItem('travel-workspace-draft');
    draftId=null;travelDraft=null;
    $('#draft-instruction').hidden=true;
  }
  function newResearch(){
    if(startPending||S.id&&!FINAL.has(S.status))return;
    ++viewRequest;es?.close();es=null;closeAll();forgetDraft();rememberWorkspace();S=fresh();
    $('#task-input').value='';$('#error-banner').classList.add('hidden');
    document.body.classList.remove('focus-mode');$('#btn-focus').setAttribute('aria-pressed','false');$('#btn-focus').textContent='专注大屏';
    renderScreen();renderMetrics();renderContent(true);loadHistory();updateRecommendation();
    document.dispatchEvent(new CustomEvent('osint:welcome'));$('#task-input').focus();
  }
  function selectedModels(){return Object.fromEntries($$('#role-grid select').map(s=>[s.dataset.role,s.value]));}
  async function startRun(){
    if(startPending || !modelsLoaded || (draftId && !travelDraft) || (S.id && !FINAL.has(S.status)))return;
    const task=$('#task-input').value.trim();
    if(!task){toast('请输入研究任务');return;}
    const roles=selectedModels();
    const settings={model_id:roles.planner,role_models:$('#model-mode').value==='recommended'?null:roles,
      execution_mode:$('#execution-mode').value,search_strategy:$('#search-strategy').value,auto_confirm:false};
    startPending=true;
    runningUI();
    try{
      const domain=travelDraft ? 'guizhou_travel' : null;
      const d=travelDraft ? await (await handoff).startDraft(travelDraft,task,settings) : await api('/api/runs',{task,...settings});
      ++viewRequest;
      forgetDraft();
      rememberWorkspace(d.run_id);
      closeAll();
      S=fresh();S.id=d.run_id;S.domain=domain;S.task=task;S.strategy=settings.search_strategy;S.status='running';
      $('#error-banner').classList.add('hidden');
      renderScreen();renderMetrics();renderContent(true);connect();loadHistory();
    }catch(error){toast(error.message);}
    finally{startPending=false;runningUI();}
  }
  async function cancelRun(){if(!S.id)return;try{await api(`/api/runs/${S.id}/cancel`,{});await viewRun(S.id);}catch(error){toast(error.message);}}
  async function resumeRun(){if(!S.id||!modelsLoaded)return;$('#btn-resume').disabled=true;try{const change=$('#resume-change-models').checked;await api(`/api/runs/${S.id}/resume`,{use_recommended:false,role_models:change?selectedModels():null});await viewRun(S.id);}catch(error){toast(error.message);}finally{runningUI();}}
  async function loadHistory(){try{const d=await api('/api/runs');$('#history-list').innerHTML=(d.runs||[]).map(h=>{const title=/^帮我拟一[个份].*(旅游|旅行)计划/.test(h.task)?((h.task.match(/去([^，、的]{2,12})的/)||[])[1]||'贵州')+'旅行行程':h.task;const date=new Date(h.created_at),validDate=Number.isFinite(date.getTime());const tag='button', action='type="button" data-run="'+esc(h.id)+'"';
      return `<${tag} class="history-item ${h.id===S.id?'active':''}" ${action} aria-current="${h.id===S.id}"><div class="history-task" title="${esc(title)}">${esc(title)}</div><div class="history-meta"><span class="history-status" data-status="${esc(h.status)}"><i class="status-dot ${esc(h.status)}" aria-hidden="true"></i>${esc(LABELS[h.status]||h.status)}</span><time ${validDate?`datetime="${esc(date.toISOString())}"`:''}>${validDate?esc(date.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'})):'日期未知'}</time></div><small class="history-count">${esc(h.report_count||0)} 份报告</small></${tag}>`;}).join('')||'<div class="empty">暂无研究记录</div>';$$('[data-run]').forEach(b=>b.addEventListener('click',()=>viewRun(b.dataset.run)));}catch{toast('研究记录读取失败');}}
  function closeAll(){readerVersion++;readerRun=null;if($('#final-reader').open)$('#final-reader').close();$$('.modal-overlay').forEach(m=>m.classList.add('hidden'));document.dispatchEvent(new CustomEvent('osint:report-chat-close'));}
  function openModal(id){openTrigger=document.activeElement;closeAll();const modal=$('#'+id);modal.classList.remove('hidden');modal.querySelector('button,input,textarea')?.focus();}
  function closeModal(id){$('#'+id).classList.add('hidden');if(openTrigger?.isConnected)openTrigger.focus();}
  function openPending(){if(!S.pending)return;const {kind,payload}=S.pending;if(kind==='plan'){const routing=S.policy?.routing?.reason;$('#confirm-plan-body').innerHTML=(routing?`<p><strong>${esc(S.policy.label)}</strong> ‷ ${esc(routing)}</p>`:'')+renderPlan(payload.plan||S.plan);openModal('confirm-modal');}if(kind==='sources'){$('#sources-options').innerHTML=(payload.options||[]).map(p=>`<label class="source-option"><input type="checkbox" value="${esc(p.id)}" ${(payload.selected||[]).includes(p.id)?'checked':''}><span><strong>${esc(p.name)}<span class="source-cost ${p.free?'':'paid'}">${p.free?'免 Key · 免费档':'可能计费'}</span></strong><small>${esc(p.description)}</small></span></label>`).join('');$('#sources-error').textContent='';openModal('sources-modal');}if(kind==='gate'){$('#gate-modal-sub').textContent='请检查问题与影响，再选择重做或接受问题继续。';$('#gate-body').innerHTML=verification(payload);openModal('gate-modal');}}
  async function submit(button,url,body,modal){const rid=S.id;button.disabled=true;try{await api(`/api/runs/${rid}/${url}`,body);if(S.id===rid)closeModal(modal);}catch(error){if(modal==='sources-modal')$('#sources-error').textContent=error.message;else toast(error.message);}finally{button.disabled=false;}}
  function askReport(){if(S.status!=='done')return;const reportType=Object.keys(S.reports).includes(S.reportTab)?S.reportTab:Object.keys(S.reports)[0];if(reportType)document.dispatchEvent(new CustomEvent('osint:report-question',{detail:{runId:S.id,reportType,domain:S.domain}}));}
  function downloadReport(){if(S.id)window.location.assign(`/api/runs/${S.id}/report.md`);}
  async function openSagImport(){
    if(!sagEnabled||S.status!=='done'||!S.reportTab)return;
    const draft={runId:S.id,reportType:S.reportTab,version:null};
    sagDraft=draft;
    const list=$('#sag-import-sections'),status=$('#sag-import-status');
    list.replaceChildren();
    status.textContent='正在读取报告片段…';
    $('#sag-import-submit').disabled=true;
    openModal('sag-import-modal');
    try{
      const data=await api(`/api/runs/${draft.runId}/sag-import?report_type=${encodeURIComponent(draft.reportType)}`);
      if(sagDraft!==draft)return;
      draft.version=data.report_version;
      list.innerHTML=data.sections.map(section=>`<article class="source-option"><span>
        <label><input type="checkbox" value="${esc(section.id)}"> <strong>${esc(section.title)}</strong></label>
        <details><summary>预览片段</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(section.markdown)}</pre></details>
      </span></article>`).join('');
      status.textContent='勾选要保存的片段。';
    }catch(error){if(sagDraft===draft)status.textContent=error.message;}
  }
  async function saveSagImport(){
    const draft=sagDraft, button=$('#sag-import-submit'),status=$('#sag-import-status');
    const inputs=$$('#sag-import-sections input');
    const ids=inputs.filter(input=>input.checked).map(input=>Number(input.value));
    if(!draft?.version||!ids.length||button.disabled)return;
    button.disabled=true;
    inputs.forEach(input=>input.disabled=true);
    status.textContent='正在保存并检查索引…';
    let saved=false;
    try{
      const result=await api(`/api/runs/${draft.runId}/sag-import`,{
        report_type:draft.reportType,report_version:draft.version,section_ids:ids
      });
      if(sagDraft!==draft)return;
      saved=true;
      status.textContent=result.status==='indexed'
        ? `已保存 ${result.count} 个片段，可在 SAG Wiki 中搜索。`
        : '片段已保存，SAG Wiki 尚未完成索引。索引更新后即可搜索。';
    }catch(error){if(sagDraft===draft)status.textContent=error.message;}
    finally{
      if(sagDraft===draft&&!saved){
        inputs.forEach(input=>input.disabled=false);
        button.disabled=false;
      }
    }
  }
  function inline(text){return esc(text).replace(/\[([^\]]+)\]\(([^)]+)\)/g,(_,label,url)=>safeURL(url)?`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`:label).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');}
  function markdown(src){const lines=String(src||'').split(/\r?\n/),out=[];let list='',code=false,buffer=[];const close=()=>{if(list){out.push(`</${list}>`);list='';}};for(let i=0;i<lines.length;i++){const t=lines[i].trim();if(t.startsWith('```')){close();if(code){out.push('<pre><code>'+esc(buffer.join('\n'))+'</code></pre>');buffer=[];}code=!code;continue;}if(code){buffer.push(lines[i]);continue;}if(!t){close();continue;}if(t.startsWith('|')&&i+1<lines.length&&/^\|?[\s:|\-]+\|$/.test(lines[i+1].trim())){close();const cells=x=>x.replace(/^\||\|$/g,'').split('|');out.push('<table><thead><tr>'+cells(t).map(x=>'<th>'+inline(x)+'</th>').join('')+'</tr></thead><tbody>');i+=2;for(;i<lines.length&&lines[i].trim().startsWith('|');i++)out.push('<tr>'+cells(lines[i].trim()).map(x=>'<td>'+inline(x)+'</td>').join('')+'</tr>');i--;out.push('</tbody></table>');continue;}const h=t.match(/^(#{1,6})\s+(.*)$/);if(h){close();out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`);continue;}const li=t.match(/^([-*+]\s+|\d+[.)]\s+)(.*)$/);if(li){const kind=/^\d/.test(li[1])?'ol':'ul';if(kind!==list){close();out.push(`<${kind}>`);list=kind;}out.push('<li>'+inline(li[2])+'</li>');continue;}close();out.push(t.startsWith('>')?'<blockquote>'+inline(t.slice(1))+'</blockquote>':/^---+$/.test(t)?'<hr>':'<p>'+inline(t)+'</p>');}close();if(buffer.length)out.push('<pre><code>'+esc(buffer.join('\n'))+'</code></pre>');return out.join('');}
  function bind(){
    $('#sag-import-sections').onchange=()=>{$('#sag-import-submit').disabled=!$('#sag-import-sections input:checked');};
    $('#sag-import-submit').onclick=saveSagImport;
    $('#btn-history').onclick=()=>{const open=document.body.classList.toggle('mobile-history-open');$('#btn-history').setAttribute('aria-expanded',String(open));$('#btn-history').textContent=open?'收起研究记录 ↑':'展开研究记录 ↓';};
    $('#btn-new-research').onclick=newResearch;$('#btn-run').onclick=startRun;$('#btn-cancel').onclick=cancelRun;$('#btn-resume').onclick=resumeRun;$('#btn-refresh-history').onclick=loadHistory;
    $('#resume-change-models').onchange=runningUI;
    $('#btn-topology').onclick=()=>{document.dispatchEvent(new CustomEvent('osint:topology'));openModal('topology-modal');};
    document.addEventListener('osint:select-stage',event=>{if(ROLES.some(r=>r[0]===event.detail)){closeModal('topology-modal');selectStage(event.detail,false);}});
    $$('.chip').forEach(b=>b.onclick=()=>{$('#task-input').value=b.dataset.example;$('#task-input').dispatchEvent(new Event('input',{bubbles:true}));});
    $('#btn-follow').onclick=()=>S.status==='done'?openFinalReport():selectStage(current(),true);$('#btn-focus').onclick=()=>{const yes=document.body.classList.toggle('focus-mode');$('#btn-focus').setAttribute('aria-pressed',String(yes));$('#btn-focus').textContent=yes?'退出专注':'专注大屏';};
    $('#btn-open-report').onclick=openFinalReport;$('#btn-ask-report').onclick=askReport;$('#btn-download-final').onclick=downloadReport;
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
      $('#mode-description').textContent=d.semantic_on_start?'开始后按任务选择最多 1、3 或 5 个子问题。':`${mode==='auto'?'自动匹配：':''}${d.label} · 已配置对应岗位模型${$('#model-mode').value==='custom'?'（当前保留手动选择）':''}。`;
    }catch{if(request===recommendationRequest)toast('模型推荐更新失败，请重新选择研究深度后重试');}
  }
  async function openFinalReport(){
    if(S.status!=='done')return;
    const id=S.id, revision=++readerVersion;
    try{
      const detail=S.detail || await api('/api/runs/'+encodeURIComponent(id));
      if(revision!==readerVersion || S.id!==id)return;
      S.detail=detail;
      const reports=detail.reports||[];
      if(!reports.length){toast('这条任务还没有最终报告。');return;}
      const reader=$('#final-reader'), body=$('#report-body'), types=$('#reader-types');
      const type=reports.find(r=>r.type===S.reportTab)?.type || reports[0].type;
      S.reportTab=type;
      readerRun=id;
      types.replaceChildren();
      for(const report of reports.length>1?reports:[]){
        const tab=document.createElement('button');tab.type='button';tab.className='soft-button';tab.textContent=report.type;
        tab.setAttribute('aria-pressed',String(type===report.type));tab.onclick=()=>{S.reportTab=report.type;void openFinalReport();};types.append(tab);
      }
      $('#reader-title').textContent=type;
      body.replaceChildren();
      if(!reader.open)reader.showModal();
      reader.scrollTop=0;
      if(detail.domain==='guizhou_travel' || detail.trip_plan || detail.summary?.trip_plan){
        const [{renderJourney},{renderFormat},{loadCatalog},{liveRequest}]=await Promise.all([
          import('/static/travel/journey.js'),import('/static/travel/cards.js'),import('/static/travel/pois.js'),import('/static/travel/api.js')]);
        const catalog=await loadCatalog().catch(()=>new Map());
        if(revision!==readerVersion || !reader.open || S.id!==id)return;
        const travel={...detail,trip_plan:detail.trip_plan || detail.summary?.trip_plan};
        renderJourney(body,travel,catalog,liveRequest);
        renderFormat(body,travel);
        if (!travel.card_report && reports[0].markdown && travel.trip_plan) {
          const full=document.createElement('details'), heading=document.createElement('summary');
          full.className='report-evidence';heading.textContent='阅读完整行程正文';
          const article=document.createElement('article');article.className='report-md';
          article.innerHTML=markdown(reports[0].markdown);full.append(heading,article);body.append(full);
        }
      }else{
        const article=document.createElement('article');article.className='report-md';
        article.innerHTML=markdown(reports.find(r=>r.type===type)?.markdown);body.append(article);
      }
    }catch(error){toast(error.message);}
  }
  async function init(){buildNav();bind();setInterval(tick,1000);renderScreen();$('#model-mode').onchange=()=>applyModelMode($('#model-mode').value==='recommended');$('#btn-reset-models').onclick=()=>{$('#model-mode').value='recommended';applyModelMode(true);toast('已恢复推荐岗位模型');};$('#execution-mode').onchange=()=>{$('#mode-description').textContent=({auto:'简单释义合并分析与推理；复杂任务保留完整研究流程。',quick:'工作预算 5 分钟，1 份简明报告，保留最终事实核查。',standard:'工作预算 8 分钟，按需生成报告，避免逐阶段重复核查。',deep:'工作预算 12 分钟，更多证据与定向核查。人工等待不计入预算。'})[$('#execution-mode').value];};await Promise.allSettled([api('/api/health').then(d=>health(d.model_ready??d.has_key,d.experiment_mode?(d.has_key?'AMD Radeon Cloud \u00b7 \u5b9e\u9a8c\u6a21\u5f0f':'AMD Radeon Cloud \u00b7 \u7f3a\u5c11 API Key'):(d.model_ready??d.has_key)?'服务已连接':'模型服务未就绪')).catch(()=>health(false,'服务离线')),api('/api/models').then(d=>{sagEnabled=d.sag_enabled===true;modelDefaults=d.role_defaults||{};modelReasons=d.role_reasons||{};modelProfiles=d.role_profiles||{};profileReasons=d.profile_reasons||{};$$('#role-grid select').forEach(sel=>{sel.innerHTML=(d.models||[]).map(m=>`<option value="${esc(m.id)}" ${m.available===false?'disabled':''}>${esc(m.label)}${m.available===false?' · '+esc(m.unavailable_reason||'当前额度不可用'):''}</option>`).join('');const note=document.createElement('small');note.textContent=modelReasons[sel.dataset.role]||'';sel.parentElement.append(note);});$('#model-sources').innerHTML=(d.sources||[]).map(s=>`<a href="${esc(safeURL(s.url))}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>`).join(' · ');applyModelMode(true);modelsLoaded=true;renderReadiness();runningUI();updateRecommendation();}).catch(()=>{modelsLoadFailed=true;renderReadiness();toast('无法载入岗位配置，请刷新后重试');})]);loadHistory();}
  document.addEventListener('DOMContentLoaded',()=>{
    $('#btn-comparison').onclick=async()=>{try{const d=await api('/api/comparison');$('#comparison-result').innerHTML='<p>固定模拟数据；不是实际搜索质量或云端耗时承诺。完整报告位于 outputs/comparison/latest.md。</p>'+d.scenarios.map(s=>`<h3>${esc(s.name)}</h3><table><thead><tr><th>指标</th><th>Legacy</th><th>Adaptive</th></tr></thead><tbody>${['search_calls','unique_urls','unique_domains','duplicate_ratio','supplemental_searches','early_stops','provider_switches'].map(k=>`<tr><th>${esc(({search_calls:'搜索次数',unique_urls:'独立网址',unique_domains:'来源域名',duplicate_ratio:'重复率',supplemental_searches:'补搜次数',early_stops:'提前停止',provider_switches:'渠道切换'})[k])}</th><td>${esc(s.legacy[k]??'unknown')}</td><td>${esc(s.adaptive[k]??'unknown')}</td></tr>`).join('')}</tbody></table>`).join('');}catch(e){$('#comparison-result').textContent=e.message;}};
  });
  document.addEventListener('DOMContentLoaded',()=>{
    void init();
    const record=new URLSearchParams(location.search).get('record');
    if(record) void viewRun(record);
    else if(draftId){
      const expected=draftId, hint=$('#draft-instruction');
      document.body.classList.add('travel-draft-mode');
      $('#welcome-title').textContent='核对这次旅行的安排';
      $('.welcome-compose label').textContent='行程草稿';
      hint.hidden=false;hint.textContent='正在读取行程草稿…';
      handoff.then(module=>module.restoreDraft(expected)).then(draft=>{
        if(draftId!==expected)return;
        travelDraft=draft;$('#task-input').value=draft.text;
        hint.textContent='可在前三行直接修改地点、日期、人数和预算，末尾可自由补充需求。点击“开始研究”后查询。';
        runningUI();$('#task-input').focus();
      }).catch(error=>{if(draftId===expected)hint.textContent=error.message;});
    }
    $('#task-input').addEventListener('input',()=>{
      if(travelDraft){travelDraft.text=$('#task-input').value;sessionStorage.setItem('travel-workspace-draft',JSON.stringify(travelDraft));}
    });
    $('#reader-close').onclick=()=>$('#final-reader').close();
    $('#final-reader').addEventListener('close',()=>{
      if(!$('#final-reader').open && readerRun===S.id && S.id){readerRun=null;askReport();}
    });
    $('#reopen-report').onclick=openFinalReport;
  });
  document.addEventListener('DOMContentLoaded',()=>{
    $('#execution-mode').addEventListener('change',updateRecommendation);
    $('#task-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing&&!e.repeat){e.preventDefault();if(!$('#btn-run').disabled)$('#btn-run').click();}});
    $('#task-input').addEventListener('input',()=>{clearTimeout(recommendationTimer);if($('#execution-mode').value==='auto')recommendationTimer=setTimeout(updateRecommendation,250);});
  });
})();
