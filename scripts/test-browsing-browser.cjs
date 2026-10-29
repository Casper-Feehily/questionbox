// Use a disposable local database with >14 featured public letters, ordinary public letters and two public tags.
// BROWSING_TEST_URL defaults to http://127.0.0.1:8130/.
// Owner checks additionally require BROWSING_TEST_OWNER_URL, USER and PASSWORD.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const origin = process.env.BROWSING_TEST_URL || 'http://127.0.0.1:8130/';
if (!['localhost', '127.0.0.1'].includes(new URL(origin).hostname)) throw new Error('Use a disposable local database.');
const url = path => new URL(path, origin).href;
const ready = page => page.waitForFunction(() => !document.querySelector('[data-answer-wall]')?.hasAttribute('aria-busy'));
const fits = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 },
        isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      assert.equal((await page.goto(origin)).status(), 200);
      await ready(page);
      await page.locator(`[data-theme-option="${theme}"]`).click();
      assert.equal(await page.locator('textarea[name="text"]').count(), 0);
      assert.ok((await page.locator('.view-tabs [aria-current]').textContent()).includes('精选回答'));
      assert.equal(await page.locator('.category-card').count(), 0);
      assert.equal(await page.locator('.answer-card').count(), mobile ? 7 : 14);
      assert.ok((await page.locator('.card-stamp').allTextContents()).every(text => text.includes('精选')), 'Featured view must exclude ordinary answers');
      assert.equal(await page.locator('.tag-filters').count(), 0);
      assert.ok(await fits(page), 'Homepage must not overflow');
      assert.ok((await page.locator('.answer-card').first().boundingBox()).y < (mobile ? 844 : 900), 'First viewport must show answers');
      assert.ok(!(await page.locator('body').textContent()).includes('PRIVATE-TAG-HIDDEN'));
      await page.screenshot({ path: `/private/tmp/questionbox-home-${mobile ? 'mobile' : 'desktop'}-${theme}.png`, fullPage: true });

      await page.locator('.view-tabs a').nth(1).click();
      await ready(page);
      await page.locator('.tag-filters a').nth(1).click();
      await ready(page);
      const tag = new URL(page.url()).searchParams.get('tag');
      assert.ok(tag);
      assert.equal(new URL(page.url()).searchParams.get('view'), 'all');
      assert.equal(await page.locator('.tag-filters').count(), 1);
      await page.locator('.view-tabs a').nth(1).click();
      await ready(page);
      assert.equal(new URL(page.url()).searchParams.get('tag'), tag);
      const next = page.locator('.pagination a').filter({ hasText: '2' });
      if (await next.count()) {
        await next.click(); await ready(page);
        assert.equal(new URL(page.url()).searchParams.get('view'), 'all');
        assert.equal(new URL(page.url()).searchParams.get('tag'), tag);
      }
      await page.locator('.reader-entry').click();
      await page.waitForFunction(() => document.querySelector('[data-deck]')?.getAttribute('aria-busy') === 'false');
      assert.equal(new URL(page.url()).searchParams.has('tag'), false);
      const total = Number(await page.locator('[data-total]').textContent());
      assert.ok(total > 14);
      await page.selectOption('#reader-tag', tag);
      await page.waitForURL(current => current.searchParams.get('tag') === tag);
      await page.waitForFunction(() => document.querySelector('[data-deck]')?.getAttribute('aria-busy') === 'false');
      const pool = await page.evaluate(() => JSON.parse(document.getElementById('reader-data').textContent).order);
      assert.ok(pool.length < total && pool.length > 1);
      assert.equal((await page.locator('[data-position]').textContent()).trim(), '01');
      await page.locator('[data-shuffle]').click();
      await page.waitForFunction(() => document.querySelector('[data-deck]')?.getAttribute('aria-busy') === 'false');
      assert.equal(new URL(page.url()).searchParams.get('tag'), tag);
      assert.ok(pool.includes(await page.locator('[data-slot="0"]').getAttribute('data-id')));
      assert.ok(await fits(page));
      await page.screenshot({ path: `/private/tmp/questionbox-reader-${mobile ? 'mobile' : 'desktop'}-${theme}.png` });

      await page.goto(url('/ask/'));
      await page.locator('[name="text"]').fill('你好🌟');
      assert.equal((await page.locator('#question-count').textContent()).trim(), '3 / 700');
      await page.locator('[name="signature"]').fill('浏览器测试');
      assert.ok(await fits(page), 'Ask page must not overflow');
      await page.screenshot({ path: `/private/tmp/questionbox-ask-${mobile ? 'mobile' : 'desktop'}-${theme}.png`, fullPage: true });
      await page.locator('.ask-form button[type="submit"]').click();
      await page.waitForURL('**/thanks/');
      assert.equal(await page.locator('a[href="/ask/"]').count(), 2);
      console.log(`PASS browsing, filter, reading and submission: ${mobile ? 'mobile' : 'desktop'} ${theme}`);
      await context.close();
    }

    // No-JavaScript browsing and filtering remains server rendered.
    const plain = await browser.newContext({ javaScriptEnabled: false });
    const page = await plain.newPage();
    await page.goto(origin);
    await page.locator('.view-tabs a').nth(1).click();
    await page.locator('.tag-filters a').nth(1).click();
    assert.ok(await page.locator('.answer-card').count());
    await plain.close();

    if (process.env.BROWSING_TEST_OWNER_URL) {
      const context = await browser.newContext();
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(url('/owner/login/'));
      await page.locator('[name="username"]').fill(process.env.BROWSING_TEST_USER);
      await page.locator('[name="password"]').fill(process.env.BROWSING_TEST_PASSWORD);
      await page.locator('button[type="submit"]').click();
      await page.waitForURL('**/owner/');
      await page.goto(url(process.env.BROWSING_TEST_OWNER_URL));
      const answer = page.locator('[name="answer"]');
      const editingAnswer = `分类保存时仍在编辑的回答 ${Date.now()}`;
      await answer.fill(editingAnswer);
      await page.locator('[name="new_tags"]').fill('浏览器新标签');
      await page.locator('[name="is_featured"]').check();
      const oldUrl = page.url();
      let release;
      const held = new Promise(resolve => { release = resolve; });
      await page.route('**/classification/', async route => {
        const response = await route.fetch();
        await held;
        await route.fulfill({ response });
      });
      await page.locator('[data-classification] button').click();
      assert.ok(await page.locator('[name="new_tags"]').isDisabled(), 'Pending classification must lock its own inputs');
      assert.ok(await answer.isEditable(), 'Classification saving must leave answer editing available');
      release();
      await page.waitForFunction(() => document.querySelector('[data-classification-status]').textContent === '精选与标签已保存。');
      await page.unroute('**/classification/');
      assert.equal(page.url(), oldUrl);
      assert.equal(await answer.inputValue(), editingAnswer);
      assert.equal(await page.locator('[name="new_tags"]').inputValue(), '');
      assert.ok(await page.getByLabel('浏览器新标签', { exact: true }).isChecked());
      await page.waitForFunction(() => document.querySelector('[data-autosave-status]').textContent === '草稿已自动保存');
      await page.reload();
      assert.equal(await answer.inputValue(), editingAnswer);
      await page.locator('[name="new_tags"]').fill('x'.repeat(31));
      await page.locator('[data-classification] button').click();
      await page.waitForFunction(() => document.querySelector('[data-classification-status]').textContent.includes('30 字'));
      assert.equal(await answer.inputValue(), editingAnswer);
      await context.close();
      console.log('PASS classification saves in place, retains edits and reports validation errors');
    }
    assert.deepEqual(errors, []);
    console.log('PASS no JavaScript browsing; no page errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
