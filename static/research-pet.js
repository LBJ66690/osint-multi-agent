/* A read-only companion: listens to existing snapshots/events, never starts a
   request, stores task content, or changes execution/checkpoint state. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const phases = {
    planning: ['规划中', 'thinking', '规划官正在拆解问题，我把小本本准备好啦。'],
    collecting: ['收集中', 'searching', '收集官出发找公开资料啦！有网页返回，我就告诉你。'],
    analyzing: ['分析中', 'thinking', '分析官正在整理实体和观点，让线索慢慢排好队～'],
    reasoning: ['推理中', 'thinking', '推理官正在对照依据和反证，我也认真想一想。'],
    reporting: ['写报告', 'writing', '报告官开始整理结论啦，我来帮忙递纸！'],
    verifying: ['核查中', 'checking', '正在核查报告。先别急着下结论，看看依据和局限。']
  };
  let root, snapshot = {}, stateKey = '', lastMessage = '', lastBroadcast = 0;
  let queueTimer, queued = null, cuddleTimer, interactionText = '', pets = 0, lastPet = -Infinity;
  let collapsed = false, collapsePreference = null, announcements = true, voiceOn = false, utterance = null, welcomeLayout = false;
  let pointer = null, suppressClickUntil = 0;
  const seenCalls = new Map();
  const narrow = matchMedia('(max-width: 950px)');
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, String(value)); } catch { /* Optional preferences must not break the app. */ } };
  const stopSpeech = () => {
    if (!utterance) return;
    utterance = null;
    try { window.speechSynthesis?.cancel(); } catch { /* Text remains available. */ }
  };
  function localVoice() {
    try { return window.speechSynthesis?.getVoices().find(v => v.localService === true && /^zh(?:-|_|$)/i.test(v.lang)); }
    catch { return null; }
  }
  function speak(text) {
    if (!voiceOn || !announcements || collapsed || document.hidden || $('.modal-overlay:not(.hidden)')) return;
    const voice = localVoice();
    if (!voice || typeof window.SpeechSynthesisUtterance !== 'function') { disableVoice('本地中文语音暂不可用，气泡播报仍可用'); return; }
    try {
      stopSpeech();
      const job = new SpeechSynthesisUtterance(text);
      job.voice = voice; job.lang = voice.lang; job.rate = 1.08; job.pitch = 1.12;
      job.onend = () => { if (utterance === job) utterance = null; };
      job.onerror = () => { if (utterance === job) { utterance = null; disableVoice('语音未能播放，继续看气泡就好'); } };
      utterance = job;
      window.speechSynthesis.speak(job);
    } catch { disableVoice('语音未能播放，继续看气泡就好'); }
  }
  function disableVoice(note) {
    stopSpeech(); voiceOn = false;
    $('#fish-voice').setAttribute('aria-pressed', 'false'); $('#fish-voice').textContent = '语音：关';
    if (note) $('#fish-note').textContent = note;
  }
  function renderBubble() {
    const text = interactionText || (announcements ? lastMessage : '进度播报已暂停，我陪你安静摸鱼～');
    const bubble = $('#fish-bubble');
    if (bubble.textContent !== text) bubble.textContent = text;
  }
  function clearQueue() { clearTimeout(queueTimer); queueTimer = null; queued = null; }
  function broadcast(text, {urgent = false, audible = true} = {}) {
    clearQueue();
    lastMessage = text;
    if (urgent) { interactionText = ''; }
    renderBubble(); lastBroadcast = Date.now();
    if (audible) speak(text);
  }
  function queue(text) {
    // Keep just the newest update; don't build a queue for every search/chunk.
    const delay = Math.max(0, 2200 - (Date.now() - lastBroadcast));
    if (!delay) { broadcast(text); return; }
    queued = {run: snapshot.id, stage: snapshot.current, text};
    if (queueTimer) return;
    queueTimer = setTimeout(() => {
      const next = queued; queueTimer = null; queued = null;
      if (next && next.run === snapshot.id && next.stage === snapshot.current && snapshot.status === 'running' && !snapshot.pending) broadcast(next.text);
    }, delay);
  }
  function describe(data) {
    if (!data.id) return {status: '待命 · 陪你研究', mood: 'idle', text: '我是大肥鱼，研究进度我来盯。先摸摸我，沾点灵感～'};
    if (data.status === 'done') return {status: '研究已完成', mood: 'celebrate', text: data.reports ? '报告送到啦！记得一起查看引用来源和核查记录。' : '这次研究已结束，阶段产出可以回看啦。', go: data.reports ? 'report' : ''};
    if (data.status === 'error') return {status: '研究暂时中断', mood: 'worried', text: '研究碰到一点问题。先看看错误提示，我们再决定怎么继续。', go: 'error'};
    if (data.status === 'cancelled') return {status: '任务已停止', mood: 'idle', text: '这次任务已停止，我先陪你休息一会儿～'};
    if (data.pending || String(data.status).startsWith('awaiting_')) {
      const waiting = {awaiting_confirm: '计划准备好啦！确认研究计划后，我们再出发。', awaiting_sources: '搜索通道等你选择。你选好以后，收集官才会开始找资料。', awaiting_gate: '核查有待处理的问题，需要你决定重做还是继续。'};
      return {status: '等待你的操作', mood: 'waiting', text: waiting[data.status] || '现在需要你处理一个待办，我在这里等你。', go: 'pending'};
    }
    const [status, mood, text] = phases[data.current] || ['准备执行', 'idle', '任务已收到，等待下一条实际执行事件。'];
    return {status, mood, text};
  }
  function update(data) {
    if (!root || !data || typeof data !== 'object') return;
    const switched = data.id !== snapshot.id;
    if (switched) {
      clearQueue(); clearTimeout(cuddleTimer); stopSpeech();
      seenCalls.clear(); interactionText = ''; root.classList.remove('is-petted');
    }
    snapshot = data;
    const info = describe(data);
    root.dataset.mood = info.mood; root.dataset.executionStage = data.current || '';
    $('#fish-status').textContent = info.status;
    const go = $('#fish-go');
    go.classList.toggle('hidden', !info.go); go.dataset.action = info.go || '';
    go.textContent = {report: '看看最终报告 ↗', pending: '去处理待办 ↗', error: '查看中断信息 ↗'}[info.go] || '';
    const key = JSON.stringify([data.id, data.status, data.current, !!data.pending, data.reports]);
    if (key !== stateKey) {
      stateKey = key;
      // Opening a saved task must not replay its historical announcements.
      broadcast(info.text, {urgent: true, audible: !switched});
    }
  }
  function effect(event) {
    if (!root || !event || !snapshot.id || (event.run_id && event.run_id !== snapshot.id)) return;
    if (snapshot.status !== 'running' || snapshot.pending) return;
    if (event.stage && event.stage !== snapshot.current) return;
    if (event.type === 'tool_result' && snapshot.current === 'collecting') {
      const n = Number.isInteger(event.result_count) ? event.result_count : Array.isArray(event.items) ? event.items.length : null;
      if (n != null && (n < 0 || n > 100000)) return;
      const signature = JSON.stringify([event.tool, n]);
      if (event.call_id && seenCalls.get(event.call_id) === signature) return;
      if (event.call_id) { seenCalls.set(event.call_id, signature); if (seenCalls.size > 120) seenCalls.delete(seenCalls.keys().next().value); }
      const source = {parallel: 'Parallel', you: 'You.com', bailian: '百炼'}[event.tool] || '搜索通道';
      queue(n === null ? `${source} 已返回内容，数量以通道面板为准。` : n === 0 ? `${source} 这次没有找到网页，先看看其他查询的结果。` : `${source} 刚带回 ${n} 条网页资料，我接住啦！`);
    } else if (event.type === 'tool_error' && snapshot.current === 'collecting') {
      queue('有一个搜索请求没有成功，具体情况可以查看搜索通道面板。');
    } else if (event.type === 'collection_progress' && snapshot.current === 'collecting') {
      queue('又完成一个子问题的检索！资料数量可以看下方指标卡。');
    } else if (event.type === 'report' && snapshot.current === 'reporting') {
      queue('一份报告已经生成，还要留意后续核查结果哦。');
    } else if (event.type === 'resumed') {
      broadcast('从断点继续啦！我会跟着实际恢复的阶段播报。', {urgent: true});
    }
  }
  function setCollapsed(value) {
    if (welcomeLayout) value = false;
    collapsed = value; root.dataset.collapsed = String(value);
    $('#fish-details').hidden = value;
    $('#fish-collapse').setAttribute('aria-expanded', String(!value));
    $('#fish-collapse').textContent = value ? '展开 ＋' : '收起 −';
    $('#fish-collapse').title = value ? '展开大肥鱼' : '收起大肥鱼';
    if (value) stopSpeech();
  }
  function pet() {
    const now = performance.now();
    if (now - lastPet < 650) return;
    lastPet = now; pets = Math.min(pets + 1, 999);
    $('#fish-affection').textContent = `♡ ${pets}`;
    $('#fish-affection').setAttribute('aria-label', `已抚摸 ${pets} 次`);
    root.classList.remove('is-petted'); void root.offsetWidth; root.classList.add('is-petted');
    const critical = snapshot.pending || ['error', 'done', 'cancelled'].includes(snapshot.status);
    if (!critical) {
      interactionText = ['咕噜～被你摸得圆了一点点。', '尾巴摇摇，灵感冒泡！', '收到一份摸摸，心情加满啦～'][ (pets - 1) % 3 ];
      renderBubble();
    }
    clearTimeout(cuddleTimer);
    cuddleTimer = setTimeout(() => { root.classList.remove('is-petted'); interactionText = ''; renderBubble(); }, 1800);
  }
  function mount() {
    root = $('#research-pet'); if (!root) return;
    welcomeLayout = document.body.classList.contains('welcome-view');
    const pref = read('osint-fish-collapsed'); collapsePreference = pref === 'true' ? true : pref === 'false' ? false : null;
    announcements = read('osint-fish-announcements') !== 'false';
    lastMessage = $('#fish-bubble').textContent;
    setCollapsed(collapsePreference ?? narrow.matches); renderBubble();
    // Stick the whole column only when it fits. Short windows use ordinary
    // page scrolling so the fish and its controls always remain reachable.
    const column = $('.workflow-column');
    if (column) {
      const fit = () => column.classList.toggle('fish-column-fits', !narrow.matches && column.offsetHeight <= window.innerHeight - 48);
      if (typeof ResizeObserver === 'function') new ResizeObserver(fit).observe(column);
      window.addEventListener('resize', fit); fit();
    }
    $('#fish-announcements').setAttribute('aria-pressed', String(announcements));
    $('#fish-announcements').textContent = announcements ? '播报：开' : '播报：关';
    $('#fish-collapse').onclick = () => { collapsePreference = !collapsed; setCollapsed(collapsePreference); save('osint-fish-collapsed', collapsed); };
    narrow.addEventListener('change', () => { if (collapsePreference === null) setCollapsed(narrow.matches); });
    $('#fish-announcements').onclick = () => {
      announcements = !announcements; save('osint-fish-announcements', announcements);
      $('#fish-announcements').setAttribute('aria-pressed', String(announcements));
      $('#fish-announcements').textContent = announcements ? '播报：开' : '播报：关';
      interactionText = ''; renderBubble();
      if (!announcements) stopSpeech();
    };
    $('#fish-voice').onclick = () => {
      if (voiceOn) { disableVoice('只看任务事件，不消耗模型额度'); return; }
      if (!localVoice() || typeof window.SpeechSynthesisUtterance !== 'function') {
        disableVoice('未找到本地中文语音，气泡播报仍可用'); return;
      }
      voiceOn = true; $('#fish-voice').setAttribute('aria-pressed', 'true'); $('#fish-voice').textContent = '语音：开';
      $('#fish-note').textContent = '使用设备本地中文语音，可随时关闭';
      if (announcements) speak(lastMessage);
    };
    // Some browsers populate voices asynchronously after the first query.
    try {
      window.speechSynthesis?.getVoices();
      window.speechSynthesis?.addEventListener?.('voiceschanged', () => {
        if (!voiceOn && localVoice() && $('#fish-note').textContent.includes('未找到本地中文语音')) $('#fish-note').textContent = '本地中文语音已就绪，点击“语音”手动开启';
      });
    } catch { /* Voice is optional; the fish still reports with text. */ }
    $('#fish-go').onclick = () => {
      const action = $('#fish-go').dataset.action;
      if (action === 'report') $('#btn-open-report')?.click();
      if (action === 'pending') $('#btn-pending')?.click();
      if (action === 'error') $('#error-banner')?.scrollIntoView({block: 'center', behavior: 'instant'});
    };
    const target = $('#fish-stroke');
    target.addEventListener('pointerdown', e => { if (e.button === 0) pointer = {id: e.pointerId, x: e.clientX, y: e.clientY, moved: false}; });
    target.addEventListener('pointermove', e => {
      if (!pointer || pointer.id !== e.pointerId) return;
      const dx = Math.abs(e.clientX - pointer.x), dy = Math.abs(e.clientY - pointer.y);
      // Horizontal rubbing works on touch; vertical movement remains page scroll.
      if (dx > 18 && (e.pointerType !== 'touch' || dx > dy * 1.4)) {
        pet(); pointer.x = e.clientX; pointer.y = e.clientY; pointer.moved = true;
      }
    });
    target.addEventListener('pointerup', () => { if (pointer?.moved) suppressClickUntil = Date.now() + 500; pointer = null; });
    for (const name of ['pointercancel', 'pointerleave']) target.addEventListener(name, () => { if (pointer?.moved) suppressClickUntil = Date.now() + 500; pointer = null; });
    target.addEventListener('click', e => { if (e.detail === 0 || Date.now() >= suppressClickUntil) pet(); });
    document.addEventListener('visibilitychange', () => { if (document.hidden) stopSpeech(); });
    // A confirmation or chat dialog should never compete with voice narration.
    const observer = new MutationObserver(records => { if (records.some(r => r.target.classList.contains('modal-overlay') && !r.target.classList.contains('hidden'))) stopSpeech(); });
    document.querySelectorAll('.modal-overlay').forEach(modal => observer.observe(modal, {attributes: true, attributeFilter: ['class']}));
    window.addEventListener('pagehide', () => { clearQueue(); clearTimeout(cuddleTimer); interactionText = ''; root.classList.remove('is-petted'); stopSpeech(); });
  }
  document.addEventListener('DOMContentLoaded', mount);
  document.addEventListener('osint:layout', e => { welcomeLayout=e.detail?.mode==='welcome'; if(root){setCollapsed(collapsePreference ?? narrow.matches);if(welcomeLayout)stopSpeech();} });
  document.addEventListener('osint:view', e => update(e.detail));
  document.addEventListener('osint:effect', e => effect(e.detail));
})();
