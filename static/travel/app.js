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

if (params.get('workspace') !== 'research') void init();

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
    $('demo-banner').hidden = false;
    $('home-link').href = '/?demo=travel';
    $('demo-link').href = '/';
    $('demo-link').textContent = '切回服务模式';
  }
  const catalog = new Map();
  let selectedForDraft;
  let selectPoi;
  let version = 0;
  let previewing = false;
  let submitting = false;

  const sessionExpired = () => show('notice', '会话已失效，请在页面下方重新登录。');
  function invalidate() {
    version++;
    $('draft-preview').hidden = true;
    $('draft-preview').replaceChildren();
    show('draft-error', '');
  }
  const ticket = setupTicket(request, invalidate);
  const setInput = (id, value) => { $(id).value = value ?? ''; };
  const account = setupAccount(request, {
    demo, sku: travelConfig.demoOrderSku, outOfSession: sessionExpired,
    applyProfile(profile) {
      let filled = false;
      for (const key of ['origin', 'travelers', 'budget_scope']) {
        const id = fieldIds[key];
        if (!$(id).value && profile[key] != null && profile[key] !== '') {
          setInput(id, profile[key]);
          filled = true;
        }
      }
      if (filled) invalidate();
    },
  });
  const report = setupReport(request, catalog, !!demo);
  const runs = setupRuns(request, events, report, account.refresh);
  const input = $('task');
  const form = $('draft-form');

  function plan(poi, example = false) {
    selectedForDraft = poi.poi_id;
    $('destination-name').textContent = `已选目的地：${poi.name || '梵净山'}`;
    $('draft-destination').hidden = false;
    input.value = example
      ? '帮我拟一个明天从贵阳去梵净山的三天、不超过2000元的旅游计划。'
      : `帮我拟一个明天去${poi.name}的三天、不超过 2000 元预算的旅游计划。`;
    const dates = demo ? exampleDates : getDates();
    for (const [key, value] of Object.entries(dates)) setInput(fieldIds[key], value);
    setInput('budget', 2000);
    if (example) {
      setInput('origin', '贵阳');
      setInput('travelers', 1);
      setInput('budget-scope', 'group');
      $('round-trip').checked = true;
    }
    invalidate();
    input.focus();
  }
  $('example').addEventListener('click', () => {
    selectPoi?.('fanjingshan', false);
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
    const origin = $('origin').value.trim();
    const travelers = Number($('travelers').value);
    const startDate = $('start-date').value;
    const endDate = $('end-date').value;
    const budget = readFen($('budget').value);
    if (startDate && endDate && startDate > endDate) return show('draft-error', '返程日应为出发日或之后。');
    if ($('budget').value && budget === null) return show('draft-error', '请输入有效的人民币金额，最多两位小数。');
    const {attachmentIds, ticketFields, pending} = ticket();
    if (pending) return show('draft-error', '请先确认识别的票面信息，或移除该截图。');
    const overrides = {timezone: 'Asia/Shanghai', include_round_trip: $('round-trip').checked};
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
          await runs.start({domain: 'guizhou_travel', task, trip, attachment_ids: attachmentIds});
          invalidate();
        } catch (error) {
          if (error.status === 401) sessionExpired();
          show('draft-error', error.message);
        } finally {
          submitting = false;
          start.disabled = $('preview').disabled = false;
        }
      }, 'primary');
      area.append(start);
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
  void request('/api/travel/home-weather').then(seeWeather, () => seeWeather(null));
  try {
    const rows = await loadCatalog();
    for (const [id, poi] of rows) catalog.set(id, poi);
    selectPoi = drawPois(catalog, plan);
    if (!demo) void onlineMap(catalog, selectPoi, travelConfig);
  } catch (error) {
    $('poi-title').textContent = '景点目录暂不可用';
    show('notice', error.message);
  }
  if (params.get('run')) void runs.open(params.get('run'));
}
