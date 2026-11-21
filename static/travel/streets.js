import {$, make, button, link, scenic} from './ui.js';

async function dataset(file) {
  const response = await fetch('/static/travel/data/' + file);
  if (!response.ok) throw new Error('地图暂不可用');
  return response.json();
}

function localMap(container, data, entries, choose) {
  const canvas = make('canvas');
  canvas.setAttribute('aria-label', '真实地理底图，地点可从下方列表选择');
  const layer = make('div', null, 'street-markers');
  const viewport=make('div',null,'street-viewport');
  viewport.tabIndex=0;viewport.setAttribute('aria-label','地图，可用方向键平移');
  const surface=make('div',null,'street-surface');surface.append(canvas,layer);viewport.append(surface);
  let zoom=1;
  const controls=make('div',null,'street-zoom');
  const zoomIn=button('放大',()=>resize(zoom+0.5)), zoomOut=button('缩小',()=>resize(zoom-0.5));
  function resize(next){
    const cx=(viewport.scrollLeft+viewport.clientWidth/2)/zoom;
    const cy=(viewport.scrollTop+viewport.clientHeight/2)/zoom;
    zoom=Math.min(3,Math.max(1,next));
    surface.style.width=surface.style.height=(zoom*100)+'%';
    viewport.scrollLeft=cx*zoom-viewport.clientWidth/2;
    viewport.scrollTop=cy*zoom-viewport.clientHeight/2;
    zoomIn.disabled=zoom>=3;zoomOut.disabled=zoom<=1;
  }
  controls.append(zoomIn,zoomOut);container.replaceChildren(viewport,controls);resize(1);
  const [west, south, east, north] = data.bbox;
  const longitudeRatio = Math.cos((north + south) * Math.PI / 360);
  const pins = entries.map((p, i) => {
    const pin = button(String(i + 1), () => choose(p.poi_id), 'street-pin');
    pin.title = p.name;
    pin.setAttribute('aria-label', p.name);
    layer.append(pin);
    return pin;
  });
  function draw() {
    const width = surface.clientWidth, height = surface.clientHeight;
    if (!width || !height) return;
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width * pixelRatio; canvas.height = height * pixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.scale(pixelRatio, pixelRatio);
    const scale = Math.min((width - 42) / ((east - west) * longitudeRatio), (height - 42) / (north - south));
    const project = ([x,y]) => [width / 2 + (x - (west + east)/2) * longitudeRatio * scale, height / 2 - (y - (south + north)/2) * scale];
    const colors = {region:'#c7dfcb', green:'#bddac3', water:'#92c7d2', road:'#beac93', bridge:'#b67b53'};
    for (const [kind, coords] of data.features) {
      if (!coords.length) continue;
      ctx.beginPath(); ctx.moveTo(...project(coords[0]));
      for(let i=1; i<coords.length; i++) ctx.lineTo(...project(coords[i]));
      ctx.strokeStyle = colors[kind]; ctx.lineWidth = kind === 'road' ? 1.4 : 2.2;
      if (kind === 'region' || ((kind === 'water' || kind === 'green') && coords[0].join() === coords.at(-1).join())) {
        ctx.fillStyle = colors[kind]; ctx.fill();
      }
      ctx.stroke();
    }
    const labelBoxes = entries.map(p=>[...project([p.coordinates.longitude,p.coordinates.latitude]),20]);
    ctx.font='12px sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';
    for (const [name, point] of data.labels || []) {
      const [x,y]=project(point), halfWidth=ctx.measureText(name).width/2+4;
      if(x<halfWidth || x>width-halfWidth || y<10 || y>height-10 || labelBoxes.some(([u,v,other])=>Math.abs(x-u)<halfWidth+other && Math.abs(y-v)<20))continue;
      labelBoxes.push([x,y,halfWidth]);ctx.lineWidth=3;ctx.strokeStyle='#f7f7ed';ctx.strokeText(name,x,y);ctx.fillStyle='#586955';ctx.fillText(name,x,y);
    }
    const placed = [];
    entries.forEach((p, i) => {
      const [x,y] = project([p.coordinates.longitude,p.coordinates.latitude]);
      let px = x, py = y;
      for(let a=0; a<100 && placed.some(([u,v])=>Math.hypot(px-u,py-v)<28); a++){
        const radius=8+5*Math.sqrt(a);
        px=Math.min(width-14,Math.max(14,x+radius*Math.cos(a*2.4)));
        py=Math.min(height-14,Math.max(14,y+radius*Math.sin(a*2.4)));
      }
      placed.push([px,py]);
      ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(px,py); ctx.strokeStyle='#465e4c';ctx.lineWidth=1;ctx.stroke();
      ctx.fillStyle='#294c39';ctx.fillRect(x-2,y-2,4,4);
      pins[i].style.left=px+'px';pins[i].style.top=py+'px';
    });
  }
  const observer = new ResizeObserver(draw); observer.observe(surface);draw();
  return () => observer.disconnect();
}

export function setupStreets(catalog, choose) {
  const province = $('map-atlas');
  const area = make('section', null, 'district-view');
  area.hidden=true;province.after(area);
  let generation = 0, stopDrawing;
  const fallback = () => {
    stopDrawing?.();generation++;area.hidden=true;province.hidden=false;
    $('map-title').textContent='贵州九市州景点地图';
    $('map-title').scrollIntoView({block:'nearest'});
  };
  async function open(regionId, street=false) {
    const current=++generation;
    const region=catalog.get(regionId);
    if (!region) return;
    stopDrawing?.();province.hidden=true;area.hidden=false;
    area.replaceChildren(button('← 返回贵州地图',fallback));
    const heading=$('map-title');heading.textContent=region.region+'景点地图';
    try {
      const {districts}=await dataset('streets.json');
      if(current!==generation)return;
      const district=districts.find(d=>d.region_id===regionId);
      if(!district)throw new Error();
      const points=district.poi_ids.map(id=>catalog.get(id)).filter(Boolean);
      const entries=street?points:district.attraction_ids.map(id=>catalog.get(id)).filter(Boolean);
      const controls=make('div',null,'actions');
      if(street){
        heading.textContent=district.name+'逛吃地图';
        controls.append(button('← '+region.region+'景点图',()=>open(regionId)));
      } else controls.append(button('进入'+district.name+'逛吃图 →',()=>open(regionId,true),'primary'));
      const frame=make('div',null,'street-map');
      const locations=make('div',null,'street-places');
      entries.forEach((p,i)=>locations.append(button((i+1)+'　'+p.name,()=>choose(p.poi_id,{showDetail:true}))));
      const chooseFromMap=id=>choose(id,{showDetail:true});
      area.append(controls,frame,locations);
      if(street){
        const visual=make('details',null,'street-illustration');
        visual.append(make('summary','查看'+district.name+'风味插画'));
        const img=make('img');img.src=district.image;img.loading='lazy';img.alt=district.name+'地方风味视觉样稿';
        visual.append(img,make('small','据团队 GPT Image 视觉样稿；地理定位见上方 OSM 地理图层。'));area.append(visual);
      } else if(region.images.length){
        const figure=scenic(region.images[0]);figure.classList.add('region-photo');area.append(figure);
      }
      area.append(link('地图数据 © OpenStreetMap 贡献者 · ODbL','https://www.openstreetmap.org/copyright'));
      try {
        let data;
        if(street) data=await dataset('street-'+district.id+'.json');
        else{
          const {features}=await dataset('guizhou-prefectures.json');
          const shape=features.find(f=>f.id===regionId).geometry;
          const polys=shape.type==='Polygon'?[shape.coordinates]:shape.coordinates;
          const rings=polys.map(poly=>poly[0]);
          const coords=rings.flat();
          const xs=coords.map(p=>p[0]),ys=coords.map(p=>p[1]);
          data={bbox:[Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)],features:rings.map(r=>['region',r])};
        }
        if(current===generation)stopDrawing=localMap(frame,data,entries,chooseFromMap);
      } catch {if(current===generation)frame.replaceChildren(make('p','底图暂不可用，请从地点列表继续。'));}
      if(current===generation)controls.querySelector('button').focus({preventScroll:true});
    }catch{if(current===generation)area.append(make('p','街区目录暂不可用，请返回省图选择景点。'));}
  }
  return open;
}
