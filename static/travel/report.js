import {renderFormat} from './cards.js';
import {renderJourney} from './journey.js';
import {requestId, runPath} from './api.js';
import {$, make, show} from './ui.js';

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
    $('guide-chat').hidden = true;
    $('question').value = '';
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
    $('guide-chat').hidden=!detail.reports?.length;
    if (!$('guide-chat').hidden) void loadChat();
    $('download').hidden=true;
    $('report-mode').textContent=demo?'样例回放':'';
    $('guide-greet').setAttribute('aria-label','与导游互动并重听行程摘要');
    const message=renderJourney(body,detail,catalog,demo ? null : request);
    renderFormat(body,detail);
    document.dispatchEvent(new CustomEvent('osint:view',{detail:{domain:'guizhou_travel',message,completed:true}}));
    if(demo) body.querySelector('.journey-hero p').textContent='贵州山水 · 样例回放';
    document.title='旅行报告 · 贵州山水';
    window.scrollTo(0,0);
  }
  function reportLink(entry,id,detail) {
    const url=new URL('/',location.origin);
    url.searchParams.set('travel_report',id);
    if(demo){
      url.searchParams.set('demo','travel');
      entry.onclick=async event=>{
        event.preventDefault();
        const record=detail || await request(runPath(id));
        sessionStorage.setItem('travel-demo-report',JSON.stringify(record));
        history.pushState(null,'',url);
        document.documentElement.classList.remove('account-mode');
        showReport(record);
      };
    }
    entry.href=url;
    return entry;
  }
  function historyLink(id,label){return reportLink(make('a',label),id);}
  function offer(detail){
    reportLink($('view-report'),detail.id,detail).hidden=false;
  }
  function reportExcerpt(question) {
    const sections=[
      [/天气|气温|下雨|降雨|温度|带伞/,'目的地天气'],
      [/高铁|火车|班次|余票|车票/,'铁路班次与余票'],
      [/接驳|到站|末班/,'到站后的接驳'],
      [/预约|费用|预算|门票/,'费用与预约'],
      [/吃|餐|住宿|酒店/,'餐食 · 住宿 · 玩乐'],
      [/行程|安排|游览|第.{1,3}天|玩法/,'每日安排'],
    ];
    const title=sections.find(([pattern])=>pattern.test(question))?.[1];
    const card=Array.from(body.querySelectorAll('.journey-card')).find(el=>el.querySelector('h2')?.textContent===title);
    if(!card)return '这份报告里暂时找不到能直接回答该问题的资料。';
    const lines=Array.from(card.querySelectorAll('p,summary,.reading,.temperature,.condition,small,a'))
      .filter(el=>!el.matches('.reading small'))
      .map(el=>el.textContent.trim()).filter(Boolean);
    return title+'\n'+lines.slice(0,9).join('\n');
  }
  function renderChat() {
    const turns = $('chat-turns');
    turns.replaceChildren();
    for (const turn of [...asList(chat?.data?.turns),...asList(chat?.excerpts)]) {
      turns.append(make('p', `游客：${turn.question || ''}`), make('p', `回答：${turn.answer || ''}`));
    }
    turns.scrollTop = turns.scrollHeight;
    $('ask').disabled = !chat?.ready || !!chat.busy;
  }
  async function loadChat() {
    if (!current) return;
    const id = current.id;
    const type = current.reports[0].type;
    const url = `${runPath(id)}/report-chat?report_type=${encodeURIComponent(type)}`;
    const attempt = ++generation;
    if (chat?.id !== id) chat = {id, type, data: null};
    show('chat-error', '');
    renderChat();
    try {
      const [data, models] = await Promise.all([request(url), request('/api/models')]);
      if (attempt !== generation) return;
      chat.data = data;
      chat.model = asList(models.models).find(m => m.available !== false)?.id;
      chat.ready=true;
      if (!chat.model) show('chat-error', '当前可查询报告摘录。');
      renderChat();
    } catch (error) {
      if (attempt === generation) show('chat-error', error.message);
    }
  }
  $('chat-form').addEventListener('submit', async event => {
    event.preventDefault();
    if (!chat?.data || chat.busy) return;
    const model_id = chat.model;
    const question = $('question').value.trim();
    if (!question) return;
    function excerpt(){
      (chat.excerpts ||= []).push({question,answer:'报告摘录 · 本页对话\n'+reportExcerpt(question)});
      $('question').value='';
    }
    if (!model_id) {excerpt();renderChat();return;}
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
      if (chat === active) {
        if ([502,503,504].includes(error.status) || (error.status===400 && /额度不可用/.test(error.message))) {
          excerpt();
          show('chat-error','在线问答暂不可用，已从当前报告中查找相关内容。');
        } else show('chat-error', error.message);
      }
    } finally {
      active.busy = false;
      if (chat === active) renderChat();
    }
  });
  return {showReport, clear, offer, historyLink};
}
