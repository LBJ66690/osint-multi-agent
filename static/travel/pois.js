import {$, booking, button, link, make, openDialog, scenic, show} from './ui.js';

export async function loadCatalog() {
  const response = await fetch('/static/travel/data/pois.json');
  if (!response.ok) throw new Error('景点目录暂不可用，仍可手动填写旅行需求。');
  const rows = await response.json();
  return new Map(rows.map(row => [row.poi_id, row]));
}

export function drawPois(catalog, plan) {
  const mobile = matchMedia('(max-width: 760px)');
  const maps = $('map-points');
  const list = $('poi-list');
  function card(poi) {
    const file = poi.images[0] || catalog.get(poi.parent_id)?.images[0];
    const image = scenic(file, false);
    const copy = make('div', null, 'poi-copy');
    copy.append(make('p', `${poi.region} · ${poi.county}`, 'eyebrow'), make('h2', poi.name),
      make('p', poi.description));
    const actions = make('div', null, 'actions');
    actions.append(button('用它规划 →', () => {
      if ($('poi-dialog').open) $('poi-dialog').close();
      plan(poi);
    }, 'primary'), booking(poi.booking));
    copy.append(actions);
    const children = [...catalog.values()].filter(row => row.parent_id === poi.poi_id);
    if (children.length) {
      const list = make('ul');
      for (const child of children) {
        const row = make('li');
        row.append(button(child.name, () => select(child.poi_id, false), 'text-button'));
        list.append(row);
      }
      copy.append(list);
    }
    const sources = make('details');
    sources.append(make('summary', '景区资料与图源'));
    const citations = make('ul');
    for (const source of poi.sources) {
      const item = make('li');
      item.append(link(source.title, source.url));
      citations.append(item);
    }
    sources.append(citations);
    if (poi.booking.verified_at) sources.append(make('small', `预约核验 ${poi.booking.verified_at}`));
    copy.append(sources);
    return [image, copy];
  }

  function select(id, allowDialog = true) {
    const poi = catalog.get(id);
    if (!poi) return;
    for (const btn of document.querySelectorAll('[data-poi]')) {
      btn.setAttribute('aria-pressed', String(btn.dataset.poi === id || btn.dataset.poi === poi.parent_id));
    }
    const cardFocused = $('poi-content').contains(document.activeElement);
    const children = card(poi);
    const heading = children[1].querySelector('h2');
    heading.id = 'poi-title';
    heading.tabIndex = -1;
    $('poi-content').replaceChildren(...children);
    if ($('poi-dialog').open || (allowDialog && mobile.matches)) {
      $('mobile-poi').replaceChildren(...card(poi));
      openDialog('poi-dialog');
      const mobileHeading = $('mobile-poi').querySelector('h2');
      mobileHeading.tabIndex = -1;
      mobileHeading.focus();
    } else if (cardFocused) {
      heading.focus();
    }
  }

  for (const poi of catalog.values()) {
    if (!poi.map_position) continue;
    const pin = button(poi.region, () => select(poi.poi_id), 'pin');
    pin.dataset.poi = poi.poi_id;
    pin.style.left = `${poi.map_position.x}%`;
    pin.style.top = `${poi.map_position.y}%`;
    pin.setAttribute('aria-label', `${poi.region}：${poi.name}`);
    maps.append(pin);
    const item = button('', () => select(poi.poi_id));
    item.dataset.poi = poi.poi_id;
    item.append(make('span', poi.region), poi.name);
    list.append(item);
  }
  select('fanjingshan', false);
  return select;
}

export async function onlineMap(catalog, select, config) {
  const {amapKey, amapServiceHost} = config;
  if (!amapKey) return;
  const frame = $('online-map');
  try {
    if (amapServiceHost) window._AMapSecurityConfig = {serviceHost: amapServiceHost};
    await new Promise((resolve, reject) => {
      const script = make('script');
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapKey)}`;
      const timeout = setTimeout(() => reject(new Error('底图连接超时')), 6500);
      script.onload = () => { clearTimeout(timeout); resolve(); };
      script.onerror = () => { clearTimeout(timeout); reject(new Error('底图加载失败')); };
      document.head.append(script);
    });
    if (!window.AMap) throw new Error('底图未初始化');
    frame.hidden = false;
    const map = new window.AMap.Map(frame, {zoom: 6.8, center: [106.73, 26.59]});
    let failed = false;
    const fail = () => {
      if (failed) return;
      failed = true;
      map.destroy();
      frame.hidden = true;
      $('map-mode').textContent = '本地示意图 · 底图不可用';
    };
    const timeout = setTimeout(fail, 6500);
    map.on('complete', () => {
      clearTimeout(timeout);
      if (failed) return;
      frame.classList.remove('loading-map');
      $('schematic').classList.add('under-map');
      $('map-mode').textContent = '高德地图';
    });
    map.on('error', () => {
      clearTimeout(timeout);
      fail();
      $('schematic').classList.remove('under-map');
    });
    const regions = [...catalog.values()].filter(poi => poi.map_position);
    const points = regions.map(poi => [poi.coordinates.longitude, poi.coordinates.latitude]);
    window.AMap.convertFrom(points, 'gps', (status, result) => {
      if (failed || status !== 'complete' || !result.locations?.length) return;
      result.locations.forEach((position, index) => {
        const poi = regions[index];
        const marker = new window.AMap.Marker({position, title: poi.name});
        marker.on('click', () => select(poi.poi_id));
        map.add(marker);
      });
    });
  } catch {
    frame.hidden = true;
    show('map-mode', '本地示意图 · 底图不可用');
  }
}
