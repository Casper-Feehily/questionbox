// Deterministic DOM regression test for a delayed classification response.
// Requires jsdom; it does not start a browser or contact a server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

(async () => {
  const dom = new JSDOM(`<form action="/classification/" data-classification>
    <input name="is_featured" type="checkbox" checked>
    <div data-tag-options><input name="tags" type="checkbox" value="1" checked></div>
    <input name="new_tags" value="新标签"><button type="submit">保存</button>
    <p data-classification-status></p></form><textarea name="answer">正在编辑</textarea>`,
  { url: 'http://localhost/', runScripts: 'outside-only' });
  const { window } = dom;
  const form = window.document.querySelector('form');
  let release;
  let body;
  window.fetch = async (_, options) => {
    body = options.body;
    return new Promise(resolve => { release = resolve; });
  };
  window.eval(fs.readFileSync(path.join(__dirname, '../static/classification.js'), 'utf8'));
  form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
  try {
    assert.equal(form.elements.new_tags.disabled, true, 'Pending classification inputs must be locked');
    assert.equal(form.elements.is_featured.disabled, true);
    assert.equal(window.document.querySelector('textarea').disabled, false, 'Answer editing remains available');
    assert.equal(body.get('new_tags'), '新标签', 'Request must capture fields before disabling them');
    release({ ok: true, status: 200, json: async () => ({ message: '精选与标签已保存。',
      is_featured: true, selected: [1], tags: [{ id: 1, name: '新标签' }] }) });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(form.elements.new_tags.disabled, false);
    assert.equal(form.elements.new_tags.value, '');
    assert.equal(form.elements.tags.checked, true);
    assert.equal(window.document.querySelector('textarea').value, '正在编辑');
    assert.equal(form.querySelector('[data-classification-status]').textContent, '精选与标签已保存。');
    form.elements.new_tags.value = '保留错误输入';
    form.dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));
    release({ ok: false, status: 400, json: async () => ({ errors: { new_tags: [{ message: '标签错误' }] } }) });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(form.elements.new_tags.value, '保留错误输入');
    assert.equal(form.elements.new_tags.disabled, false);
    assert.equal(form.querySelector('[data-classification-status]').textContent, '标签错误');
    console.log('PASS delayed classification response, independent answer edits and failure recovery');
  } finally { dom.window.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
