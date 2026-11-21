import {$, cny, openDialog, show} from './ui.js';
import {demoSku} from './demo.js';

export function setupAccount(request, {demo, sku, applyProfile, outOfSession}) {
  let profile;
  let order;
  let setting;
  let busy = false;
  let balanceRead = 0;
  const controls = ['login', 'register', 'logout', 'open-profile', 'open-order'];
  const statuses = {pending: '待完成', success: '成功', failed: '失败', cancelled: '已取消'};
  function setControls(signedIn) {
    $('account-entry').textContent=signedIn?'我的账号':'登录';
    $('account-entry').href=signedIn?(document.documentElement.classList.contains('journal-mode')?'/#account':'#account'):'/login';
    for (const id of controls) $(id).hidden = ['login', 'register'].includes(id) ? signedIn : !signedIn;
    $('logout').hidden ||= demo;
    $('security-center').hidden=!signedIn || !!demo;
  }
  function showBalance(data) {
    const balance = data?.balance || data;
    $('top-credits').textContent=balance?.unlimited?'不限次数':Number.isInteger(balance?.available)?'可用 '+balance.available+' 次':'';
    const cards=$('credit-cards');
    cards.replaceChildren();
    cards.hidden=!Number.isInteger(balance?.available) && !balance?.unlimited;
    for(const [label,value] of [['可用次数',balance?.unlimited?'不限':balance?.available],['执行中',balance?.reserved],['已使用',balance?.used]]){
      const tile=document.createElement('div'),num=document.createElement('strong'),caption=document.createElement('small');
      num.textContent=value??'—';caption.textContent=label;tile.append(caption,num);cards.append(tile);
    }
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
      if (version === balanceRead) $('credit-cards').hidden=true;
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
      $('member-avatar').textContent=(user.username || '旅').slice(0,1).toUpperCase();
      setControls(true);
      if(!demo)void loadSettings();
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
  async function loadSettings(){
    try{
      const info=await request('/api/auth/settings');
      $('member-info').textContent='加入于 '+String(info.created_at).slice(0,10)+' · '+(info.can_recover?'已设置恢复码':'尚未设置恢复码');
      if(info.has_avatar){
        const photo=new Image();photo.alt='我的头像';photo.src='/api/auth/avatar?v='+Date.now();$('member-avatar').replaceChildren(photo);
      }
    }catch{$('member-info').textContent='账号资料暂时无法读取。';}
  }
  $('avatar-file').onchange=async event=>{
    const file=event.target.files[0];if(!file)return;
    try{
      if(file.size>4*1024*1024)throw new Error('头像请小于 4 MB。');
      const data=new FormData();data.append('file',file);
      await request('/api/auth/avatar',{method:'POST',body:data});await loadSettings();
      $('security-message').textContent='头像已更新。';
    }catch(error){$('security-message').textContent=error.message;}
    finally{event.target.value='';}
  };
  const settings={
    password:['修改密码','设置 6–128 个字符的新密码。保存后，请在各设备重新登录。','保存新密码'],
    'recovery-code':['管理恢复码','验证密码后生成新的恢复码。请妥善保存，上一组恢复码随即失效。','生成恢复码'],
    'close-account':['注销账号','注销将永久删除你的账号、旅行记录、报告和额度。请输入当前密码确认。','确认永久注销'],
  };
  for(const trigger of document.querySelectorAll('[data-setting]'))trigger.onclick=()=>{
    setting=trigger.dataset.setting;
    const [title,help,action]=settings[setting];
    $('security-title').textContent=title;$('security-help').textContent=help;$('confirm-setting').textContent=action;
    $('security-form').reset();$('security-form').hidden=false;$('new-recovery').hidden=true;$('new-recovery').textContent='';
    $('new-passwords').disabled=$('new-passwords').hidden=setting!=='password';
    $('security-error').textContent='';openDialog('security-dialog');$('current-password').focus();
  };
  $('security-dialog').addEventListener('close',()=>{$('security-form').reset();$('new-recovery').textContent='';});
  $('security-form').onsubmit=async e=>{
    e.preventDefault();
    const body={password:$('current-password').value};
    if(setting==='password'){
      body.new_password=$('new-password').value;
      if(body.new_password!==$('repeat-password').value){$('security-error').textContent='两次输入的新密码不同。';return;}
    }
    const current=setting;
    $('confirm-setting').disabled=true;
    try{
      const result=await request('/api/auth/'+current,{method:'POST',body});
      if(current==='recovery-code'){
        if(!$('security-dialog').open || setting!==current)return;
        $('new-recovery').hidden=false;$('new-recovery').textContent=result.recovery_code;$('security-form').reset();
        $('security-form').hidden=true;$('security-error').textContent='请保存这组一次性恢复码。';void loadSettings();
      }else location.replace('/login');
    }catch(error){$('security-error').textContent=error.message;}
    finally{$('confirm-setting').disabled=false;}
  };
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
    body.age = body.age ? Number(body.age) : null;
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
