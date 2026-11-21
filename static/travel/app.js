import {transferDraft} from './workspace-draft.js';
import {setupDeparture} from './departure-picker.js';
import {liveEvents, liveRequest} from './api.js';
import {setupAccount} from './account.js';
import {travelConfig} from './config.js';
import {createDemo, exampleDates} from './demo.js';
import {setupGuide} from './guide.js';
import {drawPois, loadCatalog, onlineMap} from './pois.js';
import {setupReport} from './report.js';
import {setupRuns} from './runs.js';
import {setupTicket} from './ticket.js';
import {$, button, cny, make, show} from './ui.js';

const params = new URLSearchParams(location.search);

const fieldIds = {
  origin: 'origin', travelers: 'travelers', start_date: 'start-date', end_date: 'end-date', budget_fen: 'budget',
  budget_scope: 'budget-scope', include_round_trip: 'round-trip', poi_ids: 'task',
};
const fieldLabels = {
  origin: '出发地', travelers: '人数', start_date: '出发日', end_date: '返程日', budget_fen: '预算',
  budget_scope: '预算口径', include_round_trip: '往返费用范围', poi_ids: '目的地', ticket_fields: '票面信息',
};

void init();


function getDates() {
  const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', dateStyle: 'short'}).formatToParts(new Date());
  const part = key => Number(today.find(item => item.type === key).value);
  const day = offset => new Date(Date.UTC(part('year'), part('month') - 1, part('day') + offset)).toISOString().slice(0, 10);
  return {start_date: day(1), end_date: day(3)};
}
function readFen(raw) {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(raw);
  if (!match) return null;
  const amount = Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0'));
  return Number.isSafeInteger(amount) && amount >= 0 ? amount : null;
}
async function init() {
  const demo = params.get('demo') === 'travel' ? createDemo() : null;
  const request = demo?.request || liveRequest;
  const events = demo?.openEvents || liveEvents;
  const seeWeather = setupGuide();
  if (demo) {
    $('preview').textContent='预览行程需求 →';
    $('demo-banner').hidden = false;
    $('home-link').href = '/?demo=travel';
    $('demo-link').href = '/';
    $('demo-link').textContent = '切回服务模式';
  }
  const catalog = new Map();
  let visitorProfile;
  let selectedForDraft;
  let selectPoi;
  let version = 0;
  let previewing = false;
  let submitting = false;

  function accountView(){
    const active=['#account','#history'].includes(location.hash) && !document.documentElement.classList.contains('journal-mode');
    document.documentElement.classList.toggle('account-mode',active);
  }
  window.addEventListener('hashchange',accountView);
  accountView();
  const sessionExpired = () => show('notice', '会话已失效，请在页面下方重新登录。');
  let runs;
  function invalidate(userEdit = true) {
    if(userEdit)runs?.draftChanged();
    version++;
    $('draft-preview').hidden = true;
    $('draft-preview').replaceChildren();
    show('draft-error', '');
  }
  function suggest(){
    const node=$('suggest-poi');
    if(!selectPoi || !visitorProfile?.intended_region || !node)return;
    const options=[...catalog.values()].filter(p=>p.region===visitorProfile.intended_region);
    const interest=visitorProfile.interests?.trim();
    const walking=visitorProfile.walking_preference||'';
    const score=p=>{
      const text=p.name+' '+p.description+' '+(p.category||'');
      return (interest && text.includes(interest)?4:0) +
        (/轻松|低强度/.test(walking) && /公园|广场|古镇|街/.test(text)?1:0) +
        (/高强度|徒步/.test(walking) && /山|登|步道/.test(text)?1:0);
    };
    const poi=options.sort((a,b)=>score(b)-score(a))[0];
    if(!poi)return;
    node.hidden=false;
    node.replaceChildren('按你的旅行偏好推荐：',button(poi.name,()=>{location.hash='poi-card';accountView();selectPoi(poi.poi_id);$('poi-card').scrollIntoView();}));
  }
  const ticket = setupTicket(request, invalidate);
  const setInput = (id, value) => { $(id).value = value ?? ''; };
  const account = setupAccount(request, {
    demo, sku: travelConfig.demoOrderSku, outOfSession: sessionExpired,
    applyProfile(profile) {
      visitorProfile=profile;
      suggest();
      let filled = false;
      for (const key of ['origin', 'travelers', 'budget_scope']) {
        const id = fieldIds[key];
        if (!$(id).value && profile[key] != null && profile[key] !== '') {
          setInput(id, profile[key]);
          filled = true;
        }
      }
      if (filled) invalidate(false);
    },
  });
  const report = setupReport(request, catalog, !!demo);
  window.addEventListener('popstate',()=>{
    const id=new URLSearchParams(location.search).get('travel_report');
    if (!demo || params.has('travel_report')) return location.reload();
    if(id){
      const saved=JSON.parse(sessionStorage.getItem('travel-demo-report')||'null');
      if(saved?.id===id)report.showReport(saved);
    } else {
      report.clear();
      document.documentElement.classList.remove('journal-mode');
      document.title='贵州山水';
      accountView();
    }
  });
  if(params.has('travel_report') || params.has('history_report')){
    try {
      document.documentElement.classList.add('journal-mode');
      show('notice','正在打开报告…');
      const rid=params.get('travel_report') || params.get('history_report');
      const detail=demo?JSON.parse(sessionStorage.getItem('travel-demo-report')||'null'):await request('/api/runs/'+encodeURIComponent(rid));
      if(!detail || detail.id!==rid || detail.status!=='done')throw new Error('这份报告暂不可读，请返回旅行记录。');
      if(params.has('history_report') && !(detail.trip_plan || detail.summary?.trip_plan || detail.domain==='guizhou_travel')){
        location.replace('/static/workspace.html?record='+encodeURIComponent(rid));
        return;
      }
      for(const [id,poi] of await loadCatalog().catch(()=>[])) catalog.set(id,poi);
      show('notice','');
      report.showReport({...detail,trip_plan:detail.trip_plan || detail.summary?.trip_plan});
      void account.load();
    } catch(error){
      show('notice', error.status === 404 ? '当前账号找不到这份报告。请用创建行程时的账号查看旅行记录。' : error.message);
      const historyLink = make('a', '返回旅行记录');
      historyLink.href = '/#history';
      $('notice').append(' ', historyLink);
    }
    return;
  }
  runs = setupRuns(request, events, report, account.refresh);
  const input = $('task');
  const form = $('draft-form');

  const picker=setupDeparture(request,demo,invalidate);

  function plan(poi, example = false) {
    selectedForDraft = poi.poi_id;
    $('destination-name').textContent = `已选目的地：${poi.name || '梵净山'}`;
    $('draft-destination').hidden = false;
    if (example || !input.value.trim()) input.value = example
      ? '从贵阳北站出发，两人去梵净山三天两晚，全团预算2500元，妈妈下台阶膝盖吃力。'
      : '计划前往'+poi.name+'，请帮我安排旅行。';
    const dates = demo ? exampleDates : getDates();
    for (const [key, value] of Object.entries(dates)) if(example || !$(fieldIds[key]).value)setInput(fieldIds[key], value);
    if(example || !$('budget').value)setInput('budget',example?2500:2000);
    if (example) {
      $('travel-mode').value='high_speed';
      picker.choose({id:'rail:KQW',name:'贵阳北站'});
      setInput('travelers', 2);
      setInput('budget-scope', 'group');
      $('round-trip').checked = true;
    }
    invalidate();
    input.focus();
  }
  $('example').addEventListener('click', () => {
    selectPoi?.('fanjingshan', {openOnMobile: false});
    plan(catalog.get('fanjingshan') || {poi_id: 'fanjingshan'}, true);
  });
  $('clear-destination').addEventListener('click', () => {
    selectedForDraft = null;
    $('draft-destination').hidden = true;
    invalidate();
    input.focus();
  });
  form.addEventListener('input', () => invalidate());
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (previewing || submitting) return;
    const task = input.value.trim();
    if (!task) return show('draft-error', '请描述旅行需求，或从地图填入草稿。');
    if(!picker.confirmed())return;
    const origin = $('origin').value;
    const travelers = Number($('travelers').value);
    const startDate = $('start-date').value;
    const endDate = $('end-date').value;
    const budget = readFen($('budget').value);
    if (startDate && endDate && startDate > endDate) return show('draft-error', '返程日应为出发日或之后。');
    if ($('budget').value && budget === null) return show('draft-error', '请输入有效的人民币金额，最多两位小数。');
    const {attachmentIds, ticketFields, pending} = ticket();
    if (pending) return show('draft-error', '请先确认识别的票面信息，或移除该截图。');
    const overrides = {timezone: 'Asia/Shanghai', include_round_trip: $('round-trip').checked};
    overrides.travel_mode=$('travel-mode').value;
    overrides.departure_id=$('departure').value;
    if (origin) overrides.origin = origin;
    if (travelers) overrides.travelers = travelers;
    if (startDate) overrides.start_date = startDate;
    if (endDate) overrides.end_date = endDate;
    if (budget !== null) overrides.budget_fen = budget;
    if ($('budget-scope').value) overrides.budget_scope = $('budget-scope').value;
    if (selectedForDraft) overrides.poi_ids = [selectedForDraft];
    if (ticketFields && Object.keys(ticketFields).length) overrides.ticket_fields = ticketFields;
    const attempt = version;
    previewing = true;
    $('preview').disabled = true;
    show('draft-error', '');
    try {
      const data = await request('/api/travel/drafts/preview', {method: 'POST', body: {task, overrides, attachment_ids: attachmentIds}});
      if (attempt !== version) return;
      const area = $('draft-preview');
      area.hidden = false;
      area.replaceChildren(make('h3', '确认本次行程需求'));
      if (data.missing_fields?.length || !data.trip) {
        area.append(make('p', '还需要补充以下信息。'));
        for (const key of data.missing_fields ?? []) {
          area.append(button(fieldLabels[key] || key, () => $(fieldIds[key] || 'task').focus(), 'text-button'));
        }
        return;
      }
      const trip = data.trip;
      const destinations = (trip.poi_ids || []).map(id => catalog.get(id)?.name || id).join('、');
      if (!demo) {
        await transferDraft(trip, attachmentIds, destinations.split('、'), task);
        return;
      }
      const dateRange = [trip.start_date, trip.end_date].filter(Boolean).join(' 至 ');
      const scope = {person: '人均', group: '全团'}[trip.budget_scope] || '口径待确认';
      area.append(make('p', [trip.origin, destinations, dateRange, Number.isInteger(trip.travelers) ? `${trip.travelers} 人` : '', cny(trip.budget_fen), scope,
        typeof trip.include_round_trip === 'boolean' ? (trip.include_round_trip ? '含往返交通' : '往返交通另计') : ''].filter(Boolean).join(' · ')));
      if (ticketFields && Object.keys(ticketFields).length) {
        area.append(make('p', `已确认票面：${Object.values(ticketFields).join(' · ')}`));
      }
      const start = button('确认并开始 →', async () => {
        if (submitting || attempt !== version) return;
        submitting = true;
        start.disabled = $('preview').disabled = true;
        show('draft-error', '');
        try {
          await runs.start({domain: 'guizhou_travel', task, trip, report_format:'cards', attachment_ids: attachmentIds});
          invalidate(false);
        } catch (error) {
          if (error.status === 401) sessionExpired();
          show('draft-error', error.message);
        } finally {
          submitting = false;
          start.disabled = $('preview').disabled = false;
        }
      }, 'primary');
      area.append(start);
      $('progress').hidden=false;
      document.dispatchEvent(new CustomEvent('osint:view',{detail:{domain:'guizhou_travel',message:'草稿已准备好，确认日期和人数后，我来为你整理行程。'}}));
      document.dispatchEvent(new Event('travel:preview'));
    } catch (error) {
      if (attempt !== version) return;
      if (error.status === 401) sessionExpired();
      show('draft-error', error.status === 404 ? '文旅草稿服务尚未接通，可以体验样例回放。' : error.message);
    } finally {
      previewing = false;
      $('preview').disabled = submitting;
    }
  });

  void account.load();
  void runs.loadHistory();
  let weatherRegion;
  let weatherVersion=0;
  async function selectWeather(region){
    if(region===weatherRegion)return;
    weatherRegion=region;
    const version=++weatherVersion;
    seeWeather(null,region,true);
    try{
      const result=await request('/api/travel/home-weather'+(region==='贵阳'?'':'?region='+encodeURIComponent(region)));
      if(version===weatherVersion)seeWeather(result,region);
    }catch{if(version===weatherVersion)seeWeather(null,region);}
  }
  void selectWeather('贵阳');
  try {
    const rows = await loadCatalog();
    for (const [id, poi] of rows) catalog.set(id, poi);
    selectPoi = drawPois(catalog, plan, selectWeather);
    suggest();
    if (!demo) onlineMap(catalog, selectPoi, travelConfig);
  } catch (error) {
    $('poi-title').textContent = '景点目录暂不可用';
    show('notice', error.message);
  }
  if (params.get('run')) void runs.open(params.get('run'));
}
