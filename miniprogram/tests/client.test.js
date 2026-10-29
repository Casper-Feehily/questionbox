const test = require('node:test');
const assert = require('node:assert/strict');
const config = require('../config');
const api = require('../utils/api');
const mock = require('../utils/mock');
const { subscribe } = require('../utils/page');
let storage;

function setup() {
  storage = new Map();
  global.wx = {
    getStorageSync: key => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, structuredClone(value)),
    removeStorageSync: key => storage.delete(key),
    request: () => { throw new Error('Demo must not send network requests'); },
    login: () => { throw new Error('Demo must not log in'); },
    requestSubscribeMessage: () => { throw new Error('Demo must not authorise messages'); },
    showToast: () => {},
  };
  config.demo = true;
}

test('demo submission is local, idempotent, private, and never contacts external services', async () => {
  setup();
  const before = await api.wall();
  const payload = { text: '新投递的私人便签', signature: '', email: 'demo@example.com', client_request_id: api.uuid() };
  const first = await api.ask(payload);
  const replay = await api.ask(payload);
  assert.equal(first.created, true);
  assert.equal(replay.created, false);
  assert.equal(first.question.id, replay.question.id);
  assert.equal(first.question.answer, null);
  assert.equal((await api.wall()).count, before.count);
  assert.equal((await api.mine()).count, 3);
  await assert.rejects(api.detail(first.question.id, false), /内容已不存在/);
  assert.equal((await api.detail(first.question.id, true)).question.text, payload.text);
  assert.equal((await api.reminder(first.question.id, true)).question.wechat_reminder.enabled, true);
  assert.equal((await api.reminder(first.question.id, false)).question.wechat_reminder.enabled, false);
  await assert.rejects(api.ask({ ...payload, text: '相同标识不同内容' }), /投递标识/);
  assert(!('email' in first.question));
  assert(!('client_request_id' in first.question));
});

test('demo rejects oversized Unicode and separates public and my records', async () => {
  setup();
  const payload = { text: '😀'.repeat(700), signature: '', email: '', client_request_id: api.uuid() };
  assert.equal((await api.ask(payload)).created, true);
  await assert.rejects(api.ask({ ...payload, text: '😀'.repeat(701), client_request_id: api.uuid() }), /700/);
  await assert.rejects(api.ask({ ...payload, signature: '署'.repeat(31), client_request_id: api.uuid() }), /30/);
  const publicId = '22222222-2222-4222-8222-222222222222';
  assert((await api.detail(publicId, false)).question.answer);
  await assert.rejects(api.detail(publicId, true), /内容已不存在/);
});

test('real public reads do not log in and 401 private reads re-login only once', async () => {
  setup(); config.demo = false;
  let logins = 0, privateReads = 0;
  const requests = [];
  wx.login = options => { logins++; options.success({ code: 'wx-code' }); };
  wx.request = options => {
    requests.push(options);
    if (options.url.endsWith('/wechat/session/')) {
      options.success({ statusCode: 200, data: { token: 'new-token', expires_at: '2099-01-01T00:00:00Z' } });
    } else if (options.url.includes('/me/questions/')) {
      privateReads++;
      options.success(privateReads === 1 ? { statusCode: 401, data: { error: { message: 'expired' } } } :
        { statusCode: 200, data: { items: [], count: 0, has_more: false } });
    } else options.success({ statusCode: 200, data: { items: [], count: 0, has_more: false } });
  };
  await api.wall();
  assert.equal(logins, 0);
  storage.set('qb-session', { token: 'old-token', expires_at: '2099-01-01T00:00:00Z' });
  await api.mine();
  assert.equal(logins, 1);
  assert.equal(privateReads, 2);
  assert.equal(requests.at(-1).header.Authorization, 'Bearer new-token');
  wx.request = options => options.success({ statusCode: 401, data: { error: { message: 'still expired' } } });
  await assert.rejects(api.mine(), /still expired/);
  assert.equal(logins, 2);
});

test('submission network failures carry uncertainty and retries preserve the request id', async () => {
  setup(); config.demo = false;
  storage.set('qb-session', { token: 'token', expires_at: '2099-01-01T00:00:00Z' });
  const sent = [];
  wx.request = options => { sent.push(options.data); options.fail(); };
  const payload = { text: '便签', client_request_id: api.uuid() };
  await assert.rejects(api.ask(payload), error => error.uncertain === true);
  await assert.rejects(api.ask(payload), error => error.uncertain === true);
  assert.equal(sent[0].client_request_id, sent[1].client_request_id);
});

test('subscribe starts synchronously from a tap and denial never enables a reminder', async () => {
  setup();
  let called = false;
  wx.requestSubscribeMessage = options => { called = true; options.success({ template: 'reject' }); };
  const denied = subscribe({ reply_template_id: 'template' });
  assert(called);
  await assert.rejects(denied, /未开启微信提醒/);
});

test('ask page retains draft and submission id after uncertain failure, then clears on success', async () => {
  setup();
  let page;
  global.Page = definition => { page = definition; };
  global.getApp = () => ({ paint: () => {} });
  require('../pages/ask/index');
  page.data = structuredClone(page.data);
  page.setData = fields => Object.assign(page.data, fields);
  page.onLoad({});
  page.data.meta = await api.meta();
  page.input({ currentTarget: { dataset: { field: 'text' } }, detail: { value: '测试便签' } });
  const originalAsk = api.ask;
  let sentPayload;
  api.ask = async payload => { sentPayload = payload; const e = new Error('timeout'); e.uncertain = true; throw e; };
  await page.submit();
  assert.equal(page.data.text, '测试便签');
  assert.equal(page.data.uncertain, true);
  const requestId = sentPayload.client_request_id;
  api.ask = async payload => { assert.equal(payload.client_request_id, requestId); return originalAsk(payload); };
  await page.submit();
  api.ask = originalAsk;
  assert.equal(page.data.text, '');
  assert.equal(page.data.success.text, '测试便签');
  assert.equal(storage.has('qb-draft-demo'), false);
});

test('theme preference follows system changes on every open page', () => {
  setup();
  let app, systemChanged;
  global.App = definition => { app = definition; };
  wx.getSystemInfoSync = () => ({ theme: 'light' });
  wx.onThemeChange = handler => { systemChanged = handler; };
  wx.setNavigationBarColor = () => {};
  wx.setBackgroundColor = () => {};
  wx.setTabBarStyle = () => {};
  const bars = [{}, {}];
  const pages = [{}, {}].map((data, index) => ({ data, setData: fields => Object.assign(data, fields),
    getTabBar: () => ({ setData: fields => Object.assign(bars[index], fields) }) }));
  global.getCurrentPages = () => pages;
  require('../app');
  app.onLaunch();
  app.setTheme('dark');
  assert(pages.every(page => page.data.theme === 'dark'));
  assert(bars.every(bar => bar.theme === 'dark'));
  assert.equal(storage.get('qb-theme'), 'dark');
  app.setTheme('system');
  assert(pages.every(page => page.data.theme === 'light'));
  assert(bars.every(bar => bar.theme === 'light'));
  systemChanged({ theme: 'dark' });
  assert(pages.every(page => page.data.theme === 'dark'));
});

test('parallel private requests share a single WeChat login', async () => {
  setup(); config.demo = false;
  let logins = 0;
  wx.login = options => { logins++; queueMicrotask(() => options.success({ code: 'code' })); };
  wx.request = options => {
    if (options.url.endsWith('/wechat/session/')) options.success({ statusCode: 200, data: { token: 'token', expires_at: '2099-01-01T00:00:00Z' } });
    else options.success({ statusCode: 200, data: { items: [], count: 0, has_more: false } });
  };
  await Promise.all([api.mine(), api.mine()]);
  assert.equal(logins, 1);
});
