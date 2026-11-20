import {$, cny, openDialog, show} from './ui.js';
import {demoSku} from './demo.js';

export function setupAccount(request, {demo, sku, applyProfile, outOfSession}) {
  let profile;
  let order;
  let busy = false;
  let balanceRead = 0;
  const controls = ['login', 'register', 'logout', 'open-profile', 'open-order'];
  const statuses = {pending: '待完成', success: '成功', failed: '失败', cancelled: '已取消'};
  function setControls(signedIn) {
    $('account-entry').textContent=signedIn?'我的账号':'登录';
    $('account-entry').href=signedIn?(document.documentElement.classList.contains('journal-mode')?'/#account':'#account'):'/login';
    for (const id of controls) $(id).hidden = ['login', 'register'].includes(id) ? signedIn : !signedIn;
    $('logout').hidden ||= demo;
  }
  function showBalance(data) {
    const balance = data?.balance || data;
    if(balance?.unlimited){$('credits').textContent='测试管理员 · 不限次数';return;}
    const available = balance?.available;
    const reserved = balance?.reserved;
    const used = balance?.used;
    if (![available, reserved, used].every(Number.isInteger)) {
      $('credits').textContent = '当前额度待查';
      return;
    }
    $('credits').textContent = `可用 ${available} 次 · 执行中 ${reserved} 次 · 已用 ${used} 次`;
  }
  async function refresh() {
    const version = ++balanceRead;
    try {
      const data = await request('/api/travel/credits');
      if (version === balanceRead) showBalance(data);
    } catch (error) {
      if (version === balanceRead) $('credits').textContent = error.status === 401 ? '登录后可查看额度' : '额度服务暂不可用';
    }
  }
  async function loadProfile() {
    const data = await request('/api/travel/profile');
    profile = data.profile ?? data;
    applyProfile(profile);
    return data;
  }
  async function load() {
    try {
      const user = await request('/api/auth/me');
      $('account-name').textContent = user.username || '';
      setControls(true);
      await Promise.allSettled([refresh(), loadProfile()]);
    } catch (error) {
      setControls(false);
      $('credits').textContent = error.status === 401 ? '登录后查看可用次数' : '此环境尚未启用文旅账号服务';
      if (error.status === 404) {
        const health = await request('/api/health').catch(() => null);
        if (health?.auth_enabled === false) {
          $('login').hidden = true;
          $('register').hidden = true;
        }
      }
    }
  }
  $('logout').addEventListener('click', async () => {
    try {
      await request('/api/auth/logout', {method: 'POST', body: {}});
      location.assign('/login');
    } catch (error) { show('notice', error.message); }
  });
  $('open-profile').addEventListener('click', async () => {
    const form = $('profile-form');
    const errorNode = form.querySelector('[role="alert"]');
    show(errorNode, '');
    openDialog('profile-dialog');
    try {
      if (!profile) await loadProfile();
      for (const field of form.elements) {
        if (field.name) field.value = profile[field.name] ?? '';
      }
    } catch (error) {
      if (error.status === 401) outOfSession();
      show(errorNode, error.message);
    }
  });
  $('profile-form').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button.primary');
    if (submit.disabled) return;
    const body = Object.fromEntries(new FormData(form));
    body.travelers = body.travelers ? Number(body.travelers) : null;
    for (const key of Object.keys(body)) if (typeof body[key] === 'string') body[key] = body[key].trim() || null;
    submit.disabled = true;
    try {
      const data = await request('/api/travel/profile', {method: 'PATCH', body});
      profile = data.profile ?? data;
      applyProfile(profile);
      $('profile-dialog').close();
    } catch (error) {
      if (error.status === 401) outOfSession();
      show(form.querySelector('[role="alert"]'), error.message);
    } finally { submit.disabled = false; }
  });
  const product = demo ? demoSku : sku;
  $('open-order').addEventListener('click', () => {
    openDialog('order-dialog');
    if (!product) show('order-error', '此环境尚未配置服务端模拟商品。');
  });
  function showOrder() {
    const pending = order?.status === 'pending';
    $('order-outcomes').hidden = !pending;
    $('create-order').hidden = pending;
    $('create-order').disabled = busy || !product;
    for (const outcome of $('order-outcomes').querySelectorAll('button')) outcome.disabled = busy;
    $('order-info').textContent = order
      ? `模拟订单状态：${statuses[order.status] || order.status} · ${cny(order.amount_fen)}${Number.isInteger(order.credits) ? ` · ${order.credits} 次任务` : ''}`
      : '这是模拟订单，不发生真实支付。';
  }
  showOrder();
  $('create-order').addEventListener('click', async () => {
    if (busy || !product) return;
    busy = true;
    showOrder();
    show('order-error', '');
    try {
      const data = await request('/api/travel/demo-orders', {method: 'POST', body: {sku: product}});
      order = data.order ?? data;
    } catch (error) {
      if (error.status === 401) outOfSession();
      show('order-error', error.message);
    } finally { busy = false; showOrder(); }
  });
  $('order-outcomes').addEventListener('click', async ({target}) => {
    const outcome = target.dataset.outcome;
    const id = order?.order_id ?? order?.id;
    if (!outcome || !id || busy || order.status !== 'pending') return;
    busy = true;
    showOrder();
    show('order-error', '');
    try {
      const data = await request(`/api/travel/demo-orders/${encodeURIComponent(id)}/complete`, {method: 'POST', body: {outcome}});
      order = {...order, ...(data.order ?? data)};
      if (data.balance) {
        balanceRead++;
        showBalance(data.balance);
      } else await refresh();
    } catch (error) {
      if (error.status === 401) outOfSession();
      show('order-error', error.message);
    } finally { busy = false; showOrder(); }
  });
  return {load, refresh};
}
