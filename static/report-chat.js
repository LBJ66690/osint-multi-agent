/* 最终报告问答：与研究 SSE/阶段状态独立，响应按报告和版本隔离。 */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const pending = new Map(), cached = new Map(), retries = new Map();
  let current = null, opening = 0, trigger = null, selectedModel = 'qwen3.8-flash';
  const keyOf = c => JSON.stringify([c.runId,c.reportType]);
  const newId = () => crypto.randomUUID?.() || [...crypto.getRandomValues(new Uint8Array(16))].map(b=>b.toString(16).padStart(2,'0')).join('');
  function remember(key,data) {
    cached.set(key,data);
    if(cached.size>20)cached.delete(cached.keys().next().value);
  }
  async function request(url, body) {
    const response = await fetch(url, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    let data = {}; try { data = await response.json(); } catch {}
    if (!response.ok) throw new Error(typeof data.detail==='string' ? data.detail : `请求未完成 (${response.status})，请稍后重试。`);
    return data;
  }
  function hide(restore = true) {
    ++opening; $('#report-chat-modal').classList.add('hidden'); current = null;
    if (restore && trigger?.isConnected) trigger.focus();
  }
  function paragraph(text, cls) {
    const node = document.createElement('p'); node.className = cls || ''; node.textContent = text; return node;
  }
  function bubble(label, text, cls) {
    const box = document.createElement('article'); box.className = `chat-message ${cls}`;
    box.append(paragraph(label,'chat-message-label'),paragraph(text,'chat-message-text')); return box;
  }
  function render() {
    if (!current) return;
    const box = $('#report-chat-messages'), data = current.data;
    const job = pending.get(current.key), busy = !!job && job.version===data?.report_version;
    box.replaceChildren();
    for (const turn of data?.turns || []) {
      box.append(bubble('你', turn.question, 'chat-user'));
      const answer = bubble(`报告助手 · ${turn.model_id}`,turn.answer,'chat-assistant');
      answer.append(paragraph(`本次携带完整报告与 ${turn.context_turns??0} 轮历史对话${turn.usage?.total ? ` · ${turn.usage.total} Token` : ''}`,'chat-message-meta'));
      box.append(answer);
    }
    if (busy) {
      box.append(bubble('你',job.question,'chat-user'),bubble('报告助手','正在结合报告回答，请稍候…','chat-assistant chat-waiting'));
    } else if (!data?.turns?.length) {
      box.append(paragraph('你可以问：“这份报告最重要的结论是什么？”或“哪些结论还缺少证据？”','chat-empty'));
    }
    const available = [...$('#report-chat-model').options].some(o=>!o.disabled);
    const ready = current.loaded && current.modelsLoaded && available && data?.turns?.length < 50;
    $('#report-chat-model').disabled = !ready || !!job;
    $('#report-chat-question').disabled = !ready || !!job;
    $('#report-chat-send').disabled = !ready || !!job;
    $('#report-chat-send').textContent = job ? '回答中…' : '发送问题 ↗';
    $('#report-chat-error').textContent = current.error || '';
    $('#report-chat-status').textContent = !current.loaded ? (current.error?'暂时无法打开，可收起后重试。':'正在读取报告对话…') : busy ? '只处理本次提问，不重新搜索或生成报告。' : `${data?.turns?.length||0} / 50 轮 · 每次携带报告及最多最近 3 轮对话`;
    box.scrollTop = box.scrollHeight;
  }
  async function open(context) {
    if (!context?.runId || !context.reportType) return;
    const token = ++opening; trigger = document.activeElement;
    document.querySelectorAll('.modal-overlay').forEach(m=>m.classList.add('hidden'));
    const c = current = {...context,key:keyOf(context),data:null,loaded:false,modelsLoaded:false,error:''};
    $('#report-chat-title').textContent = `就报告提问 · ${context.reportType}`;
    $('#report-chat-question').value = '';
    $('#report-chat-model').replaceChildren();
    $('#report-chat-modal').classList.remove('hidden'); $('#report-chat-close').focus(); render();
    const url = `/api/runs/${encodeURIComponent(context.runId)}/report-chat?report_type=${encodeURIComponent(context.reportType)}`;
    try {
      const [data, models] = await Promise.all([request(url),request('/api/models')]);
      if (current!==c || token!==opening) return;
      const previous = cached.get(c.key);
      c.data = previous?.report_version===data.report_version && previous.turns.length>data.turns.length ? previous : data;
      remember(c.key,c.data);
      const retry=retries.get(c.key);
      if(retry?.version===c.data.report_version && !pending.has(c.key)) {
        c.retry=retry;c.error=retry.error||'';$('#report-chat-question').value=retry.question;
      }
      for (const m of models.models || []) {
        const option = new Option(`${m.label||m.id}${m.available===false?' · 当前不可用':''}`,m.id);
        option.disabled = m.available===false; $('#report-chat-model').append(option);
      }
      const usable = [...$('#report-chat-model').options].filter(o=>!o.disabled);
      $('#report-chat-model').value = usable.find(o=>o.value===selectedModel)?.value || usable[0]?.value || '';
      c.loaded=true; c.modelsLoaded=true;
      if (!usable.length) c.error='暂时没有可用问答模型，请检查账户额度。';
      render(); if (!pending.has(c.key) && usable.length) $('#report-chat-question').focus();
    } catch (error) {
      if (current===c && token===opening) { c.error=error.message; render(); }
    }
  }
  async function send() {
    const c=current;
    if (!c?.loaded || !c.modelsLoaded || pending.has(c.key)) return;
    const question=$('#report-chat-question').value.trim(), model=$('#report-chat-model').value;
    if (!question) { c.error='请输入要就报告提出的问题。';render();$('#report-chat-question').focus();return; }
    if (question.length>4000) { c.error='问题不能超过 4000 字符。';render();return; }
    const option=$('#report-chat-model').selectedOptions[0]; if (!option || option.disabled) return;
    selectedModel=model;
    // 失败后原文、模型和版本均不变时复用请求编号，避免网络断开后重复计费。
    const old=c.retry;
    const id=old?.question===question&&old.model===model&&old.version===c.data.report_version ? old.id : newId();
    const job={id,question,model,version:c.data.report_version};pending.set(c.key,job);c.error='';render();
    try {
      const data=await request(`/api/runs/${encodeURIComponent(c.runId)}/report-chat`,{
        report_type:c.reportType,report_version:job.version,model_id:model,question,request_id:id
      });
      remember(c.key,data);retries.delete(c.key);
      if (current?.key===c.key && current.data?.report_version===job.version) {
        current.data=data;current.retry=null;current.error='';$('#report-chat-question').value='';
      }
    } catch (error) {
      job.error=error.message;c.retry=job;retries.set(c.key,job);
      if(retries.size>20)retries.delete(retries.keys().next().value);
      if (current?.key===c.key && current.data?.report_version===job.version) {
        current.retry=job;current.error=error.message;$('#report-chat-question').value=question;
      }
    } finally {
      if (pending.get(c.key)===job) pending.delete(c.key);
      if (current?.key===c.key) {
        render();
        const input=$('#report-chat-question');
        if(!input.disabled && (document.activeElement===document.body || document.activeElement?.id==='report-chat-send')) input.focus();
      }
    }
  }
  document.addEventListener('osint:report-question',e=>open(e.detail));
  document.addEventListener('osint:report-chat-close',()=>hide(false));
  document.addEventListener('DOMContentLoaded',()=>{
    const modal=$('#report-chat-modal');
    $('#report-chat-close').onclick=()=>hide();
    $('#report-chat-form').onsubmit=e=>{e.preventDefault();send();};
    $('#report-chat-question').onkeydown=e=>{
      if (e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing) {e.preventDefault();send();}
    };
    modal.addEventListener('click',e=>{if(e.target===modal){e.stopImmediatePropagation();hide();}},true);
    modal.addEventListener('keydown',e=>{
      if(e.key==='Escape'){e.preventDefault();e.stopImmediatePropagation();hide();}
      if(e.key==='Tab'){
        e.stopImmediatePropagation();
        const nodes=[...modal.querySelectorAll('button,select,textarea,a[href]')].filter(n=>!n.disabled&&n.offsetParent!==null);
        if(!nodes.length)return;
        const first=nodes[0],last=nodes.at(-1);
        if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
        else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
      }
    },true);
  });
})();
