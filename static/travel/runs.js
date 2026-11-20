import {requestId, runPath} from './api.js';
import {$, button, make, show} from './ui.js';

const domain = 'guizhou_travel';
const final = new Set(['done', 'error', 'cancelled']);
const labels = {running: '执行中', done: '已完成', cancelled: '已取消', error: '运行中断',
  awaiting_gate: '等待确认', awaiting_confirm: '等待确认', awaiting_sources: '等待选择'};
const readings = {collecting: '正在收集旅行资料。', planning: '正在规划路线。', analyzing: '正在整理资料。',
  reasoning: '正在核算行程。', reporting: '正在整理报告。', verifying: '正在核对行程和来源。'};

export function setupRuns(request, events, report, refreshBalance) {
  let run;
  let generation = 0;
  let stop;
  let activeRequest;
  let starting = false;
  let transition = false;
  let historyRequest = 0;
  function rememberRoute(id) {
    const url = new URL(location.href);
    url.searchParams.set('run', id);
    history.replaceState(history.state, '', url);
  }
  function closeStream() {
    stop?.();
    stop = null;
  }
  function view(message, options = {}) {
    $('progress').hidden = false;
    $('run-status').textContent = labels[run.status] || '等待服务端状态';
    show('run-message', '');
    $('cancel').hidden = run.status === 'loading' || final.has(run.status);
    $('resume').hidden = !['error', 'cancelled'].includes(run.status) || run.resumable === false;
    document.dispatchEvent(new CustomEvent('osint:view', {detail: {
      domain, id: run.id, status: run.status, message, ...options,
    }}));
  }
  function initialize(id, status) {
    closeStream();
    const nonce = ++generation;
    run = {id, status, last: -1, calls: new Set(), resumable: true, emptyEnds: 0};
    report.clear();
    if (status !== 'loading') rememberRoute(id);
    $('view-report').hidden=true;
    $('reload-run').hidden = status !== 'loading';
    return nonce;
  }
  function eventText(event) {
    return event.message || (event.type === 'tool_error' ? '工具查询失败，相关信息待查。' : readings[event.stage]);
  }
  function track(event, id, snapshot = false) {
    if (event.run_id && event.run_id !== run.id) return;
    if (event.type === 'stream_end') return;
    if (Number.isInteger(id) && id >= 0) {
      if (id <= run.last) return;
      run.last = id;
      run.emptyEnds = 0;
    }
    if (event.call_id && event.type.startsWith('tool_')) {
      const key = JSON.stringify([event.call_id, event.type]);
      if (run.calls.has(key)) return;
      run.calls.add(key);
    }
    if (snapshot) return;
    if (['tool_call', 'tool_result', 'tool_error'].includes(event.type)) {
      document.dispatchEvent(new CustomEvent('osint:effect', {detail: {...event, domain}}));
    } else if (!final.has(event.type)) {
      if (event.type === 'resumed') run.status = 'running';
      if (event.type.startsWith('awaiting_')) run.status = event.type;
      view(eventText(event));
    } else if (event.type === 'done') {
      view('正在确认最终报告。', {snapshot: true});
    } else {
      view(eventText(event) || '正在确认任务状态。', {snapshot: true});
    }
  }
  function connect(nonce) {
    closeStream();
    const id = run.id;
    let checkingSession = false;
    stop = events(id, run.last, (eventId, event) => {
      if (generation !== nonce || typeof event?.type !== 'string') return;
      if (event.type === 'stream_end') {
        closeStream();
        void readDetail(nonce, true);
      } else {
        track(event, eventId ? Number(eventId) : null);
      }
    }, async state => {
      if (generation !== nonce) return;
      if (state === 'retry') {
        view('连接中断，正在重连。后台可能仍在执行。', {snapshot: true});
        $('reload-run').hidden = false;
        if (!checkingSession) {
          checkingSession = true;
          try { await request('/api/auth/me'); }
          catch (error) {
            if (generation === nonce && error.status === 401) {
              closeStream();
              show('notice', '会话已失效，请重新登录后查看旅行记录。');
            }
          } finally { checkingSession = false; }
        }
      } else {
        $('reload-run').hidden = true;
        view('已连接，正在等待任务进展。', {snapshot: true});
      }
    });
  }
  async function readDetail(nonce, followStream = false) {
    try {
      const detail = await request(runPath(run.id));
      if (generation !== nonce) return;
      if ((detail.domain ?? detail.summary?.domain) !== domain && !detail.trip_plan && run.status === 'loading') {
        throw new Error('这条任务不属于文旅工作台。');
      }
      if (!detail.id) detail.id = run.id;
      rememberRoute(run.id);
      for (const [id, event] of (detail.events || []).entries()) {
        if (typeof event?.type === 'string') track(event, Number.isInteger(event.id) ? event.id : id, true);
      }
      run.status = detail.status;
      run.resumable = detail.resume_info?.unavailable !== true;
      $('reload-run').hidden = true;
      if (detail.status === 'done') {
        report.offer(detail);
        view('旅行报告已完成，可以查看路线、预算和来源。', {snapshot: !followStream, completed: true});
      } else if (detail.status === 'error') {
        view(detail.error?.message || detail.summary?.error?.message || '任务运行失败，可查看进度和恢复选项。', {snapshot: true});
      } else if (detail.status === 'cancelled') {
        view('任务已取消，可按服务端状态从断点继续。', {snapshot: true});
      } else {
        if (followStream) {
          run.emptyEnds++;
          const retry = run.emptyEnds <= 3;
          view(retry ? '流已断开，正在重新连接。' : '进度流暂不可用，可手动重新读取。', {snapshot: true});
          $('reload-run').hidden = false;
          if (retry) setTimeout(() => { if (nonce === generation && !stop) connect(nonce); }, run.emptyEnds * 500);
        } else {
          run.emptyEnds = 0;
          view('已读取进度。', {snapshot: true});
          connect(nonce);
        }
      }
      void refreshBalance();
      void loadHistory();
    } catch (error) {
      if (generation !== nonce) return;
      view(error.message, {snapshot: true});
      $('reload-run').hidden = false;
      if (error.status === 401) show('notice', '会话已失效，请重新登录。');
    }
  }
  async function open(id) {
    const nonce = initialize(id, 'loading');
    view('正在读取任务…', {snapshot: true, newRun: true});
    $('cancel').hidden = true;
    await readDetail(nonce);
  }
  async function loadHistory() {
    const attempt = ++historyRequest;
    const list = $('history-list');
    try {
      const data = await request('/api/runs');
      if (attempt !== historyRequest) return;
      list.replaceChildren();
      for (const row of data.runs || []) {
        if ((row.domain ?? row.summary?.domain) !== domain || !(row.id || row.run_id)) continue;
        const item = make('li');
        item.append(button(`${(row.task || '旅行任务').replace(/^帮我拟一个|^帮我拟一份/,'').replace(/的旅游计划。?$/,'')} · ${labels[row.status] || row.status}`, async () => {
          await open(row.id || row.run_id);
          $('progress').scrollIntoView({block: 'start'});
        }, 'text-button'));
        list.append(item);
      }
      if ((data.runs || []).some(row => !Object.hasOwn(row, 'domain') && !row.summary?.domain)) {
        const link = make('a', '打开工作台历史记录');
        link.href = '/?workspace=research';
        const item = make('li');
        item.append(link);
        list.append(item);
      }
      if (!list.childElementCount) list.append(make('li', '暂无旅行记录'));
    } catch (error) {
      if (attempt === historyRequest) list.replaceChildren(make('li', error.status === 401 ? '登录后查看旅行记录' : '暂时无法读取旅行记录'));
    }
  }
  async function start(body) {
    if (starting || (run && run.status !== 'loading' && !final.has(run.status))) throw new Error('已有行程正在执行，请先查看当前进度。');
    const signature = JSON.stringify(body);
    if (activeRequest?.signature !== signature) activeRequest = {signature, key: requestId()};
    starting = true;
    try {
      const result = await request('/api/runs', {method: 'POST', body, key: activeRequest.key});
      if (!result.run_id) throw new Error('服务尚未返回任务编号，请重试。');
      activeRequest = null;
      const nonce = initialize(result.run_id, 'running');
      view('已提交，等待真实进度。', {snapshot: true, newRun: true});
      $('progress').scrollIntoView({block: 'start'});
      connect(nonce);
      void refreshBalance();
      void loadHistory();
    } catch (error) {
      if (error.code === 'insufficient_credit') void refreshBalance();
      if (error.code === 'idempotency_conflict') activeRequest = null;
      throw error;
    } finally { starting = false; }
  }
  async function change(action) {
    if (!run || transition || run.status === 'loading') return;
    transition = true;
    $(action).disabled = true;
    const nonce = ++generation;
    const id = run.id;
    closeStream();
    try {
      await request(`${runPath(id)}/${action}`, {method: 'POST', body: {}});
      if (nonce !== generation) return;
      await readDetail(nonce);
    } catch (error) {
      if (nonce === generation) {
        view(error.message, {snapshot: true});
        $('reload-run').hidden = false;
        if (!final.has(run.status)) connect(nonce);
      }
    } finally {
      transition = false;
      $(action).disabled = false;
    }
  }
  $('cancel').addEventListener('click', () => change('cancel'));
  $('resume').addEventListener('click', () => change('resume'));
  $('reload-run').addEventListener('click', () => {
    if (!run) return;
    closeStream();
    void readDetail(++generation);
  });
  $('load-history').addEventListener('click', loadHistory);
  window.addEventListener('pagehide', closeStream);
  window.addEventListener('pageshow', event => { if (event.persisted && run && !final.has(run.status)) void readDetail(++generation); });
  return {open, loadHistory, start};
}
