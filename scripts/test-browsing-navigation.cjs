// Run against a disposable local database containing tagged featured and ordinary public answers.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const origin = process.env.BROWSING_TEST_URL || 'http://127.0.0.1:8130/';
if (!['localhost', '127.0.0.1'].includes(new URL(origin).hostname)) throw new Error('Use a disposable local database.');
const ready = page => page.waitForFunction(() => !document.querySelector('[data-answer-wall]')?.hasAttribute('aria-busy'));
const selected = page => page.locator('.view-tabs [aria-current]').innerText();
const choose = (page, view) => page.locator(`.view-tabs a[href*="view=${view}"]`).click();

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [];
  try {
    for (const mobile of [false, true]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 } });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(new URL('?view=featured', origin).href);
      await ready(page);
      await page.evaluate(() => { window.navigationSentinel = document.querySelector('.site-header'); });
      const staysOnPage = () => page.evaluate(() => window.navigationSentinel === document.querySelector('.site-header'));
      const featuredCount = await page.locator('.result-count').innerText();
      await page.locator('#answers-title').scrollIntoViewIfNeeded();
      const scroll = await page.evaluate(() => scrollY);
      await choose(page, 'all');
      await page.waitForURL(url => url.searchParams.get('view') === 'all');
      await ready(page);
      assert.ok(await staysOnPage(), 'Switching views must preserve the document and header');
      assert.ok((await selected(page)).includes('所有回答'));
      assert.ok(await page.evaluate(() => document.activeElement?.closest('.view-tabs') !== null), 'Switching must preserve focus on the browsing controls');
      assert.notEqual(await page.locator('.result-count').innerText(), featuredCount);
      assert.equal(await page.locator('.tag-filters').count(), 1);
      assert.ok(Math.abs((await page.evaluate(() => scrollY)) - scroll) < 3, 'Switching views must keep the reading position');

      await page.evaluate(() => history.back());
      await page.waitForFunction(() => document.querySelector('.view-tabs [aria-current]')?.textContent.includes('精选回答'));
      await ready(page);
      assert.ok(await staysOnPage(), 'Back must restore the previous view without reloading');
      assert.equal(await page.locator('.result-count').innerText(), featuredCount);
      await page.evaluate(() => history.forward());
      await page.waitForFunction(() => document.querySelector('.view-tabs [aria-current]')?.textContent.includes('所有回答'));
      await ready(page);

      await page.locator('.tag-filters a').nth(1).click();
      await ready(page);
      const tag = new URL(page.url()).searchParams.get('tag');
      assert.ok(tag);
      await choose(page, 'featured');
      await ready(page);
      assert.equal(new URL(page.url()).searchParams.get('tag'), null);
      assert.equal(await page.locator('.tag-filters').count(), 0);
      assert.ok(await staysOnPage(), 'Filters and switching a filtered view must preserve the document');
      await choose(page, 'all'); await ready(page);
      const pageTwo = page.locator('.pagination a[aria-label="第 2 页"]');
      if (await pageTwo.count()) {
        await pageTwo.click(); await ready(page);
        assert.equal(new URL(page.url()).searchParams.get('page'), '2');
        assert.ok(await staysOnPage(), 'Pagination must preserve the document');
        const headingTop = await page.locator('#answers-title').evaluate(el => el.getBoundingClientRect().top);
        assert.ok(headingTop >= 0 && headingTop < 80, 'Pagination must start at the new page of answers');
        assert.equal(await page.evaluate(() => document.activeElement?.id), 'answers-title', 'Pagination focus must move to the new answers');
        await choose(page, 'featured'); await ready(page);
        assert.equal(new URL(page.url()).searchParams.get('page'), null, 'Changing view resets to the first page');
      }

      // Delay an actual server response so a stale result cannot win a later click.
      await page.goto(new URL('?view=all', origin).href); await ready(page);
      await page.evaluate(() => { window.navigationSentinel = document.querySelector('.site-header'); });
      let release;
      let started;
      const gate = new Promise(resolve => { release = resolve; });
      const requestStarted = new Promise(resolve => { started = resolve; });
      const delayed = async route => {
        const response = await route.fetch();
        started();
        await gate;
        await route.fulfill({ response }).catch(() => {});
      };
      await page.route('**/*view=featured*', delayed);
      await choose(page, 'featured');
      await requestStarted;
      assert.ok(await staysOnPage(), 'Pending requests must keep the current content visible');
      await choose(page, 'all'); await ready(page);
      release();
      await page.unroute('**/*view=featured*', delayed);
      assert.ok((await selected(page)).includes('所有回答'), 'The most recent selection wins');
      assert.equal(new URL(page.url()).searchParams.get('view'), 'all');

      const oldUrl = page.url();
      const oldCount = await page.locator('.result-count').innerText();
      const fail = route => route.abort('failed');
      await page.route('**/*view=featured*', fail);
      await choose(page, 'featured'); await ready(page);
      assert.equal(page.url(), oldUrl, 'Failed switching must keep the URL matched to the displayed content');
      assert.equal(await page.locator('.result-count').innerText(), oldCount);
      assert.ok(await page.locator('[data-browse-retry]').isVisible());
      await page.unroute('**/*view=featured*', fail);
      await page.locator('[data-browse-retry]').click(); await ready(page);
      assert.ok((await selected(page)).includes('精选回答'));
      assert.ok(await staysOnPage(), 'Retry must not reload the page');
      assert.equal(new URL(page.url()).searchParams.get('per_page'), mobile ? '7' : '14');

      // A failed history request must leave the URL describing the still-visible view.
      const featuredUrl = page.url();
      await page.route('**/*view=all*', fail);
      await page.evaluate(() => history.back());
      await page.locator('[data-browse-retry]').waitFor({ state: 'visible' });
      await ready(page);
      assert.equal(page.url(), featuredUrl);
      assert.ok((await selected(page)).includes('精选回答'));
      await page.unroute('**/*view=all*', fail);
      await page.locator('[data-browse-retry]').click(); await ready(page);
      assert.ok((await selected(page)).includes('所有回答'));

      if (!mobile) {
        let releaseFirst, firstStarted;
        const firstGate = new Promise(resolve => { releaseFirst = resolve; });
        const firstRequest = new Promise(resolve => { firstStarted = resolve; });
        let first = true;
        const resizeDelay = async route => {
          const response = await route.fetch();
          if (first) {
            first = false; firstStarted(); await firstGate;
          }
          await route.fulfill({ response }).catch(() => {});
        };
        await page.route('**/*view=featured*', resizeDelay);
        await choose(page, 'featured'); await firstRequest;
        await page.setViewportSize({ width: 390, height: 844 });
        await page.waitForFunction(() => document.querySelector('[data-answer-wall]').dataset.perPage === '7');
        await ready(page); releaseFirst();
        await page.unroute('**/*view=featured*', resizeDelay);
        assert.ok((await selected(page)).includes('精选回答'), 'Resizing during a request must keep the new view');
        assert.equal(await page.locator('.category-card').count(), 0);
        assert.equal(await page.locator('.answer-card').count(), 7);
        assert.equal(new URL(page.url()).searchParams.get('view'), 'featured');
        assert.ok(await staysOnPage());

        await page.setViewportSize({ width: 1280, height: 900 });
        await page.goto(new URL('?view=all&page=2&per_page=14', origin).href); await ready(page);
        const firstAnswer = await page.locator('.answer-card h3 a').first().getAttribute('href');
        await choose(page, 'featured'); await ready(page);
        await page.setViewportSize({ width: 390, height: 844 });
        await page.waitForFunction(() => document.querySelector('[data-answer-wall]').dataset.perPage === '7');
        await ready(page);
        await page.evaluate(() => history.back());
        await page.waitForFunction(() => document.querySelector('.view-tabs [aria-current]')?.textContent.includes('所有回答'));
        await ready(page);
        assert.equal(new URL(page.url()).searchParams.get('page'), '3', 'Desktop page 2 must restore as mobile page 3');
        assert.equal(await page.locator('.answer-card h3 a').first().getAttribute('href'), firstAnswer, 'History across breakpoints must retain the first answer');
      }
      console.log(`PASS seamless view/filter/pagination, history, race and retry: ${mobile ? 'mobile' : 'desktop'}`);
      await context.close();
    }
    assert.deepEqual(errors, [], 'No browser script errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
