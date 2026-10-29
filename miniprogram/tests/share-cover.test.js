const test = require('node:test');
const assert = require('node:assert/strict');

test('cover wraps by measured width, preserves emoji, and ellipsizes overflowing questions', () => {
  const { wrapText } = require('../utils/share-cover');
  const context = { measureText: text => ({ width: Array.from(text).length * 10 }) };
  assert.deepEqual(wrapText(context, '😀你好世界', 30, 2), ['😀你好', '世界']);
  assert.deepEqual(wrapText(context, '甲乙丙丁戊己庚辛', 30, 2), ['甲乙丙', '丁戊…']);
  assert.deepEqual(wrapText(context, '  你好\n世界  ', 100, 2), ['你好 世界']);
});
