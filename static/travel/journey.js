import {make, link, button, booking, scenic} from './ui.js';

const list = x => Array.isArray(x) ? x : [];
const value = (v,u='') => v == null ? '待查' : v+u;
export const weatherName = c => c == null ? '天气待查' : c===0?'晴':c<=2?'少云':c===3?'阴':c<=48?'雾':c<=57?'毛毛雨':c<=67?'雨':c<=77?'雪':c<=82?'阵雨':c<=86?'阵雪':'雷雨';
const clock = t => typeof t==='string' ? t.slice(11,16) : '待查';
function section(root,title,style='') {
  const el=make('section',null,'journey-card '+style);
  el.append(make('h2',title)); root.append(el); return el;
}
function fold(root,title,open=false){
  const el=make('details'); el.open=open; el.append(make('summary',title));root.append(el);return el;
}
function metric(root,label,data){
  const box=make('div',null,'reading');box.append(make('small',label),make('strong',data));root.append(box);
}
function weatherCard(root,forecasts,request,poi,dates){
  const card=section(root,'目的地天气','sky');
  if (!forecasts.length && request && poi) forecasts = [{location:poi.county,poi_id:poi.poi_id,days:dates.map(day=>({date:day.date}))}];
  if(!forecasts.some(f=>list(f.days).length)){card.append(make('p','本次报告没有取得目的地预报。'));return;}
  const pick=make('select');pick.setAttribute('aria-label','预报地点与日期');
  const items=forecasts.flatMap(f=>list(f.days).map(d=>({f,d})));
  items.forEach(({f,d},i)=>{const opt=make('option',f.location+' · '+d.date);opt.value=i;pick.append(opt);});
  const panel=make('div');card.append(pick,panel);
  const updates = new Map();
  const pending = new Set();
  const errors = new Map();
  async function update(item) {
    if (pending.has(item)) return;
    pending.add(item);
    errors.delete(item);
    paint();
    try {
      const result = await request('/api/travel/forecast-day?' + new URLSearchParams({poi_id:item.f.poi_id, day:item.d.date}));
      if (result.days?.[0]?.date !== item.d.date) throw new Error('补查日期不符。');
      updates.set(item,result);
    } catch(error) { errors.set(item,error.message); }
    finally { pending.delete(item); paint(); }
  }
  function paint(){
    const item=items[pick.selectedIndex];panel.replaceChildren();if(!item)return;
    const f=updates.get(item) || item.f;
    const d=updates.has(item) ? f.days[0] : item.d;
    if (d.weather_code!=null) panel.append(make('p',weatherName(d.weather_code),'condition'));
    if (d.temperature_2m_min!=null && d.temperature_2m_max!=null)
      panel.append(make('strong',d.temperature_2m_min+' – '+d.temperature_2m_max+'°C','temperature'));
    panel.append(make('p',d.date+' · 北京时间 · '+(updates.has(item)?'本次补查的 Open-Meteo 预报':f.data_mode==='fixture'?'天气样例回放':'报告保存的预报')));
    if (d.temperature_2m_min==null) {
      panel.append(make('small',d.precipitation_probability_max!=null?'原报告仅保存了部分天气指标。':'原报告没有保存这一天的预报。'));
      if (request && item.f.poi_id) {
        const fetchDay=button(pending.has(item)?'正在补查…':'补查这一天的预报',()=>update(item));
        fetchDay.disabled=pending.has(item); panel.append(fetchDay);
        if(errors.has(item))panel.append(make('p',errors.get(item)));
      }
    }
    const grid=make('div',null,'readings');panel.append(grid);
    for(const [label,k,u] of [['降雨概率','precipitation_probability_max','%'],['降水量','precipitation_sum',' mm'],['最大风速','wind_speed_10m_max',' km/h'],['最大阵风','wind_gusts_10m_max',' km/h'],['主导风向','wind_direction_10m_dominant','°'],['紫外线指数','uv_index_max','']])if(d[k]!=null)metric(grid,label,value(d[k],u));
    if(d.sunrise)metric(grid,'日出',clock(d.sunrise));
    if(d.sunset)metric(grid,'日落',clock(d.sunset));
    if(list(d.hours).length){
      const h=make('select');h.setAttribute('aria-label','选择逐小时预报');
      d.hours.forEach((r,i)=>{const o=make('option',clock(r.time)+' · '+weatherName(r.weather_code));o.value=i;h.append(o);});
      const hourly=make('div',null,'readings');
      const periods=make('div',null,'weather-periods');
      for(const [label,target] of [['白天',12],['夜间',21]]){
        periods.append(button(label,()=>{const idx=d.hours.findIndex(r=>Number(clock(r.time).slice(0,2))===target);if(idx>=0){h.selectedIndex=idx;hour();}}));
      }
      panel.append(make('h3','逐小时预报'),periods,h,hourly);
      function hour(){hourly.replaceChildren();const r=d.hours[h.selectedIndex];strip.querySelectorAll('button').forEach((el,i)=>el.setAttribute('aria-pressed',String(i===h.selectedIndex)));
        for(const [label,k,u] of [['气温','temperature_2m','°C'],['体感','apparent_temperature','°C'],['小时降水概率','precipitation_probability','%'],['湿度','relative_humidity_2m','%'],['云量','cloud_cover','%'],['气压','surface_pressure',' hPa'],['能见度','visibility',' m'],['空气质量（欧洲 AQI）','european_aqi',''],['PM2.5','pm2_5',' μg/m³'],['PM10','pm10',' μg/m³'],['二氧化硫','sulphur_dioxide',' μg/m³'],['一氧化碳','carbon_monoxide',' μg/m³']])if(r[k]!=null)metric(hourly,label,value(r[k],u));}
      const strip=make('div',null,'hour-strip');
      const temps=d.hours.map(r=>r.temperature_2m).filter(Number.isFinite);
      const min=Math.min(...temps),max=Math.max(...temps);
      d.hours.forEach((r,i)=>{
        const tick=button(clock(r.time),()=>{h.selectedIndex=i;hour();},'hour-tick');
        tick.title=clock(r.time)+' · '+value(r.temperature_2m,'°C');
        const bar=make('i');
        bar.style.height=(Number.isFinite(r.temperature_2m)?10+32*(r.temperature_2m-min)/Math.max(1,max-min):2)+'px';
        tick.prepend(make('b',value(r.temperature_2m,'°')),bar);
        strip.append(tick);
      });
      panel.insertBefore(strip,h);
      h.selectedIndex=Math.min(12,d.hours.length-1);h.onchange=hour;hour();
    }
    if(f.as_of)panel.append(make('small','采集于 '+f.as_of.replace('T',' ')));
  }
  function selectDay(){
    const item=items[pick.selectedIndex];
    const today=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date());
    if(item && item.d.date>=today && request && item.f.poi_id && item.d.temperature_2m_min==null && !updates.has(item) && !errors.has(item)) void update(item);
    else paint();
  }
  pick.onchange=selectDay;selectDay();
}
function railCard(root,groups,request,context,date){
  const card=section(root,'铁路班次与余票');
  card.append(link('到铁路 12306 查票、购票 ↗','https://www.12306.cn/index/','button'));
  if(!groups.length){
    card.append(make('p',context?.travel_mode && !['rail','high_speed'].includes(context.travel_mode)
      ? '本次选择了非铁路出行方式。' : '这份报告没有保存铁路班次。'));
    if(request && context?.origin && context.candidate_station && date && (!context.travel_mode || ['rail','high_speed'].includes(context.travel_mode))){
      const target=context.candidate_station;
      const station=make('select');station.setAttribute('aria-label','确认补查的出发站');
      station.add(new Option('请选择出发站',''));
      const feedback=make('p');feedback.setAttribute('role','status');
      const result=make('div');
      const query=button('查询当日直达车次',async()=>{
        query.disabled=true; station.disabled=true; result.replaceChildren();feedback.textContent='正在向铁路服务查票…';
        try {
          const group=await request('/api/travel/rail-day?'+new URLSearchParams({departure_id:station.value,arrival_id:arrivalId,day:date}));
          feedback.textContent='本次补查 · '+group.as_of;
          drawGroup(result,group,true);
        } catch(error){feedback.textContent=error.message;}
        finally{station.disabled=false;query.disabled=!arrivalId || !station.value;}
      });
      let arrivalId;
      query.disabled=true;
      station.onchange=()=>{query.disabled=!arrivalId || !station.value;result.replaceChildren();feedback.textContent='';};
      card.append(make('small',date+' · 候选到达站 '+target+'。请确认具体出发站。'),station,query,feedback,result);
      const stations=q=>request('/api/travel/departures?'+new URLSearchParams({mode:'rail',q}));
      Promise.all([stations(context.origin),stations(target)]).then(([from,to])=>{
        arrivalId=to.items.find(r=>r.name===target)?.id;
        for(const r of from.items) station.add(new Option(r.name,r.id));
        if(context.departure_id) station.value=context.departure_id;
        query.disabled=!arrivalId || !station.value;
        if(!arrivalId || !from.items.length)feedback.textContent='车站目录未能匹配，请通过 12306 查票。';
      }).catch(()=>{feedback.textContent='暂时无法读取车站目录。';});
    }
    return;
  }
  for(const group of groups)drawGroup(card,group,groups.length===1);
  card.append(make('small','按本次查询快照展示，余票与成交价以铁路 12306 为准。'));
}
function drawGroup(card,group,open){
    const trains=list(group.trains);
    const box=fold(card,[group.from,group.to].filter(Boolean).join(' → ')+' · '+(group.date||'')+' · '+trains.length+' 趟',open);
    if(!trains.length)box.append(make('p','该日期与站点组合未查到直达车次。'));
    for(const t of trains){
      const row=fold(box,[t.start_train_code,t.start_time+' → '+t.arrive_time,t.from_station+' → '+t.to_station].join('　'));
      if(!list(t.prices).length)row.append(make('p','票价与余票待查'));
      for(const p of list(t.prices))row.append(make('p',p.seat_name+'　'+(p.price==null?'票价待查':'¥'+p.price)+'　余票 '+value(p.num)));
    }
}
export function renderJourney(root,detail,catalog,request){
  const p=detail.trip_plan||{}, evidence=list(detail.evidence);
  const data=tool=>evidence.filter(e=>e.title===tool && e.snapshot?.value).map(e=>e.snapshot.value);
  const snapshots=data('rail.search');
  const rail=snapshots.length?snapshots:Object.entries(p.railway||{}).map(([to,trains])=>({from:trains[0]?.from_station,to,date:trains[0]?.start_date,trains}));
  const storedWeather=list(p.weather).length?p.weather:data('weather.forecast');
  const destinations=(detail.trip_context?.poi_ids || list(p.booking_entries).map(b=>b.poi_id)).map(id=>catalog.get(id)).filter(Boolean);
  const weather=storedWeather.map(f=>{
    if(f.poi_id)return f;
    const matches=destinations.filter(poi=>poi.county===f.location);
    return matches.length===1 ? {...f,poi_id:matches[0].poi_id} : f;
  });
  root.replaceChildren();
  const media=list(p.media)[0], poi=catalog.get(media?.poi_id)||catalog.get(p.booking_entries?.[0]?.poi_id);
  const hero=make('div',null,'journey-hero');
  const img=poi?.images?.find(i=>i.image_id===media?.image_id)||poi?.images?.[0];
  if(img)hero.append(scenic(img));
  const heading=make('div');heading.append(make('p','贵州山水 · 旅行手册'),make('h1',(poi?.name||'贵州')+'旅行报告'),make('p',list(p.days).length? p.days[0].date+' 至 '+p.days.at(-1).date:'出行日期待确认'));hero.append(heading);root.append(hero);
  railCard(root,rail,request,detail.trip_context,p.days?.[0]?.date);weatherCard(root,weather,request,poi,list(p.days));
  const routes=section(root,'到站后的接驳');for(const l of list(p.legs)){
    const dest=catalog.get(l.poi_id)?.name||l.poi_id;
    routes.append(make('p',l.origin+' → '+dest),make('strong',l.label+'　'+(l.distance_m/1000).toFixed(1)+' 公里 · '+Math.round(l.duration_s/60)+' 分钟'));
  }
  routes.append(make('small',list(p.legs).length?'接驳车班次和末班时间请以运营方公告为准。':(list(p.unresolved).find(t=>t.includes('route.leg'))?.replace(/^.*?待查[：:]\s*/,'') || '尚缺已核对的到达站与景区入口坐标。')));
  if(!p.legs?.length && poi){
    routes.append(make('p',[detail.trip_context?.candidate_station && '候选到达站：'+detail.trip_context.candidate_station,'目的地：'+poi.name].filter(Boolean).join(' · ')),link('查看景区出行提示 ↗',poi.booking?.url));
  }
  const days=section(root,'每日安排');
  for(const day of list(p.days)){
    const advice=list(detail.day_suggestions).find(item=>item.date===day.date) || day;
    const title=advice.title==='待安排'?'':advice.title;
    const box=fold(days,[day.date,title].filter(Boolean).join(' · '),true);
    if(advice.suggestions?.length)box.append(make('small','建议行程 · 可按体力调整'));
    for(const text of list(advice.suggestions))box.append(make('p',text));
    for(const a of list(day.activities)){
      const text=typeof a==='string'?a:a.text||a.title;
      if(text && !text.includes('当日预报降雨概率'))box.append(make('p',text));
    }
    for(const alt of list(p.weather_alternatives).filter(w=>w.date===day.date))
      box.append(make('small','天气提醒 · '+alt.text));
  }
  const local=section(root,'餐食 · 住宿 · 玩乐');
  for(const [name,description] of [['餐食','午餐示例：酸汤鱼配时蔬，留出约一小时用餐；店家和人均价待核实。'],['住宿','住宿示例：前一晚住县城，早晨前往入口；客房库存、房价与接送服务待核实。'],['玩乐','玩乐示例：晴天选山间步道，雨天改为当地非遗展馆；开放时间和预约要求待核实。']]){
    fold(local,name+' · 展示样例').append(make('p',description+' 此项尚待知识库提供可核对的地点和来源。'));
  }
  const budget=section(root,'费用与预约');
  if(p.budget?.known_lower_fen>0)budget.append(make('strong','已核实费用 '+(p.budget.known_lower_fen/100).toLocaleString('zh-CN')+' 元起 · '+(p.budget.upper_fen==null?'上限待查':'上限 '+(p.budget.upper_fen/100).toLocaleString('zh-CN')+' 元')));
  for(const l of list(p.budget?.lines))budget.append(make('p',l.label+'　'+(l.upper_fen==null&& !l.lower_fen?'待报价':value(l.lower_fen/100,' 元起'))));
  for(const b of list(p.booking_entries))budget.append(booking(b.type?b:catalog.get(b.poi_id)?.booking));
  const refs=fold(budget,'查看依据与待确认事项');
  for(const e of evidence)refs.append(link(e.id+' · '+({'rail.search':'铁路查询','route.leg':'道路估算','weather.forecast':'出游日预报','rag.retrieve':'旅行资料'}[e.title]||e.title),e.source_url||e.url));
  for(const u of list(p.unresolved))refs.append(make('p',String(u).replace(/rail\.search|route\.leg|weather\.forecast|rag\.retrieve/g,k=>({'rail.search':'铁路','route.leg':'接驳','weather.forecast':'天气','rag.retrieve':'旅行资料'}[k]))));
  if(!detail.trip_plan && detail.reports?.[0]?.markdown)days.append(make('pre',detail.reports[0].markdown));
  return '行程已整理好。'+(poi?.name?'目的地是'+poi.name+'。':'')+(p.legs?.length?'已整理 '+p.legs.length+' 段接驳估算。':'')+(list(p.days).length?'共 '+p.days.length+' 天，展开每日安排可以看游览建议。':'')+(weather[0]?.days?.[0]?.precipitation_probability_max!=null ? weather[0].location+'首日预报降雨概率为'+weather[0].days[0].precipitation_probability_max+'%。':'目的地天气仍待确认。')+(rail.length?'共查到'+rail.reduce((n,g)=>n+list(g.trains).length,0)+'趟直达列车，展开班次可看席别和票价。':'');
}
