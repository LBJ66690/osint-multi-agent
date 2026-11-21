(() => {
  const form = document.querySelector('#auth-form');
  const loginPath = '/login';
  const redirectToLogin = () => window.location.replace(loginPath);

  window.OSINTAuth = {
    expired: redirectToLogin,
    async check() {
      try {
        const response = await fetch('/api/auth/me', {cache: 'no-store'});
        if (response.status === 401) redirectToLogin();
        return response.ok ? await response.json() : null;
      } catch {
        return null;
      }
    },
  };

  async function post(path, data) {
    const response = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      cache: 'no-store',
      body: JSON.stringify(data || {}),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error=new Error(typeof payload.detail === 'string' ? payload.detail : payload.detail?.message || '请求未成功，请稍后重试。');
      error.challenge=payload.detail?.captcha_required ? payload.detail : null;
      throw error;
    }
    return response;
  }

  if (!form) {
    const account = document.querySelector('#account-session');
    if (!account) return;
    window.OSINTAuth.check().then(user => {
      if (!user) return;
      account.querySelector('#account-name').textContent = user.username;
      account.hidden = false;
    });
    account.querySelector('#account-signout').addEventListener('click', async event => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await post('/api/auth/logout');
        redirectToLogin();
      } catch {
        account.querySelector('#account-message').textContent = '退出失败，请重试。';
        button.disabled = false;
      }
    });
    return;
  }

  const recovering = location.pathname === '/recover';
  const registering = location.pathname === '/register';
  const title = document.querySelector('#auth-title');
  const feedback = document.querySelector('#auth-feedback');
  const submit = document.querySelector('#auth-submit');
  const password = document.querySelector('#password');
  const invite = document.querySelector('#signup-code');
  const registerTab = document.querySelector('#register-tab');
  const path = recovering ? '/api/auth/reset-password' : registering ? '/api/auth/travel-register' : '/api/auth/login';
  const action = recovering ? '重设密码' : registering ? '注册并进入' : '登录';

  const showError = message => {
    feedback.textContent = message;
    feedback.hidden = !message;
  };

  if (registering) {
    document.querySelector('#visitor-fields').hidden = false;
    document.querySelector('#visitor-fields').disabled = false;
    title.textContent = '创建旅行账号';
    document.title = '账号注册 · 贵州山水';
    password.autocomplete = 'new-password';
    password.minLength = 6;
    password.setAttribute('aria-describedby', 'password-hint');
    document.querySelector('#username').setAttribute('aria-describedby', 'username-hint');
    document.querySelector('#username-hint').hidden = false;
    document.querySelector('#password-hint').hidden = false;
  }
  const $ = id => document.getElementById(id);
  const confirming=registering || recovering;
  $('confirm-field').hidden=!confirming;
  $('confirm-password').required=confirming;
  $('forgot-password').hidden=confirming;
  if(recovering){
    title.textContent=document.title='找回密码';
    $('password-label').textContent='新密码';
    $('recovery-field').hidden=false;
    $('recovery-code').required=true;
    password.autocomplete='new-password';
    password.minLength=6;
  }
  $('reveal-password').onclick=()=>{
    const visible=password.type==='password';
    password.type=visible?'text':'password';
    $('reveal-password').textContent=visible?'隐藏密码':'显示密码';
    $('reveal-password').setAttribute('aria-pressed',String(visible));
  };
  function captcha(data){
    $('captcha-field').hidden=!data?.captcha_required;
    $('captcha').required=!!data?.captcha_required;
    $('captcha').value='';
    if(data?.image)$('captcha-image').src=data.image;
  }
  $('username').addEventListener('input',()=>captcha(null));
  $('refresh-captcha').onclick=async()=>{
    try{captcha(await (await post('/api/auth/login-challenge',{username:$('username').value})).json());}
    catch(error){showError(error.message);}
  };
  function showCode(code){
    form.hidden=true;$('forgot-password').hidden=true;
    title.textContent=registering?'注册成功':'密码已重设';
    $('issued-code').textContent=code;$('recovery-result').hidden=false;
    if(recovering){$('auth-continue').href='/login';$('auth-continue').textContent='已保存，去登录';}
    $('auth-continue').focus();
  }
  if(!recovering)
  document.querySelector(registering ? '#register-tab' : '#login-tab').setAttribute('aria-current', 'page');
  submit.textContent = action;

  async function loadOptions() {
    try {
      const response = await fetch('/api/auth/options', {cache: 'no-store'});
      if (!response.ok) throw new Error('账户服务暂时不可用，请刷新后重试。');
      const options = await response.json();
      registerTab.hidden = !options.registration_enabled && !registering;
      if (registering && !options.registration_enabled) {
        showError('此站点暂未开放注册。');
        return;
      }
      if (registering && options.signup_code_required) {
        document.querySelector('#invite-field').hidden = false;
        invite.required = true;
      }
      submit.disabled = false;
    } catch (error) {
      showError(error.message);
    }
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    submit.disabled = true;
    submit.textContent = registering ? '正在创建账号…' : '正在登录…';
    showError('');
    const data = {username: document.querySelector('#username').value, password: password.value};
    if (registering) {
      const values = new FormData(form);
      data.profile = {age:Number(values.get('age')), gender:values.get('gender'), intended_region:values.get('intended_region'),
        interests:values.get('interests') || null, walking_preference:values.get('walking_preference') || null};
    }
    if(confirming && password.value!==$('confirm-password').value){
      showError('两次输入的密码不同。');submit.disabled=false;submit.textContent=action;$('confirm-password').focus();return;
    }
    if(recovering){data.recovery_code=$('recovery-code').value.trim();data.new_password=data.password;delete data.password;}
    if($('captcha').required)data.captcha=$('captcha').value;
    if (invite.required) data.signup_code = invite.value;
    try {
      const result=await (await post(path, data)).json();
      if(result.recovery_code)showCode(result.recovery_code);else window.location.replace('/');
    } catch (error) {
      showError(error.message);
      if(error.challenge){captcha(error.challenge);$('captcha').focus();}else password.focus();
    } finally {
      submit.disabled = false;
      submit.textContent = action;
    }
  });

  loadOptions();
})();
