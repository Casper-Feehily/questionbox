const test = require('node:test');
const assert = require('node:assert/strict');

function tabBar(t, route = 'pages/wall/index') {
  const saved = { Component: global.Component, getCurrentPages: global.getCurrentPages, getApp: global.getApp, wx: global.wx };
  let definition, keyboard, currentRoute = route, theme = 'light';
  const requests = [];
  global.Component = value => { definition = value; };
  global.getCurrentPages = () => [{ route: currentRoute }];
  global.getApp = () => ({ theme: () => theme });
  global.wx = {
    switchTab: options => requests.push(options),
    onKeyboardHeightChange: listener => { keyboard = listener; },
    offKeyboardHeightChange: listener => { if (keyboard === listener) keyboard = null; }
  };
  const path = require.resolve('../custom-tab-bar/index');
  delete require.cache[path];
  require(path);
  const bar = Object.assign({}, definition.methods, { data: structuredClone(definition.data) });
  bar.setData = patch => Object.assign(bar.data, patch);
  definition.lifetimes.attached.call(bar);
  t.after(() => {
    definition.lifetimes.detached.call(bar);
    Object.assign(global, saved);
    delete require.cache[path];
  });
  return { bar, requests, show: () => definition.pageLifetimes.show.call(bar),
    enter: value => { currentRoute = value; }, theme: value => { theme = value; },
    keyboard: height => keyboard({ height }) };
}

test('bottom navigation follows programmatic page changes and system theme', t => {
  const ui = tabBar(t, 'pages/mine/index');
  assert.equal(ui.bar.data.selected, 2);
  ui.enter('pages/ask/index'); ui.theme('dark'); ui.show();
  assert.equal(ui.bar.data.selected, 1);
  assert.equal(ui.bar.data.theme, 'dark');
  ui.enter('pages/wall/index'); ui.show();
  assert.equal(ui.bar.data.selected, 0);
});

test('page lifecycle can select its own tab before the native page stack catches up', t => {
  const ui = tabBar(t, 'pages/wall/index');
  ui.bar.sync('pages/ask/index');
  assert.equal(ui.bar.data.selected, 1);
  ui.bar.sync('pages/mine/index');
  assert.equal(ui.bar.data.selected, 2);
});

test('tab taps switch to the matching page and do not mis-highlight after a failed switch', t => {
  const ui = tabBar(t);
  const tap = index => ui.bar.switchTab({ currentTarget: { dataset: { index } } });
  tap(1);
  assert.equal(ui.requests[0].url, '/pages/ask/index');
  assert.equal(ui.bar.data.selected, 0);
  tap(2);
  assert.equal(ui.requests.length, 1, 'Ignore duplicate taps while navigation is pending');
  ui.requests[0].complete(); // Failure leaves the current route unchanged.
  assert.equal(ui.bar.data.selected, 0);
  tap(2);
  assert.equal(ui.requests[1].url, '/pages/mine/index');
  ui.enter('pages/mine/index'); ui.requests[1].complete();
  assert.equal(ui.bar.data.selected, 2);
  tap(2); tap(5); tap('bad');
  assert.equal(ui.requests.length, 2);
});

test('bottom navigation gets out of the way while typing a question', t => {
  const ui = tabBar(t, 'pages/ask/index');
  ui.keyboard(300);
  assert.equal(ui.bar.data.keyboardVisible, true);
  ui.keyboard(0);
  assert.equal(ui.bar.data.keyboardVisible, false);
});
