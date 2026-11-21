import {liveRequest, requestId} from './api.js';
import {loadCatalog} from './pois.js';

const storageKey = 'travel-workspace-draft';
const modes = {high_speed:'高铁动车', rail:'普速铁路', air:'飞机', car:'自驾', coach:'长途汽车', tour:'跟团游'};

const ticketLabels = {date:'乘车日期', train_number:'车次', arrival_station:'到达站', arrival_time:'到达时间',
  reservation_date:'预约日期', latest_entry:'最晚入园时间'};

async function draftOwner() {
  try {
    return (await liveRequest('/api/auth/me')).username;
  } catch (error) {
    if (error.status === 404 && (await liveRequest('/api/health')).auth_enabled === false) return null;
    throw error;
  }
}

export async function transferDraft(trip, attachmentIds, names, note) {
  const username = await draftOwner();
  const text = [
    `我计划从${trip.origin}出发，以${modes[trip.travel_mode]}方式前往${names.join('、')}。`,
    `出游日期为${trip.start_date}至${trip.end_date}，共${trip.travelers}人同行。`,
    `全团预算为${trip.budget_fen / 100}元，${trip.include_round_trip ? '包含' : '不含'}往返交通。`,
    ...Object.entries(trip.ticket_fields || {}).filter(([,value]) => value).map(([key,value]) => '已核对'+ticketLabels[key]+'：'+value),
    note,
  ].join('\n');
  const draft = {id:requestId(), username, trip, attachmentIds, text};
  sessionStorage.setItem(storageKey, JSON.stringify(draft));
  location.assign('/static/workspace.html?draft='+encodeURIComponent(draft.id));
}

export async function restoreDraft(id) {
  const draft = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
  if (!draft || draft.id !== id) throw new Error('草稿已失效，请回到贵州旅行首页重新确认。');
  const username = await draftOwner();
  if (draft.username !== username) throw new Error('请用创建草稿的账号继续。');
  return draft;
}

function saveDraft(draft) {
  sessionStorage.setItem(storageKey, JSON.stringify(draft));
}

export async function startDraft(draft, text, settings) {
  if (await draftOwner() !== draft.username) throw new Error('当前登录账号已变更，请重新确认行程草稿。');
  const lines = text.split(/\n/);
  const route = lines[0]?.trim().match(/^我计划从(.+?)出发，以(.+?)方式前往(.+?)[。.]?$/);
  const dates = lines[1]?.trim().match(/^出游日期为(\d{4}-\d\d-\d\d)至(\d{4}-\d\d-\d\d)，共(\d+)人同行[。.]?$/);
  const budget = lines[2]?.trim().match(/^全团预算为(\d+(?:\.\d{1,2})?)元，(包含|不含)往返交通[。.]?$/);
  if (!route || !dates || !budget) throw new Error('请保留草稿前三行的句式，直接修改地点、日期、人数或金额。其他要求可在下方自由补充。');
  const mode = Object.keys(modes).find(key => modes[key] === route[2]);
  if (!mode) throw new Error('可用出行方式有'+Object.values(modes).join('、')+'。');
  const trip = {...draft.trip, travel_mode:mode, start_date:dates[1], end_date:dates[2], travelers:Number(dates[3]),
    budget_fen:Math.round(Number(budget[1])*100), include_round_trip:budget[2]==='包含'};
  if (dates[1] > dates[2] || !trip.travelers || trip.travelers > 10) throw new Error('请核对出游日期和同行人数（1–10 人）。');
  if (route[1] !== trip.origin || mode !== draft.trip.travel_mode) {
    const {items} = await liveRequest('/api/travel/departures?'+new URLSearchParams({mode,q:route[1]}));
    const matches = items.filter(item => item.name === route[1]);
    if (matches.length !== 1) throw new Error('请使用出发地目录中的完整名称。');
    trip.departure_id = matches[0].id;
    trip.origin = matches[0].name;
  }
  if (draft.trip.ticket_fields) {
    const ticket = {};
    for (const [key,label] of Object.entries(ticketLabels)) {
      const prefix='已核对'+label+'：';
      const line=lines.slice(3).find(line=>line.startsWith(prefix));
      if (line?.slice(prefix.length).trim()) ticket[key]=line.slice(prefix.length).trim();
    }
    trip.ticket_fields=ticket;
  }
  const catalog = await loadCatalog();
  trip.poi_ids = route[3].split('、').map(name => {
    const matches = [...catalog.values()].filter(row => row.name === name.trim());
    if (matches.length !== 1) throw new Error('请使用景点目录中的完整名称：'+name);
    return matches[0].poi_id;
  });
  const body = {...settings, domain:'guizhou_travel', task:text, trip, attachment_ids:draft.attachmentIds, report_format:'cards'};
  const signature = JSON.stringify(body);
  if (signature !== draft.signature) {draft.signature = signature; draft.key = requestId(); saveDraft(draft);}
  return liveRequest('/api/runs', {method:'POST', body, key:draft.key});
}
