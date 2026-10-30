/* 只读视觉层：使用主界面快照，不调用任务接口、不管理执行状态。 */
(() => {
  'use strict';
  const $=s=>document.querySelector(s);
  const roles=[['planning','规划','◎'],['collecting','收集','⌕'],['analyzing','分析','▤'],['reasoning','推理','◇'],['reporting','报告','▧'],['verifying','核查','✓']];
  const names={parallel:'Parallel',you:'You.com',bailian:'百炼'};
  let snapshot={},previousStage=null,previousRun=null,flightTimer,transitionTimer,deliveryTimer,selected='planning',tab='workflow';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const calm=()=>document.hidden||document.body.classList.contains('reduced-motion')||matchMedia('(prefers-reduced-motion: reduce)').matches;
  const selectStage=stage=>document.dispatchEvent(new CustomEvent('osint:select-stage',{detail:stage}));
  const visibleTopology=()=>!$('#topology-modal').classList.contains('hidden');
  function clearFlight(){clearTimeout(flightTimer);$('#evidence-flight')?.replaceChildren();}
  function reset(){clearFlight();clearTimeout(transitionTimer);clearTimeout(deliveryTimer);$('#live-screen').classList.remove('stage-handoff');$('#report-ready').classList.remove('delivery-arrival');previousStage=null;}
  function mount(){
    const stars=document.createElement('div');stars.className='agent-stars';stars.id='agent-stars';
    stars.innerHTML='<svg class="star-links" viewBox="0 0 360 300" aria-hidden="true">'+roles.map((r,i)=>`<path data-star-link="${r[0]}" d="M180 150 L${i<3?42:318} ${[75,150,225][i%3]}"/>`).join('')+'</svg>'+roles.map((r,i)=>`<button class="agent-star star-${i}" data-star="${r[0]}" aria-label="查看${r[1]}阶段"><span>${r[2]}</span><b>${r[1]}</b><small>待命</small></button>`).join('');
    $('#network').append(stars);
    const flight=document.createElement('div');flight.id='evidence-flight';flight.className='evidence-flight';flight.setAttribute('aria-hidden','true');$('#network').append(flight);
    stars.querySelectorAll('[data-star]').forEach(button=>button.onclick=()=>selectStage(button.dataset.star));
    for(const value of ['workflow','architecture']){
      $('#tab-'+value).onclick=()=>switchTab(value);
      $('#tab-'+value).onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'workflow':e.key==='End'?'architecture':tab==='workflow'?'architecture':'workflow';switchTab(next);$('#tab-'+next).focus();}};
    }
  }
  function update(data){
    if(!$('#agent-stars'))return;
    snapshot=data;
    if(data.id!==previousRun){reset();previousRun=data.id;}
    const live=!!data.id&&data.status==='running'&&!data.pending;
    const hero=$('#live-screen');hero.classList.toggle('visual-running',live);hero.classList.toggle('visual-waiting',!!data.pending);hero.classList.toggle('visual-error',['error','cancelled'].includes(data.status));
    hero.dataset.execution=data.current||'';
    if(previousStage&&previousStage!==data.current&&live&&!calm()){
      hero.classList.remove('stage-handoff');void hero.offsetWidth;hero.classList.add('stage-handoff');
      clearTimeout(transitionTimer);transitionTimer=setTimeout(()=>hero.classList.remove('stage-handoff'),700);
    }
    previousStage=data.current;
    if(!live)clearFlight();
    for(const [stage] of roles){
      const button=$(`[data-star="${stage}"]`),row=data.stages[stage]||{};
      const actual=stage===data.current&&!!data.id;
      button.classList.toggle('star-live',actual&&live);button.classList.toggle('star-done',row.status==='done');button.classList.toggle('star-selected',stage===data.view);button.setAttribute('aria-pressed',String(stage===data.view));
      button.querySelector('small').textContent=actual&&data.pending?'等待操作':actual&&['error','cancelled'].includes(data.status)?'已中断':actual&&live?'执行中':row.status==='done'?'已完成':'待命';
      $(`[data-star-link="${stage}"]`).classList.toggle('lit',actual&&live);
    }
    $('#mission-state').textContent=!data.id?'等待研究任务':data.pending?'等待您的操作':data.status==='done'?'最终报告已交付':data.status==='error'?'执行中断 · 进度已保存':data.status==='cancelled'?'任务已终止':`${roles.find(r=>r[0]===data.current)?.[1]||'研究'}岗位运行中`;
    $('#mission-detail').textContent=data.id?(data.stages[data.current]?.label||'等待下一条执行事件'):'轨道与光流将在真实执行时点亮';
    if(visibleTopology())updateTopology();
  }
  function effect(event){
    if(!snapshot.id)return;
    if(event.type==='done'&&snapshot.reports&&!calm()){
      $('#report-ready').classList.add('delivery-arrival');clearTimeout(deliveryTimer);deliveryTimer=setTimeout(()=>$('#report-ready').classList.remove('delivery-arrival'),850);
    }
    if(event.type!=='tool_result'||event.stage!=='collecting'||snapshot.status!=='running'||calm())return;
    const count=Number.isInteger(event.result_count)?event.result_count:event.items?.length;
    if(!count||count<0)return;
    let card=$('#evidence-flight .flight-card');
    if(card){card.dataset.count=Number(card.dataset.count)+count;card.querySelector('b').textContent=`${card.dataset.count} 条证据已返回`;return;}
    card=document.createElement('div');card.className='flight-card '+(event.tool==='you'?'from-right':'from-left');card.dataset.count=count;
    const label=document.createElement('small');label.textContent=names[event.tool]||'搜索通道';
    const title=document.createElement('b');title.textContent=`${count} 条证据已返回`;
    const excerpt=document.createElement('span');excerpt.textContent=event.items?.[0]?.title||'公开网页检索结果';
    card.append(label,title,excerpt);$('#evidence-flight').append(card);clearTimeout(flightTimer);flightTimer=setTimeout(clearFlight,1400);
  }
  function workflow(){
    return `<div class="atlas-intro"><span class="atlas-dot"></span><span id="atlas-status"></span></div><div class="atlas-manual">用户任务 → 规划方案 → 人工确认 → 手选搜索通道</div><div class="atlas-flow">${roles.map((r,i)=>`<button data-atlas-node="${r[0]}" class="atlas-node"><span class="atlas-index">0${i+1}</span><span class="atlas-symbol">${r[2]}</span><strong>${r[1]}${r[0]==='reporting'?'交付':'岗位'}</strong><small class="atlas-node-status">待命</small></button>`).join('')}</div><div class="atlas-branches"><div><b>搜索来源</b><p>Parallel / You.com 直接检索<br>百炼 MCP 仅勾选后启用</p></div><div><b>执行分支</b><p>简明模式合并分析与推理<br>深入模式增加阶段定向核查</p></div><div><b>质量与恢复</b><p>最终报告独立核查<br>失败 → 检查点 → 原步骤继续</p></div></div><div id="atlas-inspector" class="atlas-inspector"></div><p class="atlas-footnote">连线表示数据传递顺序，不代表所有模型同时运行。点击节点查看职责与当前配置。</p>`;
  }
  function architecture(){
    const layers=[['01 · 交互层','浏览器工作台','阶段浏览 · 人工确认 · 渠道选择 · 最终报告'],['02 · 服务层','FastAPI + SSE','POST 提交决策 / 恢复任务 · SSE 回传事件与计时'],['03 · 编排层','Python 执行流水线','任务深度 · 岗位模型 · 超时预算 · 质量关卡'],['04 · 能力层','模型与搜索','百炼兼容模型 API · 免费搜索 MCP · 可选百炼搜索 · 本地报告模板'],['05 · 持久层','本地文件','原子保存检查点 · 证据与分析 · 事件记录 · Markdown 报告']];
    return `<div class="architecture-stack">${layers.map(row=>`<article><span>${row[0]}</span><div><h4>${row[1]}</h4><p>${row[2]}</p></div></article>`).join('')}</div><p class="atlas-footnote">这是当前项目的软件拓扑。模型和搜索调用由后端执行，浏览器只接收事件；刷新界面不会重新启动研究。</p>`;
  }
  function updateTopology(){
    const label=roles.find(r=>r[0]===snapshot.current)?.[1]||'尚未开始';
    $('#atlas-status').textContent=snapshot.id?`当前任务：${snapshot.status==='done'?'已完成':snapshot.pending?'等待人工操作':snapshot.status==='error'?'已中断':label+'阶段'} · ${snapshot.mode?({quick:'简明解答',standard:'标准研究',deep:'深入研究'})[snapshot.mode]:'按任务配置执行'}`:'尚未选择任务 · 展示项目结构';
    document.querySelectorAll('[data-atlas-node]').forEach(button=>{
      const stage=button.dataset.atlasNode,row=snapshot.stages?.[stage]||{};
      button.classList.toggle('atlas-current',stage===snapshot.current&&!!snapshot.id&&snapshot.status==='running');button.classList.toggle('atlas-selected',stage===selected);button.setAttribute('aria-pressed',String(stage===selected));
      button.querySelector('small').textContent=stage===snapshot.current&&snapshot.pending?'等待决策':row.status==='done'?'已完成':stage===snapshot.current&&snapshot.status==='running'?'正在执行':'未执行 / 待命';
    });
    const descriptions={planning:'理解用户目标并提出可检索的问题，用户确认后再执行。',collecting:'按用户选定渠道检索公开网页；免费 MCP 不调用收集模型，百炼搜索使用收集模型。',analyzing:'从证据中提炼实体、观点和案例，保留来源与资料局限。',reasoning:'串联证据与反证形成发现；简明模式通常复用合并分析产出。',reporting:'按模板生成报告，核查后交付；正文与来源可直接查看和下载。',verifying:'程序检查结构与覆盖，模型独立核查最终报告；深入模式还检查分析和推理。'};
    const roleKey={planning:'planner',collecting:'collector',analyzing:'analyst',reasoning:'reasoner',reporting:'reporter',verifying:'verifier'}[selected];
    const model=snapshot.roles[roleKey]||snapshot.stages?.[selected]?.model||'实际执行后显示';
    // 更新节点信息时不替换正在聚焦的按钮，保证键盘操作稳定。
    const inspector=$('#atlas-inspector');
    if(inspector.dataset.selected!==selected){inspector.innerHTML='<div><span class="section-kicker">NODE DETAILS</span><h4></h4><p></p><small></small></div><button class="soft-button" id="atlas-view-stage">查看此阶段 ↗</button>';inspector.dataset.selected=selected;$('#atlas-view-stage').onclick=()=>selectStage(selected);}
    inspector.querySelector('h4').textContent=roles.find(r=>r[0]===selected)[1];inspector.querySelector('p').textContent=descriptions[selected];inspector.querySelector('small').textContent='绑定模型：'+model;
  }
  function switchTab(value){tab=value;for(const kind of ['workflow','architecture']){const active=kind===value;$('#tab-'+kind).setAttribute('aria-selected',String(active));$('#tab-'+kind).tabIndex=active?0:-1;$('#topology-'+kind).classList.toggle('hidden',!active);}}
  function openTopology(){
    // 手动打开后才构建图，不在后台渲染隐藏拓扑。
    if(!$('#topology-workflow').children.length){$('#topology-workflow').innerHTML=workflow();$('#topology-architecture').innerHTML=architecture();document.querySelectorAll('[data-atlas-node]').forEach(button=>button.onclick=()=>{selected=button.dataset.atlasNode;updateTopology();});}
    switchTab(tab);updateTopology();
  }
  document.addEventListener('DOMContentLoaded',mount);
  document.addEventListener('osint:view',e=>update(e.detail));
  document.addEventListener('osint:effect',e=>effect(e.detail));
  document.addEventListener('osint:topology',openTopology);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearFlight();});
})();
