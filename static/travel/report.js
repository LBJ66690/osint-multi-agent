import {renderJourney} from './journey.js';
import {requestId, runPath} from './api.js';
import {$, make, openDialog, show} from './ui.js';

const asList = value => Array.isArray(value) ? value : [];
export function setupReport(request, catalog, demo) {
  let current;
  let chat;
  let generation = 0;
  let retry;
  const report = $('report');
  const body = $('report-body');
  function clear() {
    generation++;
    current = chat = retry = null;
    if ($('chat-dialog').open) $('chat-dialog').close();
    report.hidden = true;
  }

  function showReport(detail) {
    clear();
    current = detail;
    document.documentElement.classList.add('journal-mode');
    report.hidden=false;
    $('progress').hidden=false;
    $('account-entry').href='/#account';
    $('account-entry').textContent='我的账号';
    $('open-chat').hidden=!detail.reports?.length;
    $('download').hidden=true;
    $('report-mode').textContent=demo?'样例回放':'';
    $('guide-greet').setAttribute('aria-label','与导游互动并重听行程摘要');
    const message=renderJourney(body,detail,catalog);
    document.dispatchEvent(new CustomEvent('osint:view',{detail:{domain:'guizhou_travel',message,completed:true}}));
    if(demo) body.querySelector('.journey-hero p').textContent='贵州山水 · 样例回放';
    document.title='旅行报告 · 贵州山水';
    window.scrollTo(0,0);
  }
  function offer(detail){
    const url=new URL('/',location.origin);
    url.searchParams.set('travel_report',detail.id);
    const entry=$('view-report');
    if(demo){
      url.searchParams.set('demo','travel');
      entry.onclick=event=>{
        event.preventDefault();
        sessionStorage.setItem('travel-demo-report',JSON.stringify(detail));
        history.pushState(null,'',url);
        showReport(detail);
      };
    }
    entry.href=url;
    entry.hidden=false;
  }
  function renderChat() {
    const turns = $('chat-turns');
    turns.replaceChildren();
    for (const turn of asList(chat?.data?.turns)) {
      turns.append(make('p', `游客：${turn.question || ''}`), make('p', `回答：${turn.answer || ''}`));
    }
    turns.scrollTop = turns.scrollHeight;
    $('ask').disabled = !chat?.data?.report_version || !$('chat-model').value || !!chat.busy;
  }
  $('chat-model').addEventListener('change', renderChat);
  $('open-chat').addEventListener('click', async () => {
    if (!current) return;
    const id = current.id;
    const type = current.reports[0].type;
    const url = `${runPath(id)}/report-chat?report_type=${encodeURIComponent(type)}`;
    const attempt = ++generation;
    if (chat?.id !== id) chat = {id, type, data: null};
    $('chat-model').replaceChildren();
    show('chat-error', '');
    openDialog('chat-dialog');
    renderChat();
    try {
      const [data, models] = await Promise.all([request(url), request('/api/models')]);
      if (attempt !== generation) return;
      chat.data = data;
      for (const model of asList(models.models).filter(m => m.available !== false)) {
        const option = make('option', model.label || model.id);
        option.value = model.id;
        $('chat-model').append(option);
      }
      if (!$('chat-model').value) show('chat-error', '没有可用的问答模型。');
      renderChat();
    } catch (error) {
      if (attempt === generation) show('chat-error', error.message);
    }
  });
  $('chat-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!chat?.data || chat.busy) return;
    const model_id = $('chat-model').value;
    const question = $('question').value.trim();
    if (!question || !model_id) return;
    const {report_version} = chat.data;
    const signature = JSON.stringify([chat.id, chat.type, model_id, question, report_version]);
    if (retry?.signature !== signature) retry = {signature, key: requestId()};
    const active = chat;
    active.busy = true;
    renderChat();
    show('chat-error', '');
    try {
      const data = await request(`${runPath(chat.id)}/report-chat`, {method: 'POST', body: {
        report_type: chat.type, report_version, model_id, question, request_id: retry.key,
      }});
      if (chat !== active) return;
      chat.data = data;
      retry = null;
      $('question').value = '';
    } catch (error) {
      if (chat === active) show('chat-error', error.message);
    } finally {
      active.busy = false;
      if (chat === active) renderChat();
    }
  });
  return {showReport, clear, offer};
}
