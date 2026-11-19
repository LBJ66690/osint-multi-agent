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
      throw new Error(typeof payload.detail === 'string' ? payload.detail : '请求未成功，请稍后重试。');
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

  const registering = location.pathname === '/register';
  const title = document.querySelector('#auth-title');
  const feedback = document.querySelector('#auth-feedback');
  const submit = document.querySelector('#auth-submit');
  const password = document.querySelector('#password');
  const invite = document.querySelector('#signup-code');
  const registerTab = document.querySelector('#register-tab');
  const path = registering ? '/api/auth/register' : '/api/auth/login';
  const action = registering ? '注册并进入' : '登录';

  const showError = message => {
    feedback.textContent = message;
    feedback.hidden = !message;
  };

  if (registering) {
    title.textContent = '创建工作台账号';
    document.title = '账号注册 · 情报指挥台';
    password.autocomplete = 'new-password';
    password.minLength = 12;
    password.setAttribute('aria-describedby', 'password-hint');
    document.querySelector('#username').setAttribute('aria-describedby', 'username-hint');
    document.querySelector('#username-hint').hidden = false;
    document.querySelector('#password-hint').hidden = false;
  }
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
    if (invite.required) data.signup_code = invite.value;
    try {
      await post(path, data);
      window.location.replace('/');
    } catch (error) {
      showError(error.message);
      password.focus();
    } finally {
      submit.disabled = false;
      submit.textContent = action;
    }
  });

  loadOptions();
})();
