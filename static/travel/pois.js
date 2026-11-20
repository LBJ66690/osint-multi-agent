import {$, booking, button, link, make, openDialog, scenic, show} from './ui.js';
import {setupAtlas} from './atlas.js';

export async function loadCatalog() {
  const response = await fetch('/static/travel/data/pois.json');
  if (!response.ok) throw new Error('景点目录暂不可用，仍可手动填写旅行需求。');
  const rows = await response.json();
  return new Map(rows.map(row => [row.poi_id, row]));
}

export function drawPois(catalog, plan) {
  const mobile = matchMedia('(max-width: 760px)');
  const syncAtlas = setupAtlas(catalog, select);
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
        row.append(button(child.name, () => select(child.poi_id, {openOnMobile: false}), 'text-button'));
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

  function select(id, {openOnMobile = true, showDetail = false} = {}) {
    const poi = catalog.get(id);
    if (!poi) return;
    syncAtlas(poi.parent_id || id);
    const details = $('atlas-detail');
    details.hidden = false;
    details.textContent = `查看${poi.name}`;
    details.onclick = event => {
      event.preventDefault();
      select(id, {showDetail: true});
    };
    const cardFocused = $('poi-content').contains(document.activeElement);
    const children = card(poi);
    const heading = children[1].querySelector('h2');
    heading.id = 'poi-title';
    heading.tabIndex = -1;
    $('poi-content').replaceChildren(...children);
    if ($('poi-dialog').open || (openOnMobile && mobile.matches)) {
      $('mobile-poi').replaceChildren(...card(poi));
      openDialog('poi-dialog');
      const mobileHeading = $('mobile-poi').querySelector('h2');
      mobileHeading.tabIndex = -1;
      mobileHeading.focus();
    } else if (showDetail || cardFocused) {
      heading.focus({preventScroll: true});
      $('poi-card').scrollIntoView({block: 'nearest'});
    }
  }

  select('fanjingshan', {openOnMobile: false});
  return select;
}

export function onlineMap(catalog, select, config) {
  const {amapKey, amapServiceHost} = config;
  if (!amapKey) return;
  const toggle = $('switch-map');
  const atlas = $('map-atlas');
  const frame = $('online-map');
  let map;

  function showOnline(showMap) {
    frame.hidden = !showMap;
    atlas.classList.toggle('online-active', showMap);
    toggle.textContent = showMap ? '返回景点地图' : '在线地图';
    show('map-mode', showMap ? '高德地图' : '本地地图');
    if (showMap) map.resize?.();
  }

  async function loadSdk() {
    if (window.AMap) return;
    if (amapServiceHost) window._AMapSecurityConfig = {serviceHost: amapServiceHost};
    await new Promise((resolve, reject) => {
      const script = make('script');
      script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(amapKey)}`;
      function finish(error) {
        clearTimeout(timeout);
        if (error) { script.remove(); reject(error); }
        else resolve();
      }
      const timeout = setTimeout(() => finish(new Error('底图连接超时')), 6500);
      script.onload = () => finish();
      script.onerror = () => finish(new Error('底图加载失败'));
      document.head.append(script);
    });
    if (!window.AMap) throw new Error('底图未初始化');
  }

  toggle.hidden = false;
  toggle.addEventListener('click', async () => {
    if (map) return showOnline(frame.hidden);
    toggle.disabled = true;
    show('map-mode', '正在连接在线地图');
    try {
      await loadSdk();
      frame.classList.add('loading-map');
      frame.hidden = false;
      const instance = new window.AMap.Map(frame, {zoom: 6.8, center: [106.73, 26.59]});
      map = instance;
      const fail = () => {
        if (map !== instance) return;
        instance.destroy();
        map = null;
        showOnline(false);
        show('map-mode', '本地地图 · 底图不可用');
        toggle.disabled = false;
      };
      const timeout = setTimeout(fail, 6500);
      instance.on('error', () => { clearTimeout(timeout); fail(); });
      instance.on('complete', () => {
        clearTimeout(timeout);
        if (map !== instance || !frame.classList.contains('loading-map')) return;
        frame.classList.remove('loading-map');
        showOnline(true);
        toggle.disabled = false;
      });
      const regions = [...catalog.values()].filter(poi => !poi.parent_id);
      const points = regions.map(poi => [poi.coordinates.longitude, poi.coordinates.latitude]);
      window.AMap.convertFrom(points, 'gps', (status, result) => {
        if (map !== instance || status !== 'complete' || !result.locations?.length) return;
        result.locations.forEach((position, index) => {
          const poi = regions[index];
          const marker = new window.AMap.Marker({position, title: poi.name});
          marker.on('click', () => select(poi.poi_id));
          instance.add(marker);
        });
      });
    } catch {
      map?.destroy();
      map = null;
      showOnline(false);
      show('map-mode', '本地地图 · 底图不可用');
      toggle.disabled = false;
    }
  });
}
