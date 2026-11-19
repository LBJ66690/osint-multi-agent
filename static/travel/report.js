import {requestId, runPath} from './api.js';
import {$, booking, cny, dateTime, link, make, openDialog, scenic, show} from './ui.js';

const outcomes = {ready: '行程已整理', needs_confirmation: '需要确认', infeasible: '当前条件下不可行'};
const asList = value => Array.isArray(value) ? value : [];
const valueText = value => typeof value === 'string' ? value : value?.text || value?.title || value?.label || value?.message || '';
const citeId = evidence => evidence.id ?? evidence.evidence_id;

function evidenceSection(node, evidence, ids) {
  const selected = evidence.filter(item => !ids?.length || ids.includes(citeId(item)));
  if (!selected.length && !ids?.length) return;
  const details = make('details');
  details.append(make('summary', '事实来源'));
  const list = make('ul');
  for (const item of selected) {
    const row = make('li');
    row.append(link(`${citeId(item) || ''} ${item.title || '查看来源'}`.trim(), item.source_url || item.url));
    if (item.excerpt) row.append(make('p', item.excerpt));
    const checked = dateTime(item.retrieved_at || item.as_of);
    if (checked) row.append(make('small', `采集 ${checked}`));
    if (item.data_mode === 'fixture') row.append(make('small', ' · 回放样例'));
    list.append(row);
  }
  const found = new Set(selected.map(citeId));
  for (const id of ids ?? []) if (!found.has(id)) list.append(make('li', `${id} · 来源详情待提供`));
  details.append(list);
  node.append(details);
}
function appendRows(node, items) {
  if (!items.length) return;
  const list = make('ul');
  for (const item of items) {
    const text = valueText(item);
    if (text) list.append(make('li', text));
  }
  node.append(list);
}
function subsection(root, title) {
  const section = make('article');
  section.append(make('h3', title));
  root.append(section);
  return section;
}
function moneyRange(lower, upper) {
  if (Number.isInteger(lower) && Number.isInteger(upper) && lower === upper) return cny(lower);
  if (Number.isInteger(lower) && Number.isInteger(upper)) return `${cny(lower)}–${cny(upper)}`;
  if (Number.isInteger(lower)) return `${cny(lower)} 起 · 上限待查`;
  if (Number.isInteger(upper)) return `上限 ${cny(upper)} · 下限待查`;
  return '金额待查';
}

export function setupReport(request, catalog, demo) {
  let current;
  let chat;
  let generation = 0;
  let downloadURL;
  let retry;
  const report = $('report');
  const body = $('report-body');
  const download = $('download');
  function clear() {
    generation++;
    current = chat = retry = null;
    if ($('chat-dialog').open) $('chat-dialog').close();
    if (downloadURL) URL.revokeObjectURL(downloadURL);
    downloadURL = null;
    report.hidden = true;
  }
  window.addEventListener('pagehide', () => { if (downloadURL) URL.revokeObjectURL(downloadURL); });

  function showReport(detail) {
    clear();
    current = detail;
    const plan = detail.trip_plan;
    report.hidden = false;
    $('report-mode').textContent = detail.data_mode === 'fixture' || demo ? '样例回放' : detail.data_mode === 'cache' ? '缓存资料' : '';
    body.replaceChildren();
    $('open-chat').hidden = !detail.reports?.[0]?.type;
    download.hidden = !detail.reports?.length;
    if (!plan) {
      body.append(make('p', '报告已完成，结构化行程尚未提供。'));
    } else {
      body.append(make('p', outcomes[plan.outcome] || '请查看行程详情'));
      for (const day of asList(plan.days)) {
        const section = subsection(body, [day.date, day.title].filter(Boolean).join(' · ') || '行程安排');
        if (day.summary) section.append(make('p', day.summary));
        appendRows(section, asList(day.activities));
        if (day.evidence_ids?.length) evidenceSection(section, asList(detail.evidence), day.evidence_ids);
      }
      const budget = plan.budget;
      if (budget) {
        const section = subsection(body, '预算');
        section.append(make('p', moneyRange(budget.known_lower_fen, budget.upper_fen)));
        const lines = asList(budget.lines).map(row => `${row.label || '费用'}：${moneyRange(row.lower_fen, row.upper_fen)}`);
        appendRows(section, lines.concat(asList(budget.missing_costs)));
      }
      if (plan.weather_alternatives?.length) {
        const section = subsection(body, '天气与备选');
        for (const item of plan.weather_alternatives) {
          section.append(make('p', [item.date, item.location, valueText(item)].filter(Boolean).join(' · ')));
          if (item.evidence_ids?.length) evidenceSection(section, asList(detail.evidence), item.evidence_ids);
        }
      }
      if (plan.booking_entries?.length) {
        const section = subsection(body, '预约入口');
        for (const entry of plan.booking_entries) {
          const poi = catalog.get(entry.poi_id);
          const row = make('div');
          if (entry.name || poi?.name) row.append(make('p', entry.name || poi.name));
          row.append(booking(entry.type ? entry : poi?.booking));
          section.append(row);
        }
      }
      for (const item of asList(plan.media)) {
        const image = catalog.get(item.poi_id)?.images.find(row => row.image_id === item.image_id);
        if (image) body.append(scenic(item.caption ? {...image, caption: item.caption} : image));
      }
      evidenceSection(body, asList(detail.evidence), plan.evidence_ids);
      if (plan.unresolved?.length) appendRows(subsection(body, '待确认事项'), plan.unresolved);
    }
    if (demo) {
      const text = ['# 旅行报告 · 样例回放', ...Array.from(body.querySelectorAll('h3,p,li'), el => el.textContent)].join('\n\n');
      downloadURL = URL.createObjectURL(new Blob([text], {type: 'text/markdown;charset=utf-8'}));
      download.href = downloadURL;
      download.download = '旅行报告-样例回放.md';
    } else {
      download.href = `${runPath(detail.id)}/report.md`;
      download.download = '';
    }
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
  return {showReport, clear};
}
