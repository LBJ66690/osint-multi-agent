/* Move the original controls, never clone inputs, listeners, or pet state. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const stages = [['planning','规划'],['collecting','收集'],['analyzing','分析'],['reasoning','推理'],['reporting','报告'],['verifying','核查']];
  const modes = {quick:'简明解答',standard:'标准研究',deep:'深入研究'};
  let snapshot = {}, mode = '', preview = false, input, send, examples, fish, topology;
  function move(node, selector) { const target=$(selector); if(node && target && node.parentElement!==target) target.append(node); }
  function setMode(next) {
    if(mode===next)return;
    mode=next;const welcome=next==='welcome';
    move(input,welcome?'#welcome-input-slot':'#sidebar-input-slot');
    move(send,welcome?'#welcome-send-slot':'#sidebar-send-slot');
    move(examples,welcome?'#welcome-examples-slot':'#sidebar-examples-slot');
    move(fish,welcome?'#welcome-pet-host':'#dashboard-pet-slot');
    move(topology,welcome?'#welcome-topology-slot':'#dashboard-topology-slot');
    $('#welcome-screen').hidden=!welcome;$('#dashboard-screen').hidden=welcome;
    document.body.classList.toggle('welcome-view',welcome);
    document.body.classList.toggle('dashboard-view',!welcome);
    document.dispatchEvent(new CustomEvent('osint:layout',{detail:{mode:next}}));
    if(!welcome)requestAnimationFrame(()=>{if(mode==='dashboard'){const y=matchMedia('(max-width:680px)').matches?$('.workspace').getBoundingClientRect().top+scrollY-12:0;window.scrollTo({top:Math.max(0,y),behavior:'instant'});}});
  }
  function settings() {
    const value=$('#execution-mode').value;
    $('#welcome-depth').textContent=value==='auto'?'自动匹配深度':modes[value]||'按所选深度';
    $('#welcome-strategy').textContent=$('#search-strategy').value==='adaptive'?'自适应检索':'稳定检索';
  }
  function update(data) {
    if(!input || !data)return;
    snapshot=data;
    setMode(data.id||preview?'dashboard':'welcome');
    const status= !data.id?'看板预览':data.pending?'等待你确认':({running:'正在执行',done:'研究已完成',error:'执行中断',cancelled:'已终止'})[data.status]||'准备执行';
    $('#mission-status').textContent=status;
    $('#mission-status').dataset.status=data.pending?'waiting':data.status;
    $('#task-title').title=data.task||'研究工作台';
    $('#mission-depth').textContent=modes[data.mode]||'按任务配置';
    $('#mission-strategy').textContent=data.strategy==='adaptive'?'自适应检索':'稳定检索';
    const providers={parallel:'Parallel',you:'You.com',bailian:'百炼'};
    $('#mission-sources').textContent=(data.sources||[]).map(s=>providers[s]).filter(Boolean).join(' + ')||'等待选通道';
    for(const [key] of stages){
      const b=$(`[data-route-stage="${key}"]`),row=data.stages?.[key]||{};
      b.classList.toggle('is-done',row.status==='done');
      b.classList.toggle('is-active',!!data.id&&data.current===key&&data.status==='running');
      b.classList.toggle('is-viewed',data.view===key);
      b.setAttribute('aria-pressed',String(data.view===key));
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    input=$('#task-input');send=$('#btn-run');examples=$('.example-chips');fish=$('#research-pet');topology=$('#btn-topology');
    for(const [i,[key,name]] of stages.entries()){
      const b=document.createElement('button');b.type='button';b.dataset.routeStage=key;
      const n=document.createElement('span');n.textContent=String(i+1).padStart(2,'0');
      b.append(n,document.createTextNode(name));b.setAttribute('aria-label',`查看${name}阶段`);
      b.onclick=()=>document.dispatchEvent(new CustomEvent('osint:select-stage',{detail:key}));$('#mission-route').append(b);
    }
    $('#btn-preview-workspace').onclick=()=>{preview=true;setMode('dashboard');update(snapshot);window.scrollTo({top:0,behavior:'instant'});};
    $('#execution-mode').addEventListener('change',settings);$('#search-strategy').addEventListener('change',settings);
    const settingsLink=document.createElement('button');settingsLink.type='button';settingsLink.className='welcome-settings-link';settingsLink.textContent='研究设置 ↓';
    settingsLink.onclick=()=>{$('.composer').scrollIntoView({block:'start',behavior:'instant'});$('#execution-mode').focus({preventScroll:true});};$('.welcome-settings').append(settingsLink);
    setMode('welcome');settings();
  });
  document.addEventListener('osint:view',e=>update(e.detail));
  document.addEventListener('osint:welcome',()=>{preview=false;setMode('welcome');window.scrollTo({top:0,behavior:'instant'});});
})();
