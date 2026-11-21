import {$, button, make, show} from './ui.js';

// Layout positions are in the photo rail. The dots come from the POI
// coordinates, and the region labels come from the boundary data.
const cards = new Map([
  ['chishui-danxia', {slot: '1 / 1 / 3 / 2', color: '#e8e5a9'}],
  ['zhijin-cave', {slot: '3 / 1 / 5 / 2', color: '#d0e0af'}],
  ['wumeng-grassland', {slot: '5 / 1 / 7 / 2', color: '#a8d6ca'}],
  ['huangguoshu', {slot: '7 / 1 / 9 / 2', color: '#d0dcaa'}],
  ['wanfenglin', {slot: '9 / 1 / 11 / 2', color: '#efd3b2'}],
  ['fanjingshan', {slot: '1 / 3 / 3 / 4', color: '#afdcc3'}],
  ['qingyan', {slot: '4 / 3 / 6 / 4', color: '#e8cb9c'}],
  ['xijiang-miao-village', {slot: '6 / 3 / 8 / 4', color: '#dcd2e7'}],
  ['libo-xiaoqikong', {slot: '9 / 3 / 11 / 4', color: '#c1ddb6'}],
]);

function photoCard(poi, design, choose) {
  const card = button('', () => choose(poi.poi_id, {showDetail: true}), 'atlas-card');
  card.dataset.poi = poi.poi_id;
  card.style.gridArea = design.slot;
  card.style.setProperty('--region-color', design.color);
  const photo = poi.images[0];
  const substitute = make('span', '实景照片暂不可用', 'atlas-card-photo atlas-photo-fallback');
  substitute.setAttribute('aria-hidden', 'true');
  if (photo?.src?.startsWith('/static/travel/assets/')) {
    const image = make('img', null, 'atlas-card-photo');
    image.alt = '';
    image.decoding = 'async';
    image.addEventListener('error', () => image.replaceWith(substitute), {once: true});
    image.src = photo.src;
    card.append(image);
  } else {
    card.append(substitute);
  }
  const caption = make('span', null, 'atlas-card-caption');
  caption.append(make('small', poi.region), make('strong', poi.name));
  card.append(caption);
  return card;
}

function prepareCanvas(canvas, width, height) {
  const pixelRatio = Math.min(devicePixelRatio || 1, 2);
  const pixelWidth = Math.round(width * pixelRatio);
  const pixelHeight = Math.round(height * pixelRatio);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  const context = canvas.getContext('2d');
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  context.clearRect(0, 0, width, height);
  return context;
}

function projection([west, south, east, north], width, height) {
  // Equirectangular, with the map's midlatitude used to keep the aspect faithful.
  const xScale = Math.cos((north + south) / 2 * Math.PI / 180);
  const rangeX = (east - west) * xScale;
  const rangeY = north - south;
  const margin = Math.min(width, height) * .038;
  const scale = Math.min((width - 2 * margin) / rangeX, (height - 2 * margin) / rangeY);
  const xOrigin = (width - rangeX * scale) / 2;
  const yOrigin = (height - rangeY * scale) / 2;
  return ([lon, lat]) => [xOrigin + (lon - west) * xScale * scale, yOrigin + (north - lat) * scale];
}

function boundaryPath(geometry, project) {
  const path = new Path2D();
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates;
  for (const polygon of polygons) for (const ring of polygon) {
    path.moveTo(...project(ring[0]));
    for (const vertex of ring.slice(1)) path.lineTo(...project(vertex));
    path.closePath();
  }
  return path;
}
function move(node, [x, y]) {
  node.style.left = `${x}px`;
  node.style.top = `${y}px`;
}

export function setupAtlas(catalog, choose, openRegion) {
  const atlas = $('map-atlas');
  const surface = $('region-map');
  const schematic = $('schematic');
  const pins = $('map-points');
  const labels = $('region-labels');
  const rail = $('poi-list');
  const regions = new Map();
  let boundary;
  let selected;
  let outline = [];
  let scheduled;
  let redraw = true;

  for (const [id, design] of cards) {
    const poi = catalog.get(id);
    if (!poi) continue;
    const card = photoCard(poi, design, choose);
    rail.append(card);
    regions.set(id, {poi, design, card});
  }

  function drawMap() {
    const {width, height} = schematic.getBoundingClientRect();
    if (!width || !height) return;
    const context = prepareCanvas(surface, width, height);
    const project = projection(boundary.bbox, width, height);
    outline = [];
    const occupied = [...regions.values()].map(({poi,pin})=>{
      const [x,y]=project([poi.coordinates.longitude,poi.coordinates.latitude]);
      return [x,y,pin.offsetWidth/2+1,pin.offsetHeight/2+1];
    });
    for (const feature of boundary.features) {
      const region = regions.get(feature.id);
      if (!region) continue;
      const {poi, design, label, pin} = region;
      const path = boundaryPath(feature.geometry, project);
      outline.push({id: feature.id, path});
      context.fillStyle = design.color;
      context.fill(path, 'evenodd');
      context.strokeStyle = '#6b8f79';
      context.lineWidth = 1;
      context.lineJoin = 'round';
      context.stroke(path);
      const [lx,ly] = project(feature.properties.label);
      const hw=label.offsetWidth/2, hh=label.offsetHeight/2;
      let x=lx,y=ly;
      for(let i=0; i<260 && occupied.some(([u,v,a,b])=>Math.abs(x-u)<hw+a && Math.abs(y-v)<hh+b); i++){
        const radius=5*Math.sqrt(i+1);
        x=Math.max(hw,Math.min(width-hw,lx+radius*Math.cos(i*2.4)));
        y=Math.max(hh,Math.min(height-hh,ly+radius*Math.sin(i*2.4)));
      }
      occupied.push([x,y,hw+1,hh+1]);move(label,[x,y]);
      move(pin, project([poi.coordinates.longitude, poi.coordinates.latitude]));
    }
    const active = outline.find(item => item.id === selected);
    if (active) {
      context.strokeStyle = '#2f664c';
      context.lineWidth = 2;
      context.stroke(active.path);
    }
  }

  function drawLeaders() {
    const canvas = $('map-leaders');
    if (!canvas.getClientRects().length) return;
    const {left, top, width, height} = canvas.getBoundingClientRect();
    const context = prepareCanvas(canvas, width, height);
    const entries = [...regions.values()].filter(region => region.pin);
    const active = entries.find(item => item.poi.poi_id === selected);
    for (const region of [...entries.filter(item => item !== active), ...(active ? [active] : [])]) {
      const photo = region.card.getBoundingClientRect();
      const pin = region.pin.getBoundingClientRect();
      const side = photo.left < pin.left ? 1 : -1;
      const x = (side === 1 ? photo.right : photo.left) - left;
      const y = photo.top + photo.height * .48 - top;
      const targetX = pin.left + pin.width / 2 - left;
      const targetY = pin.top + pin.height / 2 - top;
      const reach = Math.abs(targetX - x);
      context.beginPath();
      context.moveTo(x, y);
      context.bezierCurveTo(x + reach * .49 * side, y, targetX - reach * .22 * side, targetY, targetX, targetY);
      context.setLineDash(region === active ? [] : [3, 4]);
      context.lineWidth = region === active ? 1.5 : 1;
      context.strokeStyle = region === active ? '#2b674c' : '#6b8f7b';
      context.stroke();
    }
  }

  function update() {
    if (!boundary || scheduled) return;
    scheduled = requestAnimationFrame(() => {
      scheduled = null;
      if (redraw) {
        drawMap();
        redraw = false;
      }
      drawLeaders();
    });
  }

  surface.addEventListener('click', event => {
    const bounds = surface.getBoundingClientRect();
    const context = surface.getContext('2d');
    context.save();
    context.resetTransform();
    const region = outline.find(item => context.isPointInPath(item.path,
      event.clientX - bounds.left, event.clientY - bounds.top, 'evenodd'));
    context.restore();
    if (region) openRegion(region.id);
  });

  async function loadBoundaries() {
    try {
      const response = await fetch('/static/travel/data/guizhou-prefectures.json');
      if (!response.ok) throw new Error('地图数据不可用');
      const data = await response.json();
      for (const feature of data.features) {
        const region = regions.get(feature.id);
        if (!region) continue;
        const {poi} = region;
        region.label = button(poi.region, () => openRegion(poi.poi_id), 'region-name');
        labels.append(region.label);
        region.pin = button('', () => choose(poi.poi_id), 'pin');
        region.pin.dataset.poi = poi.poi_id;
        region.pin.title = poi.name;
        region.pin.setAttribute('aria-label', `${poi.region}：${poi.name}`);
        region.pin.setAttribute('aria-pressed', String(selected === poi.poi_id));
        pins.append(region.pin);
      }
      boundary = data;
      const observer = new ResizeObserver(() => { redraw = true; update(); });
      observer.observe(atlas);
      observer.observe(schematic);
      update();
    } catch {
      show('atlas-error', '区划地图暂不可用，请点选景点照片。');
    }
  }
  void loadBoundaries();

  return id => {
    selected = id;
    redraw = true;
    for (const [key, {card, pin}] of regions) {
      const pressed = String(key === id);
      card.setAttribute('aria-pressed', pressed);
      pin?.setAttribute('aria-pressed', pressed);
    }
    update();
  };
}
