import {weatherName} from './journey.js';
import {$, dateTime, make} from './ui.js';

const people = {
  xiaolan: {name: '小岚', role: '苗族导游'},
  ahe: {name: '阿禾', role: '布依族导游'},
};
const actions = new Set(['wave', 'map', 'notes', 'umbrella', 'itinerary']);
function getSetting(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function setSetting(key, value) {
  try { localStorage.setItem(key, String(value)); } catch { /* Settings are optional. */ }
}
function localVoice() {
  try { return window.speechSynthesis?.getVoices().find(voice => voice.localService && /^zh(?:-|_|$)/i.test(voice.lang)); }
  catch { return null; }
}

export function setupGuide(allowServerVoice) {
  const image = $('guide-image');
  const guide = $('guide');
  const speech = $('voice');
  let voiceOn = getSetting('travel-guide-voice') === 'true';
  let utterance;
  let audio, audioUrl, controller;
  let voiceGeneration = 0;
  let lastText = $('guide-message').textContent;
  let backgroundAction = 'idle';
  let foregroundAction = null;
  let timer;

  function stop() {
    voiceGeneration++;
    controller?.abort();
    controller=undefined;
    if(audio){audio.pause();audio=undefined;}
    if(audioUrl){URL.revokeObjectURL(audioUrl);audioUrl=undefined;}
    if (utterance) {
      utterance = undefined;
      window.speechSynthesis?.cancel();
    }
  }
  function setVoice(enabled, title) {
    voiceOn = enabled;
    if (!title) setSetting('travel-guide-voice', enabled);
    speech.setAttribute('aria-pressed', String(enabled));
    speech.textContent = title || (enabled ? '语音：开' : '语音：关');
    if (!enabled) stop();
  }
  async function serverSpeech(text, generation) {
    if (generation !== voiceGeneration || !voiceOn) return;
    if (!allowServerVoice) {setVoice(false,'样例模式缺少浏览器中文语音');return;}
    controller=new AbortController();
    try {
      const res=await fetch('/api/travel/guide-voice',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:text.slice(0,450)}),signal:controller.signal});
      if (!res.ok) throw new Error();
      const blob=await res.blob();
      if (generation !== voiceGeneration) return;
      audioUrl=URL.createObjectURL(blob);
      audio=new Audio(audioUrl);
      audio.onended=()=>{if(generation===voiceGeneration)stop();};
      await audio.play();
    } catch { if(generation===voiceGeneration)setVoice(false,'播放失败 · 点击重试'); }
  }
  function speak(text) {
    if (!voiceOn || document.hidden || document.querySelector('dialog[open]')) return;
    stop();
    const generation=voiceGeneration;
    const voice=localVoice();
    if (!voice || typeof SpeechSynthesisUtterance!=='function') {
      void serverSpeech(text,generation);
      return;
    }
    try {
      const job=new SpeechSynthesisUtterance(text);
      job.voice=voice;job.lang=voice.lang;
      job.onend=()=>{if(utterance===job)utterance=undefined;};
      job.onerror=()=>{if(utterance===job){utterance=undefined;void serverSpeech(text,generation);}};
      utterance=job;
      window.speechSynthesis.speak(job);
    } catch { void serverSpeech(text,generation); }
  }
  function setPerson() {
    const id = $('guide-select').value;
    const {name, role} = people[id];
    setSetting('travel-guide', id);
    image.onerror = () => {
      image.onerror = null;
      image.src = `/static/travel/assets/${id}.png`;
    };
    image.src = `/static/travel/assets/${id}.svg`;
    $('guide-name').replaceChildren(name, ' ', make('small', role));
  }
  function drawAction() {
    const action = foregroundAction || backgroundAction;
    guide.dataset.action = action;
    const prop = $('guide-prop');
    const propId = {preview:'notes', briefing:'itinerary'}[action] || action;
    if (!actions.has(propId)) {
      prop.replaceChildren();
      return;
    }
    const namespace = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(namespace, 'svg');
    const use = document.createElementNS(namespace, 'use');
    svg.setAttribute('viewBox', '0 0 42 42');
    use.setAttribute('href', '/static/travel/assets/guide-props.svg#' + propId);
    svg.append(use);
    prop.replaceChildren(svg);
  }
  function action(value, persist = false) {
    clearTimeout(timer);
    foregroundAction = value;
    drawAction();
    if (!persist) timer = setTimeout(() => { foregroundAction = null; drawAction(); }, 2100);
  }
  function message(text, animate, audible) {
    if (animate) action(animate, animate === 'itinerary');
    if (!text || text === lastText) return;
    lastText = text;
    $('guide-message').textContent = text;
    if (audible) speak(text);
  }
  setVoice(voiceOn);
  const saved = getSetting('travel-guide');
  if (Object.hasOwn(people, saved)) $('guide-select').value = saved;
  setPerson();
  $('guide-select').addEventListener('change', () => { stop(); setPerson(); });
  $('guide-greet').addEventListener('click', () => {
    const briefing = document.documentElement.classList.contains('journal-mode');
    action(briefing ? 'briefing' : 'wave');
    if(briefing) speak(lastText);
    else $('task').focus();
  });
  speech.addEventListener('click', () => {
    if (voiceOn) setVoice(false);
    else {setVoice(true);speak(lastText);}
  });
  const motion = $('motion');
  let reduce = getSetting('travel-reduce-motion') === 'true';
  function setMotion() {
    document.body.classList.toggle('reduce-motion', reduce);
    motion.setAttribute('aria-pressed', String(reduce));
  }
  setMotion();
  motion.addEventListener('click', () => {
    reduce = !reduce;
    setSetting('travel-reduce-motion', reduce);
    setMotion();
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.addEventListener('pagehide', stop);

  document.addEventListener('osint:view', ({detail}) => {
    if (detail.domain !== 'guizhou_travel') return;
    const dock = $('progress-guide');
    if (guide.parentElement !== dock) dock.append(guide);
    if (detail.newRun) {
      clearTimeout(timer);
      foregroundAction = null;
      drawAction();
      stop();
    }
    message(detail.message, detail.completed && !detail.snapshot ? 'itinerary' : null, !detail.snapshot);
  });
  document.addEventListener('osint:effect', ({detail}) => {
    if (detail.domain !== 'guizhou_travel') return;
    if (detail.type === 'tool_call') message('我来核对行程里的交通和天气。', 'map', false);
    if (detail.type === 'tool_result') message('资料已记好，正在整理行程。', 'notes', true);
    if (detail.type === 'tool_error') message('有资料需要再确认，我会在报告里标明。', null, false);
  });
  document.addEventListener('travel:preview',()=>action('preview'));
  action('wave');

  return function showWeather(weather,region='贵阳',loading=false) {
    const city = typeof weather?.location === 'string' ? weather.location : weather?.location?.name;
    const timestamp = dateTime(weather?.as_of);

    const condition = weather?.condition || (weather?.weather_code!=null ? weatherName(weather.weather_code) : '');
    const isRain = /雨|rain/i.test(condition) || weather?.precipitation_probability_max>=50;
    const available = city===region && weather.stale === false && timestamp && ['observation', 'forecast'].includes(weather.kind);
    if (!available) {
      $('weather').textContent = region+(loading?' · 正在查询':' · 天气待查');
      $('weather-time').textContent = loading?'正在读取模型预报':'暂未取得预报';
      $('weather-icon').textContent = '☁';
      backgroundAction = 'idle';
    } else {
      const observation = weather.kind === 'observation';
      $('weather').textContent = `${city} · ${condition}`;
      $('weather-time').textContent = `${weather.temperature_2m_min!=null ? weather.temperature_2m_min+'～'+weather.temperature_2m_max+'°C · ' : ''}${observation ? '观测' : '预报'} ${timestamp}${!observation && isRain ? ' · 建议带伞' : ''}${weather.data_mode === 'cache' ? ' · 缓存' : weather.data_mode === 'fixture' ? ' · 回放' : ''}`;
      $('weather-icon').textContent = isRain ? '☂' : /阴|云|雾/.test(condition) ? '☁' : '☀';
      backgroundAction = observation && isRain ? 'umbrella' : 'idle';
    }
    drawAction();
  };
}
