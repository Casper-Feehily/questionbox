const test = require('node:test');
const assert = require('node:assert/strict');
const api = require('../utils/api');
const config = require('../config');

function setup() {
  config.demo = false;
  const requests = [];
  requests.scrolls = [];
  global.wx = {
    getStorageSync: () => undefined,
    nextTick: callback => callback(),
    pageScrollTo: options => requests.scrolls.push(options),
    request: options => {
      if (options.url.endsWith('/meta/')) options.success({ statusCode: 200, data: { owner_name: '站长', wechat_enabled: false } });
      else requests.push(options);
    }
  };
  global.getApp = () => ({ paint() {} });
  return requests;
}

function page(mine = false) {
  let definition;
  global.Page = value => { definition = value; };
  require('../utils/list')(mine);
  definition.data = structuredClone(definition.data);
  definition.setData = patch => Object.assign(definition.data, patch);
  return definition;
}

function answer(id) {
  return { id, text: '公开问题', signature: '', display_name: '匿名朋友', answer: '回答',
    created_at: '2026-10-01T00:00:00Z', answered_at: '2026-10-02T00:00:00Z',
    answer_updated_at: '2026-10-02T00:00:00Z', status: 'answered', version: 1,
    is_featured: false, tags: [{ id: 3, name: '学习' }] };
}

function respond(request, id, more = false, { page = 1, count = 1 } = {}) {
  request.success({ statusCode: 200, data: { items: id ? [answer(id)] : [], page, has_more: more, count,
    selected_view: 'all', tags: [{ id: 3, name: '学习' }], public_answer_count: 8, public_topic_count: 2 } });
}

test('public filter requests use the shared API without a WeChat login', async () => {
  const requests = setup();
  const pending = api.wall(2, { view: 'all', tag: 3, per_page: 14 });
  assert.equal(requests[0].url, 'https://ask.example.com/api/v1/questions/?page=2&view=all&tag=3&per_page=14');
  assert.equal(requests[0].header.Authorization, undefined);
  respond(requests[0], 'public');
  await pending;
});

test('demo browsing uses the same featured and tag rules and exposes only public topics', async () => {
  setup(); config.demo = true;
  const all = await api.wall(1, { view: 'all', per_page: 14 });
  assert.equal(all.public_answer_count, 2);
  assert.equal(all.public_topic_count, 2);
  const featured = await api.wall(1, { view: 'featured', tag: 2 });
  assert.equal(featured.count, 1);
  assert.equal(featured.items[0].is_featured, true);
  const filtered = await api.wall(1, { view: 'all', tag: 2 });
  assert.equal(filtered.count, 1);
  assert.equal(filtered.items[0].text, '刚开始学编程时，应该怎样保持好奇心？');
  assert.equal((await api.wall(1, { view: 'all', tag: 'invalid' })).count, 0);
});

test('switching scope while loading discards the previous response and keeps public statistics', async () => {
  const requests = setup(), wall = page();
  const first = wall.reload();
  assert.match(requests[0].url, /view=featured/);
  const second = wall.selectView({ currentTarget: { dataset: { view: 'all' } } });
  assert.equal(requests.length, 2);
  respond(requests[1], 'new');
  await second;
  respond(requests[0], 'old');
  await first;
  assert.equal(wall.data.items[0].id, 'new');
  assert.equal(wall.data.publicCount, 8);
  assert.equal(wall.data.topicCount, 2);
  assert.equal(wall.data.tags[0].name, '学习');
  assert.equal(wall.data.loading, false);
});

test('tag switching invalidates an in-flight next page and returning to featured clears the tag', async () => {
  const requests = setup(), wall = page();
  const first = wall.selectView({ currentTarget: { dataset: { view: 'all' } } });
  respond(requests[0], 'first', true, { count: 14 });
  await first;
  const more = wall.changePage({ currentTarget: { dataset: { page: 2 } } });
  const filtered = wall.selectTag({ currentTarget: { dataset: { tag: 3 } } });
  assert.match(requests[2].url, /view=all&tag=3/);
  respond(requests[2], 'filtered');
  await filtered;
  respond(requests[1], 'stale-next-page');
  await more;
  assert.deepEqual(wall.data.items.map(q => q.id), ['filtered']);
  const featured = wall.selectView({ currentTarget: { dataset: { view: 'featured' } } });
  assert.equal(wall.data.tag, '');
  assert(!requests[3].url.includes('&tag='));
  respond(requests[3], 'featured');
  await featured;
});

test('wall replaces seven-answer pages and preserves the tag while moving backward and forward', async () => {
  const requests = setup(), wall = page();
  const filtered = wall.selectTag({ currentTarget: { dataset: { tag: 3 } } });
  assert.match(requests[0].url, /page=1&view=all&tag=3&per_page=7$/);
  respond(requests[0], 'first', true, { count: 70 });
  await filtered;
  for (const [target, expected] of [[2, [1, 2, 3, 4, 5]], [6, [4, 5, 6, 7, 8]],
    [10, [6, 7, 8, 9, 10]], [1, [1, 2, 3, 4, 5]]]) {
    const pending = wall.changePage({ currentTarget: { dataset: { page: String(target) } } });
    assert.match(requests.at(-1).url, new RegExp(`page=${target}&view=all&tag=3&per_page=7$`));
    respond(requests.at(-1), `page-${target}`, target < 10, { page: target, count: 70 });
    await pending;
    assert.deepEqual(wall.data.items.map(q => q.id), [`page-${target}`]);
    assert.equal(wall.data.page, target);
    assert.deepEqual(wall.data.pageNumbers, expected);
    assert.equal(requests.scrolls.at(-1).selector, '#answers-title');
  }
  const before = requests.length;
  wall.changePage({ currentTarget: { dataset: { page: 1 } } });
  wall.changePage({ currentTarget: { dataset: { page: 11 } } });
  wall.onReachBottom();
  assert.equal(requests.length, before);
  assert.equal(wall.data.totalPages, 10);
});

test('failed page retains the displayed answers and retry requests the failed page', async () => {
  const requests = setup(), wall = page();
  const first = wall.reload();
  respond(requests[0], 'first', true, { count: 21 });
  await first;
  const second = wall.changePage({ currentTarget: { dataset: { page: 2 } } });
  requests[1].fail();
  await second;
  assert.equal(wall.data.page, 1);
  assert.deepEqual(wall.data.items.map(q => q.id), ['first']);
  assert.equal(requests.scrolls.length, 0);
  assert(wall.data.error);
  const retry = wall.retry();
  assert.match(requests[2].url, /page=2&/);
  respond(requests[2], 'second', true, { page: 2, count: 21 });
  await retry;
  assert.equal(wall.data.page, 2);
  assert.equal(wall.data.error, '');
});

test('latest page click wins and reopening the wall refreshes its current page', async () => {
  const requests = setup(), wall = page();
  const first = wall.reload();
  respond(requests[0], 'first', true, { count: 21 });
  await first;
  const second = wall.changePage({ currentTarget: { dataset: { page: 2 } } });
  const third = wall.changePage({ currentTarget: { dataset: { page: 3 } } });
  respond(requests[2], 'third', false, { page: 3, count: 21 });
  await third;
  respond(requests[1], 'stale-second', true, { page: 2, count: 21 });
  await second;
  assert.equal(wall.data.page, 3);
  assert.deepEqual(wall.data.items.map(q => q.id), ['third']);
  wall.onShow();
  assert.match(requests[3].url, /page=3&/);
  respond(requests[3], 'refreshed', false, { page: 3, count: 21 });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(wall.data.page, 3);
  assert.equal(requests.scrolls.length, 1);
  const filtered = wall.selectTag({ currentTarget: { dataset: { tag: 3 } } });
  assert.match(requests[4].url, /page=1&view=all&tag=3/);
  respond(requests[4], 'filtered');
  await filtered;
  assert.deepEqual(wall.data.pageNumbers, [1]);
  assert.equal(wall.data.totalPages, 1);
});

test('wall falls back to the last page when answers disappear, including an empty wall', async () => {
  const requests = setup(), wall = page();
  const first = wall.reload();
  respond(requests[0], 'first', true, { count: 21 });
  await first;
  const third = wall.changePage({ currentTarget: { dataset: { page: 3 } } });
  respond(requests[1], null, false, { page: 3, count: 8 });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(requests[2].url, /page=2&/);
  respond(requests[2], 'last', false, { page: 2, count: 8 });
  await third;
  assert.equal(wall.data.page, 2);
  assert.deepEqual(wall.data.pageNumbers, [1, 2]);
  const refresh = wall.reload();
  respond(requests[3], null, false, { page: 2, count: 0 });
  await new Promise(resolve => setImmediate(resolve));
  respond(requests[4], null, false, { count: 0 });
  await refresh;
  assert.equal(wall.data.page, 1);
  assert.deepEqual(wall.data.items, []);
  assert.deepEqual(wall.data.pageNumbers, [1]);
});

test('my questions still appends ten-question pages on reaching the bottom', async () => {
  const requests = setup(), mine = page(true);
  wx.getStorageSync = () => ({ token: 'token', expires_at: '2099-01-01T00:00:00Z' });
  const first = mine.reload();
  await new Promise(resolve => setImmediate(resolve));
  respond(requests[0], 'first', true, { count: 11 });
  await first;
  mine.onReachBottom();
  await new Promise(resolve => setImmediate(resolve));
  assert.match(requests[1].url, /\/me\/questions\/\?page=2$/);
  respond(requests[1], 'second', false, { page: 2, count: 11 });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(mine.data.items.map(q => q.id), ['first', 'second']);
});
