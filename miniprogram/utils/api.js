const config = require('../config');
const mock = require('./mock');
let loginPromise;

function request(path, method = 'GET', data, token) {
  return new Promise((resolve, reject) => wx.request({
    url: config.apiBase + path, method, data, timeout: 15000,
    header: Object.assign({ 'content-type': 'application/json' }, token ? { Authorization: `Bearer ${token}` } : {}),
    success(response) {
      if (response.statusCode >= 200 && response.statusCode < 300) { resolve(response.data); return; }
      const detail = response.data && response.data.error;
      let message = detail ? detail.message : '服务暂时不可用，请稍后重试。';
      if (detail && detail.fields) message = Object.values(detail.fields).flat().join(' ');
      const error = new Error(message);
      error.status = response.statusCode;
      error.code = detail && detail.code;
      error.retryAfter = detail && detail.retry_after;
      error.uncertain = response.statusCode >= 500;
      reject(error);
    },
    fail() {
      const error = new Error('网络连接未完成。请检查网络后重试，已填写的内容会保留。');
      error.uncertain = true;
      reject(error);
    }
  }));
}

async function session() {
  const saved = wx.getStorageSync('qb-session');
  if (saved && saved.token && Date.parse(saved.expires_at) > Date.now() + 60000) return saved.token;
  if (!loginPromise) loginPromise = new Promise((resolve, reject) => wx.login({
    success(result) { result.code ? resolve(result.code) : reject(new Error('微信登录未完成，请重试。')); },
    fail() { reject(new Error('微信登录暂时不可用，请稍后重试。')); }
  })).then(code => request('/wechat/session/', 'POST', { code })).then(result => {
    wx.setStorageSync('qb-session', result);
    return result.token;
  }).finally(() => { loginPromise = null; });
  return loginPromise;
}

async function call(path, method = 'GET', data, privateRequest = false) {
  if (config.demo) return mock.call(path, method, data);
  const token = privateRequest ? await session() : null;
  try { return await request(path, method, data, token); }
  catch (e) {
    if (!privateRequest || e.status !== 401) throw e;
    wx.removeStorageSync('qb-session');
    return request(path, method, data, await session()); // Only one re-login/replay.
  }
}

function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.floor(Math.random() * 16); return (c === 'x' ? r : (r & 3) | 8).toString(16);
  });
}

module.exports = {
  isDemo: () => config.demo, uuid,
  meta: () => call('/meta/'),
  wall: (page, filters = {}) => {
    const query = [`page=${page || 1}`];
    ['view', 'tag', 'per_page'].forEach(key => {
      if (filters[key] !== undefined && filters[key] !== '') query.push(`${key}=${encodeURIComponent(filters[key])}`);
    });
    return call(`/questions/?${query.join('&')}`);
  },
  mine: page => call(`/me/questions/?page=${page || 1}`, 'GET', undefined, true),
  detail: (id, mine) => call(`${mine ? '/me' : ''}/questions/${id}/`, 'GET', undefined, !!mine),
  ask: data => call('/questions/', 'POST', data, true),
  reminder: (id, enabled) => call(`/me/questions/${id}/wechat-reminder/`, 'POST', { enabled }, true)
};
