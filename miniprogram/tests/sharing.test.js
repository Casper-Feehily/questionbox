const test = require('node:test');
const assert = require('node:assert/strict');
const config = require('../config');

const HOME = '/pages/wall/index';
const COVER = '/assets/share-cover.png';
const ID = '11111111-1111-4111-8111-111111111111';

function pageFor(t, route) {
  const saved = { Page: global.Page, getApp: global.getApp, getCurrentPages: global.getCurrentPages, wx: global.wx, demo: config.demo };
  let page;
  const menus = [], hidden = [], requests = [];
  config.demo = false;
  global.Page = definition => { page = definition; };
  global.getApp = () => ({ paint() {} });
  global.wx = {
    showShareMenu: options => menus.push(options.menus),
    hideShareMenu: options => hidden.push(options.menus),
    getStorageSync: () => undefined,
    request: options => {
      requests.push(options);
      if (options.url.endsWith('/meta/')) options.success({ statusCode: 200, data: { owner_name: '站长' } });
      else options.success({ statusCode: 200, data: { question: {
        id: ID, text: ' 怎样保持好奇心？\n从哪里开始？ ', answer: '从小问题开始。', status: 'answered',
        created_at: '2026-10-27T00:00:00Z' } } });
    }
  };
  const path = require.resolve(`../pages/${route}/index`);
  delete require.cache[path];
  require(path);
  page.data = structuredClone(page.data);
  page.setData = (patch, callback) => { Object.assign(page.data, patch); if (callback) callback(); };
  t.after(() => {
    if (page.onUnload) page.onUnload();
    global.Page = saved.Page; global.getApp = saved.getApp; global.getCurrentPages = saved.getCurrentPages;
    global.wx = saved.wx; config.demo = saved.demo;
    delete require.cache[path];
  });
  return { page, menus, hidden, requests };
}

test('public wall enables both native sharing menus with a public route and fixed cover', t => {
  const { page, menus } = pageFor(t, 'wall');
  page.onLoad({});
  assert.deepEqual(menus.at(-1), ['shareAppMessage', 'shareTimeline']);
  const friend = page.onShareAppMessage();
  assert.equal(friend.path, HOME);
  assert.equal(friend.imageUrl, COVER);
  assert(friend.title.includes('提问箱'));
  assert.deepEqual(page.onShareTimeline(), { title: friend.title, query: '', imageUrl: COVER });
});

test('a published answer shares its public id and can be read without WeChat login', async t => {
  const { page, menus, requests } = pageFor(t, 'detail');
  page.onLoad({ id: ID });
  assert.deepEqual(menus.at(-1), ['shareAppMessage']);
  await page.reload();
  assert.deepEqual(menus.at(-1), ['shareAppMessage', 'shareTimeline']);
  const friend = page.onShareAppMessage(), timeline = page.onShareTimeline();
  assert.equal(friend.path, `/pages/detail/index?id=${ID}`);
  assert(friend.title.includes('怎样保持好奇心？'));
  assert.equal(friend.title, '怎样保持好奇心？ 从哪里开始？｜站长的提问箱');
  assert.equal(friend.imageUrl, COVER);
  assert.equal(timeline.query, `id=${ID}`);
  assert.equal(timeline.imageUrl, COVER);
  const read = requests.find(request => request.url.endsWith(`/questions/${ID}/`));
  assert(read);
  assert.equal(read.header.Authorization, undefined);
});

test('private question details only forward the public home and never expose their screenshot or query', t => {
  const { page, menus, hidden } = pageFor(t, 'detail');
  page.onLoad({ id: ID, mine: '1' });
  page.setData({ question: { id: ID, text: '私人内容', answer: null, status: 'pending' } });
  page.refreshShareMenu();
  assert.deepEqual(menus.at(-1), ['shareAppMessage']);
  assert.deepEqual(hidden.at(-1), ['shareTimeline']);
  const share = page.onShareAppMessage();
  assert.equal(share.path, HOME);
  assert.equal(share.imageUrl, COVER);
  assert(!JSON.stringify(share).includes('私人内容'));
  assert(!JSON.stringify(share).includes(ID));
  assert(!page.onShareTimeline().query.includes(ID));
});

for (const route of ['ask', 'mine', 'privacy']) {
  test(`${route} forwards the home with a safe cover instead of sharing personal page data`, t => {
    const { page, menus } = pageFor(t, route);
    page.setData({ text: '未提交草稿', email: 'private@example.com', items: [{ text: '私人记录' }] });
    page.refreshShareMenu();
    const share = page.onShareAppMessage();
    assert.equal(share.path, HOME);
    assert.equal(share.imageUrl, COVER);
    assert.equal(page.onShareTimeline, undefined);
    assert.deepEqual(menus.at(-1), ['shareAppMessage']);
    assert(!/未提交草稿|private@example|私人记录/.test(JSON.stringify(share)));
  });
}

test('reader shares the public reading experience through both menus', t => {
  const { page, menus } = pageFor(t, 'reader');
  page.refreshShareMenu();
  assert.deepEqual(menus.at(-1), ['shareAppMessage', 'shareTimeline']);
  assert.equal(page.onShareAppMessage().path, '/pages/reader/index');
  assert.equal(page.onShareTimeline().query, '');
  assert.equal(page.onShareTimeline().imageUrl, COVER);
});

test('reader opened directly from a share can return to the wall without a previous page', t => {
  const { page } = pageFor(t, 'reader');
  const navigation = [];
  wx.navigateBack = () => navigation.push('back');
  wx.switchTab = options => navigation.push(options.url);
  global.getCurrentPages = () => [page];
  page.back();
  assert.deepEqual(navigation, [HOME]);
  global.getCurrentPages = () => [{}, page];
  page.back();
  assert.deepEqual(navigation, [HOME, 'back']);
});

test('a shared answer can open the home tab even when there is no earlier page', async t => {
  const { page } = pageFor(t, 'detail');
  const destinations = [];
  wx.switchTab = options => destinations.push(options.url);
  global.getCurrentPages = () => [page];
  page.onLoad({ id: ID });
  await page.reload();
  page.goHome();
  page.goAsk();
  assert.deepEqual(destinations, [HOME, '/pages/ask/index']);
});

test('timeline single-page mode keeps the answer readable and avoids disabled share-menu APIs', async t => {
  const { page, menus, hidden, requests } = pageFor(t, 'detail');
  wx.getEnterOptionsSync = () => ({ scene: 1154 });
  page.onLoad({ id: ID });
  await page.reload();
  assert.equal(page.data.singlePage, true);
  assert.equal(page.data.question.status, 'answered');
  assert.equal(menus.length, 0);
  assert.equal(hidden.length, 0);
  assert(requests.some(request => request.url.endsWith(`/questions/${ID}/`)));
  wx.getEnterOptionsSync = () => ({ scene: 1007 });
  page.onShow();
  assert.equal(page.data.singlePage, false, 'Full app entry restores navigation controls');
});

test('missing or removed answers fall back to home and keep timeline sharing disabled', async t => {
  const { page, menus } = pageFor(t, 'detail');
  page.onLoad({ id: ID });
  await page.reload();
  wx.request = options => options.success({ statusCode: 404, data: { error: { message: '已删除' } } });
  await page.reload();
  assert.equal(page.onShareAppMessage().path, HOME);
  assert.deepEqual(menus.at(-1), ['shareAppMessage']);
});

function canvasSupport() {
  const exports = [], drawn = [];
  const context = new Proxy({ measureText: text => ({ width: Array.from(text).length * 40 }),
    fillText: text => drawn.push(text) }, { get: (target, key) => target[key] || (() => {}) });
  const canvas = { getContext: () => context };
  wx.createSelectorQuery = () => ({ in() { return this; }, select() { return this; },
    fields() { return this; }, exec(callback) { callback([{ node: canvas }]); } });
  wx.canvasToTempFilePath = options => exports.push(options);
  return { exports, drawn };
}

test('public answer prepares a question-specific cover for both sharing channels', async t => {
  const { page } = pageFor(t, 'detail');
  const canvas = canvasSupport();
  page.onLoad({ id: ID });
  await page.reload();
  page.onReady();
  assert.equal(canvas.exports.length, 1);
  assert(canvas.drawn.join('').includes('怎样保持好奇心？'));
  canvas.exports[0].success({ tempFilePath: 'wxfile://question-cover.png' });
  await page.coverJob;
  assert.equal(page.onShareAppMessage().imageUrl, 'wxfile://question-cover.png');
  assert.equal(page.onShareTimeline().imageUrl, 'wxfile://question-cover.png');
  await page.reload();
  assert.equal(canvas.exports.length, 1, 'Unchanged question reuses its cover');
});

test('private question never draws a cover even when its question has been answered', async t => {
  const { page } = pageFor(t, 'detail');
  const canvas = canvasSupport();
  page.onLoad({ id: ID, mine: '1' });
  page.setData({ question: { id: ID, text: '私人问题', answer: '回答', status: 'answered' } });
  page.onReady();
  assert.equal(canvas.exports.length, 0);
  assert.equal(canvas.drawn.length, 0);
  assert.equal(page.onShareAppMessage().imageUrl, COVER);
});

test('late cover export cannot overwrite a changed question or survive page unload', async t => {
  const { page } = pageFor(t, 'detail');
  const canvas = canvasSupport();
  page.onLoad({ id: ID }); await page.reload(); page.onReady();
  const firstJob = page.coverJob;
  page.setData({ question: { ...page.data.question, text: '更新后的问题' } });
  page.prepareShareCover();
  const nextJob = page.coverJob;
  assert.equal(page.onShareAppMessage().imageUrl, COVER);
  canvas.exports[1].success({ tempFilePath: 'wxfile://new.png' }); await nextJob;
  canvas.exports[0].success({ tempFilePath: 'wxfile://old.png' }); await firstJob;
  assert.equal(page.onShareAppMessage().imageUrl, 'wxfile://new.png');
  page.setData({ question: { ...page.data.question, text: '再次更新' } }); page.prepareShareCover();
  const lastJob = page.coverJob;
  page.onUnload();
  canvas.exports[2].success({ tempFilePath: 'wxfile://after-unload.png' }); await lastJob;
  assert.equal(page.onShareAppMessage().imageUrl, COVER);
});

test('failed cover generation safely uses the generic cover without blocking reading', async t => {
  const { page } = pageFor(t, 'detail');
  const canvas = canvasSupport();
  page.onLoad({ id: ID }); await page.reload(); page.onReady();
  canvas.exports[0].fail({ errMsg: 'export failed' }); await page.coverJob;
  assert.equal(page.onShareAppMessage().imageUrl, COVER);
  assert.equal(page.data.question.status, 'answered');
  assert.equal(page.data.error, '');
});
