export const frozenDate = '2026-11-19';
export const exampleDates = {start_date: '2026-11-20', end_date: '2026-11-22'};
export const demoSku = 'fixture-trip';
const evidence = [
  {id: 'E1', title: '梵净山景区购票须知', source_url: 'https://fanjingshan.gz.cn/?page_id=1188',
    excerpt: '官方页面列出购票渠道和不同入园方向。'},
  {id: 'E2', title: '江口县天气回放样例', excerpt: '固定样例中江口次日可能有雨。', data_mode: 'fixture'},
];
const tripPlan = {
  outcome: 'ready',
  days: [
    {date: '2026-11-20', title: '贵阳 → 铜仁 → 江口', activities: ['从贵阳出发，抵达后前往江口住宿。']},
    {date: '2026-11-21', title: '梵净山东门', activities: ['按官方预约时间从东门入园。'], evidence_ids: ['E1']},
    {date: '2026-11-22', title: '江口 → 贵阳', activities: ['预留接驳和返程时间。']},
  ],
  budget: {known_lower_fen: 0, upper_fen: null, status: 'needs_confirmation',
    lines: [{label:'实际费用',lower_fen:0,upper_fen:null}]},
  railway: {'铜仁站': [{start_train_code:'演示车次',start_date:'2026-11-20',from_station:'贵阳北',to_station:'铜仁',start_time:'08:00',arrive_time:'09:45',prices:[{seat_name:'二等座',price:149,num:'样例有票'}]}]},
  weather: [{location:'江口县',data_mode:'fixture',days:[{date:'2026-11-21',weather_code:3,temperature_2m_min:8,temperature_2m_max:16,precipitation_probability_max:35,precipitation_sum:0.4,sunrise:'2026-11-21T07:12',sunset:'2026-11-21T17:51',hours:[
    {time:'2026-11-21T12:00',temperature_2m:16,apparent_temperature:15,relative_humidity_2m:69,weather_code:3},
    {time:'2026-11-21T21:00',temperature_2m:10,apparent_temperature:9,relative_humidity_2m:80,weather_code:2}]}]}],
  weather_alternatives: [{date: '2026-11-21', location: '江口县', text: '若步道受降雨影响，改为江口县内的室内活动，出发前复查公告。', evidence_ids: ['E2']}],
  booking_entries: [{poi_id: 'fanjingshan-east'}],
  media: [{poi_id: 'fanjingshan', image_id: 'fanjingshan-photo'}],
  evidence_ids: ['E1', 'E2'],
  unresolved: ['以上交通、价格与天气均为回放数据。实际出游前请复查。'],
};
const eventRows = [
  {type: 'tool_call', stage: 'collecting', tool: 'route.leg', call_id: 'r1', message: '正在查看东门接驳路线。'},
  {type: 'tool_result', stage: 'collecting', tool: 'rag.retrieve', call_id: 'q1', message: '已取回一条梵净山东门的游览经验。'},
  {type: 'tool_error', stage: 'collecting', tool: 'ticket.quote', call_id: 'q2', message: '指定日期的门票报价待查。'},
  {type: 'stage_start', stage: 'verifying', message: '正在核对行程和来源。'},
  {type: 'done', stage: 'verifying', message: '固定样例报告已保存。'},
];

export function createDemo() {
  const credits = {available: 1, reserved: 0, used: 0};
  const runs = new Map();
  const orders = new Map();
  const requests = new Map();
  let profile = {};
  let nextAttachment = 0;

  const getRun = id => {
    const run = runs.get(id);
    if (!run) throw Object.assign(new Error('这条样例任务已结束或已刷新页面。'), {status: 404});
    return run;
  };
  const setStatus = (run, status) => {
    if (run.status === 'running') {
      credits.reserved--;
      if (status === 'done') credits.used++;
      else credits.available++;
    }
    run.status = status;
  };
  function itinerary(run) {
    const plan = structuredClone(tripPlan);
    const sample = run.trip.departure_id === 'rail:KQW' && run.trip.poi_ids.join() === 'fanjingshan'
      && run.trip.start_date === exampleDates.start_date && run.trip.end_date === exampleDates.end_date
      && run.trip.travelers === 2 && run.trip.budget_fen === 250000
      && run.trip.budget_scope === 'group' && run.trip.include_round_trip;
    if (!sample || run.attachment_ids.length || run.trip.ticket_fields) {
      return {outcome: 'needs_confirmation', days: [], budget: {}, weather_alternatives: [],
        booking_entries: [], media: [], evidence_ids: [],
        unresolved: ['当前输入已保存。回放只提供标准梵净山示例的完整行程，请在服务模式预览自定义行程。']};
    }
    return plan;
  }
  function detail(run) {
    return {...run, events: [...run.events], data_mode: 'fixture',
      trip_plan: run.status === 'done' ? itinerary(run) : null,
      evidence: run.status === 'done' ? evidence : [],
      reports: run.status === 'done' ? [{type: '旅行报告',markdown:[run.task,...itinerary(run).days.flatMap(d=>[d.date,...(d.activities||[])]),...itinerary(run).unresolved].join('\n')}] : [],
    };
  }
  function creditError() {
    return Object.assign(new Error('可用额度不足，可先模拟购买一次任务。'), {status: 409, code: 'insufficient_credit'});
  }
  async function request(path, {method = 'GET', body, key} = {}) {
    if (path === '/api/auth/me') return {username: '样例游客'};
    if (path === '/api/health') return {auth_enabled: true};
    if (path === '/api/travel/home-weather') return {location: '贵阳', kind: 'observation', condition: '小雨', as_of: `${frozenDate}T09:55:00+08:00`, stale: false, data_mode: 'fixture'};
    if (path === '/api/travel/credits') return {...credits};
    if (path === '/api/travel/profile') {
      if (method === 'PATCH') profile = {...profile, ...body};
      return {...profile};
    }
    if (path === '/api/travel/attachments') {
      return {attachment_id: `demo-attachment-${++nextAttachment}`, suggested_fields: {
        date: '2026-11-20', arrival_time: '10:30', arrival_station: '铜仁站', train_number: 'G123',
      }};
    }
    if (path === '/api/travel/drafts/preview') {
      const trip = {...profile, ...body.overrides};
      const required = ['origin', 'start_date', 'end_date', 'travelers', 'budget_fen', 'budget_scope'];
      return {trip, missing_fields: required.filter(field => trip[field] == null || trip[field] === '').concat(trip.poi_ids?.length ? [] : ['poi_ids'])};
    }
    if (path === '/api/travel/demo-orders') {
      const id = `fixture-order-${orders.size + 1}`;
      const order = {order_id: id, status: 'pending', amount_fen: 100, credits: 1};
      orders.set(id, order);
      return {...order};
    }
    const payment = path.match(/^\/api\/travel\/demo-orders\/([^/]+)\/complete$/);
    if (payment) {
      const order = orders.get(payment[1]);
      if (order.status === 'pending') {
        order.status = body.outcome;
        if (body.outcome === 'success') credits.available++;
      }
      return {order: {...order}, balance: {...credits}};
    }
    if (path === '/api/runs' && method === 'POST') {
      const fixture = body.report_format==='cards' && itinerary({...body,attachment_ids:body.attachment_ids||[]}).days.length
        ? await fetch('/static/travel/data/fanjing-cards.fixture.json').then(r=>r.json()) : null;
      const signature = JSON.stringify(body);
      const prior = requests.get(key);
      if (prior) {
        if (signature !== prior.signature) throw Object.assign(new Error('试图重复使用已绑定的请求。'), {status: 409, code: 'idempotency_conflict'});
        return prior.response;
      }
      if (credits.available < 1) throw creditError();
      const run = {id: `demo-fjs-${runs.size + 1}`, ...structuredClone(body), rendered_format:fixture?'cards':'text', ...(fixture?{card_report:fixture}:{}), status: 'running', events: [], turns: [], offset: 0};
      runs.set(run.id, run);
      credits.available--;
      credits.reserved++;
      const response = {run_id: run.id, model_id: 'fixture'};
      requests.set(key, {signature, response});
      return response;
    }
    if (path === '/api/runs') return {runs: [...runs.values()].reverse().map(({id, task, status, domain}) => ({id, task, status, domain}))};
    if (path === '/api/models') return {models: [{id: 'fixture', label: '样例问答'}]};
    const match = path.match(/^\/api\/runs\/([^/?]+)(?:\/([^?]+))?/);
    if (!match) throw new Error('该操作在样例回放中不可用。');
    const run = getRun(match[1]);
    if (!match[2]) return detail(run);
    if (match[2] === 'cancel') {
      if (run.status === 'running') setStatus(run, 'cancelled');
      return {ok: true};
    }
    if (match[2] === 'resume') {
      if (run.status === 'cancelled' || run.status === 'error') {
        if (credits.available < 1) throw creditError();
        run.status = 'running';
        credits.available--;
        credits.reserved++;
      }
      return {ok: true};
    }
    if (match[2] === 'report-chat') {
      if (method === 'POST' && !run.turns.some(turn => turn.request_id === body.request_id)) {
        run.turns.push({request_id: body.request_id, question: body.question,
          answer: '样例回答：请在报告中展开「事实来源」，核对预约入口；实际入园信息请以景区公告为准。'});
      }
      return {turns: [...run.turns], report_version: 'fixture-v1'};
    }
    throw new Error(`未知的回放事件：${match[2]}`);
  }
  function openEvents(id, after, receive, connection) {
    const run = getRun(id);
    let cursor = after + 1;
    let timer;
    connection('open');
    const send = () => {
      if (cursor < run.events.length) {
        receive(String(cursor), run.events[cursor++]);
      } else if (run.status !== 'running') {
        receive('', {type: 'stream_end'});
        return;
      } else {
        const event = {...eventRows[run.offset++], run_id: id, data_mode: 'fixture'};
        run.events.push(event);
        receive(String(cursor++), event);
        if (event.type === 'done') setStatus(run, 'done');
      }
      timer = setTimeout(send, 450);
    };
    timer = setTimeout(send, 450);
    return () => clearTimeout(timer);
  }
  return {request, openEvents};
}
