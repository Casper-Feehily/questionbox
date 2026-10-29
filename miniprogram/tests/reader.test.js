const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

async function nativeReader(t) {
  const api = require('../utils/api');
  const saved = { wall: api.wall, meta: api.meta, Page: global.Page, wx: global.wx,
    getApp: global.getApp, setTimeout: global.setTimeout, clearTimeout: global.clearTimeout };
  let page, token = 0;
  const timers = new Map();
  global.Page = definition => { page = definition; };
  global.getApp = () => ({ paint() {} });
  global.wx = { nextTick: callback => callback(),
    createSelectorQuery: () => ({ in() { return this; }, select() { return this; },
      boundingClientRect(callback) { callback({ width: 320 }); return this; }, exec() {} }) };
  api.wall = async () => ({ items: ['a', 'b', 'c', 'd', 'e', 'f'].map(id => ({
    id, text: '问题', answer: '长回信', created_at: '2026-10-27T00:00:00Z' })), count: 6, has_more: false });
  api.meta = async () => ({ owner_name: '站长' });
  const modulePath = require.resolve('../pages/reader/index');
  delete require.cache[modulePath];
  require(modulePath);
  page.data = structuredClone(page.data);
  page.setData = (fields, callback) => { Object.assign(page.data, fields); if (callback) callback(); };
  t.after(() => {
    page.onUnload();
    api.wall = saved.wall; api.meta = saved.meta;
    for (const key of ['Page', 'wx', 'getApp', 'setTimeout', 'clearTimeout']) global[key] = saved[key];
    delete require.cache[modulePath];
  });
  page.onLoad({});
  await new Promise(resolve => setImmediate(resolve));
  global.setTimeout = (callback, delay) => { const id = ++token; timers.set(id, { callback, delay }); return id; };
  global.clearTimeout = id => timers.delete(id);
  const tick = () => { for (const [id, { callback }] of [...timers]) { timers.delete(id); callback(); } };
  const finish = async direction => { await page.move(direction); tick(); tick(); };
  return { page, tick, finish };
}

function transform(style) {
  const [, x, y] = style.match(/translate3d\(([-.\d]+)px,([-.\d]+)(?:px)?,0\)/);
  return { x: Number(x), y: Number(y), scale: Number(style.match(/scale\(([-.\d]+)\)/)?.[1] || 1),
    angle: Number(style.match(/rotate\(([-.\d]+)deg\)/)?.[1] || 0),
    opacity: Number(style.match(/opacity:([-.\d]+)/)[1]), z: Number(style.match(/z-index:(\d+)/)[1]) };
}

test('reader preserves history and colors through pagination and new rounds', async () => {
  assert.ok(fs.existsSync(require('node:path').join(__dirname, '../utils/reader.js')), 'The native sliding reader must exist');
  const { Reader } = require('../utils/reader');
  const calls = [];
  const reader = new Reader(async page => {
    calls.push(page);
    return { items: (page === 1 ? ['a', 'b'] : ['c', 'd']).map(id => ({ id, answer: id })), count: 4, has_more: page === 1 };
  }, () => .5);
  await reader.load();
  const seen = [];
  for (let i = 0; i < 9; i++) {
    const active = reader.visible()[0];
    seen.push({ id: active.question.id, tone: active.tone });
    const next = await reader.plan(1);
    reader.commit(next);
  }
  assert.deepEqual(new Set(seen.slice(0, 4).map(card => card.id)), new Set(['a', 'b', 'c', 'd']));
  assert.ok(seen.every((card, i) => !i || seen[i - 1].id !== card.id));
  for (let i = 8; i >= 0; i--) {
    reader.commit(await reader.plan(-1));
    const active = reader.visible()[0];
    assert.equal(active.question.id, seen[i].id);
    assert.equal(active.tone, seen[i].tone);
  }
  assert.equal(await reader.plan(-1), null);
  assert.deepEqual(calls, [1, 2]);
});

test('failed page loading keeps the current letter and retries the same page', async () => {
  const { Reader } = require('../utils/reader');
  let failing = true;
  const reader = new Reader(async page => {
    if (page === 2 && failing) throw new Error('offline');
    return { items: [{ id: page === 1 ? 'a' : 'b' }], has_more: page === 1, count: 2 };
  });
  await reader.load();
  await assert.rejects(reader.plan(1), /offline/);
  assert.equal(reader.visible()[0].question.id, 'a');
  failing = false;
  reader.commit(await reader.plan(1));
  assert.equal(reader.visible()[0].question.id, 'b');
});

test('right drag brings the previous card in at full opacity while current sinks', async () => {
  const { Reader, motion } = require('../utils/reader');
  const reader = new Reader(async () => ({ items: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], count: 3, has_more: false }));
  await reader.load();
  reader.commit(await reader.plan(1));
  const cards = reader.visible();
  const previous = cards.find(card => card.slot === -1);
  const current = cards.find(card => card.slot === 0);
  assert.equal(transform(motion(cards, 320)[previous.key]).opacity, 0,
    'Offscreen history must not leave a card edge or shadow visible at rest');
  const early = motion(cards, 320, 40), later = motion(cards, 320, 110);
  assert.ok(early[previous.key].includes('translate3d(-280px'));
  assert.ok(later[previous.key].includes('translate3d(-210px'));
  assert.ok(later[previous.key].includes('opacity:1'));
  assert.ok(later[current.key].includes('translate3d(0px,'));
  assert.equal(reader.index, 1, 'Dragging does not commit navigation');
});

test('native page measures after delayed loading and repeated quick releases keep following', async () => {
  const api = require('../utils/api');
  const originalWall = api.wall, originalMeta = api.meta;
  const originalTimeout = global.setTimeout, originalClear = global.clearTimeout;
  let page, release, token = 0, measured = 0;
  const timers = new Map();
  global.Page = definition => { page = definition; };
  global.getApp = () => ({ paint() {} });
  global.wx = { createSelectorQuery: () => ({ in() { return this; }, select() { return this; },
    boundingClientRect(callback) { measured++; callback(page.data.cards.length ? { width: 339 } : null); return this; }, exec() {} }) };
  api.wall = () => new Promise(resolve => { release = resolve; });
  api.meta = async () => ({ owner_name: '站长' });
  require('../pages/reader/index');
  page.data = structuredClone(page.data);
  page.setData = (fields, callback) => { Object.assign(page.data, fields); if (callback) callback(); };
  try {
    page.onLoad({}); page.onReady();
    assert.equal(page.width, 300);
    release({ items: ['a', 'b', 'c'].map(id => ({ id, text: '问题', answer: '长回信' })), has_more: false, count: 3 });
    await page.reader.pending;
    await Promise.resolve(); await Promise.resolve();
    assert.equal(page.width, 339);
    assert.ok(measured >= 2);
    global.setTimeout = callback => { const id = ++token; timers.set(id, callback); return id; };
    global.clearTimeout = id => timers.delete(id);
    const runTimers = () => { for (const [id, callback] of [...timers]) { timers.delete(id); callback(); } };
    const touch = (x, y = 0) => ({ touches: [{ clientX: x, clientY: y }] });
    page.touchStart(touch(200)); page.touchMove(touch(80));
    page.touchEnd(); // End before the queued drag frame runs.
    await Promise.resolve(); await Promise.resolve();
    runTimers(); runTimers();
    assert.equal(page.data.busy, false);
    assert.equal(page.reader.index, 1);
    page.touchStart(touch(100)); page.touchMove(touch(140)); runTimers();
    assert.equal(page.data.dragging, true);
    assert.ok(page.data.styles['letter-0'].includes('translate3d(-299px'));
    page.cancel();
    assert.equal(page.reader.index, 1);
    assert.equal(page.data.dragging, false);
    page.touchStart(touch(100)); page.touchMove(touch(102, 80));
    assert.equal(page.gesture, null, 'Vertical gestures belong to the answer scroll');
    page.scroll({ currentTarget: { dataset: { key: 'letter-1' } }, detail: { scrollTop: 180 } });
    await page.previous(); runTimers(); runTimers();
    await page.next(); runTimers(); runTimers();
    assert.equal(page.data.cards.find(card => card.slot === 0).scrollTop, 180);
    page.onUnload(); assert.equal(timers.size, 0);
  } finally {
    api.wall = originalWall; api.meta = originalMeta;
    global.setTimeout = originalTimeout; global.clearTimeout = originalClear;
  }
});

test('return transition keeps hidden history out of the deck and sinks the discarded preview behind it', async t => {
  const { page, tick, finish } = await nativeReader(t);
  await finish(1); await finish(1);
  assert.equal(page.reader.index, 2);
  await page.previous();
  const initial = structuredClone(page.data.styles);
  assert.equal(transform(initial['letter-1']).opacity, 1, 'Returning card is opaque throughout its slide');
  tick();
  for (const { key } of page.data.cards) {
    const from = transform(initial[key]), to = transform(page.data.styles[key]);
    assert(!(from.x > 0 && to.x < 0 && to.opacity > 0), `Hidden ${key} must not fly across the visible deck`);
  }
  const discarded = transform(page.data.styles['letter-4']);
  assert.equal(discarded.x, 0, 'The bottom preview sinks vertically rather than flying right');
  assert.equal(discarded.opacity, 0);
  assert(discarded.z < 4, 'The discarded preview stays behind the readable card');
  tick();
  assert.equal(page.reader.index, 1);
  assert.equal(page.data.busy, false);
});

test('return keeps card roles stable until the slide completes and restores scroll after committing', async t => {
  const { page, tick, finish } = await nativeReader(t);
  await finish(1); await finish(1);
  const oldActive = page.data.cards.find(card => card.slot === 0);
  page.scrolls.set(oldActive.question.id, 180);
  await page.previous();
  assert.equal(page.data.cards.find(card => card.key === oldActive.key).slot, 0,
    'Changing slots early switches stacked captions and scroll geometry mid-animation');
  tick(); tick();
  assert.equal(page.data.cards.find(card => card.key === oldActive.key).slot, 1);
  await finish(1);
  assert.equal(page.data.cards.find(card => card.slot === 0).scrollTop, 180);
});

test('quick right release flushes the latest drag position before starting the return animation', async t => {
  const { page, finish } = await nativeReader(t);
  await finish(1); await finish(1);
  const touch = x => ({ touches: [{ clientX: x, clientY: 0 }] });
  page.touchStart(touch(80));
  page.touchMove(touch(200)); // Release before the scheduled drag frame.
  page.touchEnd();
  await Promise.resolve(); await Promise.resolve();
  assert.equal(transform(page.data.styles['letter-1']).x, -200,
    'Animation must start where the finger released, without snapping back to -320px');
});
