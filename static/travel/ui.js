export const $ = id => document.getElementById(id);
export const make = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text != null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
};
export function show(id, text) {
  const node = typeof id === 'string' ? $(id) : id;
  node.textContent = text || '';
  node.hidden = !text;
}
export function button(text, click, style) {
  const node = make('button', text, style);
  node.type = 'button';
  node.addEventListener('click', click);
  return node;
}
export function urlFor(raw) {
  if (typeof raw !== 'string' || !raw) return null;
  try {
    const url = new URL(raw, location.origin);
    if (raw.startsWith('/') && !raw.startsWith('//') && url.origin === location.origin) return url.href;
    if (url.protocol === 'https:' && !url.username && !url.password && raw.startsWith('https://')) return url.href;
  } catch { /* Bad or unsupported URL. */ }
  return null;
}
export function link(text, raw, style) {
  const url = urlFor(raw);
  if (!url) return make('span', text, style);
  const node = make('a', text, style);
  node.href = url;
  if (new URL(url).origin !== location.origin) {
    node.target = '_blank';
    node.rel = 'noopener noreferrer';
  }
  return node;
}
export const dateTime = value => {
  if (!value || !Number.isFinite(Date.parse(value))) return '';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'short', hour12: false}).format(new Date(value));
};
export const cny = fen => Number.isInteger(fen) ? new Intl.NumberFormat('zh-CN', {style: 'currency', currency: 'CNY', maximumFractionDigits: 2}).format(fen / 100) : '待查';
export function scenic(image, lazy = true) {
  const figure = make('figure', null, 'scenic');
  const fallback = make('div', '实景图暂不可用', 'image-placeholder');
  if (!image || !image.src?.startsWith('/static/travel/assets/')) {
    figure.append(fallback);
    return figure;
  }
  const img = make('img');
  img.alt = image.alt || image.caption || '';
  img.loading = lazy ? 'lazy' : 'eager';
  img.addEventListener('error', () => img.replaceWith(fallback), {once: true});
  img.src = image.src;
  const caption = make('figcaption', `${image.caption} · `);
  caption.append(link(image.credit, image.source_url), ' · ', link(image.license, image.license_url));
  figure.append(img, caption);
  return figure;
}

export function booking(bookingData) {
  const node = make('div', null, 'actions');
  if (!bookingData || !urlFor(bookingData.url)) {
    node.append(make('small', '预约入口待核验'));
    return node;
  }
  if (bookingData.type === 'direct') {
    node.append(link('去预约 ↗', bookingData.url, 'button'));
  } else if (bookingData.type === 'official_mini_program') {
    node.append(link('查看官方预约方式 ↗', bookingData.url, 'button'), make('small', `微信小程序：${bookingData.mini_program}`));
  } else {
    node.append(make('small', '预约入口待核验'));
  }
  return node;
}

export function openDialog(id) {
  const dialog = $(id);
  if (!dialog.open) dialog.showModal();
}
for (const close of document.querySelectorAll('[data-close]')) {
  close.addEventListener('click', () => close.closest('dialog').close());
}
