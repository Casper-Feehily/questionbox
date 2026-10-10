// Requires Playwright and Chrome. Point READER_TEST_URL at a reader with at least eighteen letters.
// Example: READER_TEST_URL=http://127.0.0.1:8125/read/ node scripts/test-reader-browser.cjs
const assert = require('node:assert/strict');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require('playwright');
const url = process.env.READER_TEST_URL || 'http://127.0.0.1:8125/read/';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function ready(page) {
  await page.waitForFunction(() => document.querySelector('[data-deck]')?.getAttribute('aria-busy') === 'false');
}

async function cardState(page, id) {
  return page.evaluate(id => {
    const deck = document.querySelector('[data-deck]').getBoundingClientRect();
    const nodes = [...document.querySelectorAll('.reader-card')].filter(card => card.dataset.id === id);
    const states = nodes.map(card => {
      const style = getComputedStyle(card), rect = card.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, opacity: Number(style.opacity),
        color: style.backgroundColor, visible: style.visibility === 'visible' && Number(style.opacity) > 0,
        overlap: Math.max(0, Math.min(rect.right, deck.right) - Math.max(rect.left, deck.left)),
        active: card.dataset.slot === '0' };
    });
    return states.find(state => state.visible && state.overlap > 1) || states[0] || null;
  }, id);
}

async function position(page) { return page.locator('[data-position]').textContent(); }
async function activeId(page) { return page.locator('[data-slot="0"]').getAttribute('data-id'); }
async function clickNext(page) { await page.locator('[data-next]').click(); await ready(page); }

async function pointer(page, mobile) {
  const box = await page.locator('[data-slot="0"] .reader-card-top').boundingBox();
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  const session = mobile ? await page.context().newCDPSession(page) : null;
  if (mobile) await session.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y }] });
  else { await page.mouse.move(x, y); await page.mouse.down(); }
  let last = 0;
  return {
    async move(dx) {
      const from = last;
      for (let step = 1; step <= 5; step++) {
        const nextX = x + from + (dx - from) * step / 5;
        if (mobile) await session.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: nextX, y }] });
        else await page.mouse.move(nextX, y);
        await pause(20);
      }
      last = dx;
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    },
    async end(cancel = false) {
      if (mobile) {
        await session.send('Input.dispatchTouchEvent', { type: cancel ? 'touchCancel' : 'touchEnd', touchPoints: [] });
        await session.detach();
      } else await page.mouse.up();
    },
  };
}

async function recordReturn(page, id) {
  await page.evaluate(id => {
    window.readerReturnFrames = [];
    const start = performance.now();
    const record = () => {
      const card = [...document.querySelectorAll('.reader-card')].find(card => card.dataset.id === id);
      const style = card && getComputedStyle(card);
      readerReturnFrames.push({ time: performance.now() - start, present: !!card,
        opacity: style && Number(style.opacity), overflow: document.documentElement.scrollWidth - innerWidth });
      if (performance.now() - start < 650) requestAnimationFrame(record);
    };
    requestAnimationFrame(record);
  }, id);
}

async function checkReturnFrames(page) {
  const frames = await page.evaluate(() => readerReturnFrames);
  assert.ok(frames.some(frame => frame.present), 'Previous letter must enter the rendered animation');
  assert.ok(frames.filter(frame => frame.present).every(frame => frame.opacity >= .99), 'Previous letter must slide back at full opacity rather than fade into existence');
  assert.ok(frames.every(frame => frame.overflow === 0), 'Returning letters must not widen the page');
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const errors = [], results = [];
  try {
    for (const mobile of [false, true]) for (const theme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 900 }, isMobile: mobile, hasTouch: mobile, colorScheme: theme });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      assert.equal((await page.goto(url)).status(), 200);
      await ready(page);
      await page.selectOption('#theme-select', theme);
      const count = await page.evaluate(() => JSON.parse(document.getElementById('reader-data').textContent).order.length);
      assert.ok(count >= 18, 'Provide at least eighteen public letters to exercise cache eviction before the next round');
      const firstId = await activeId(page);
      await clickNext(page);
      const previousId = await activeId(page);
      const previousColor = (await cardState(page, previousId)).color;
      await clickNext(page);
      const currentId = await activeId(page), currentStart = await cardState(page, currentId);

      const drag = await pointer(page, mobile);
      await drag.move(40);
      const early = await cardState(page, previousId);
      assert.ok(early?.visible && early.overlap > 1, 'Previous letter must already follow a right drag before release');
      await drag.move(110);
      const later = await cardState(page, previousId), currentDragged = await cardState(page, currentId);
      assert.ok(later.x > early.x + 50, 'Previous letter must move continuously with the finger');
      assert.equal(later.opacity, 1);
      assert.ok(Math.abs(currentDragged.x - currentStart.x) < 20, 'Current letter should sink into the stack instead of sliding away');
      await page.screenshot({ path: path.join(os.tmpdir(), `questionbox-return-drag-${mobile ? 'mobile' : 'desktop'}-${theme}.png`) });
      await recordReturn(page, previousId);
      await drag.end();
      await ready(page);
      assert.equal(await position(page), '02');
      assert.equal(await activeId(page), previousId);
      assert.equal((await cardState(page, previousId)).color, previousColor);
      await checkReturnFrames(page);

      const short = await pointer(page, mobile);
      await short.move(20);
      assert.ok((await cardState(page, firstId))?.overlap > 1, 'Even a short right drag should reveal the previous letter');
      await short.end();
      await page.waitForFunction(id => {
        const deck = document.querySelector('[data-deck]').getBoundingClientRect();
        const card = [...document.querySelectorAll('.reader-card')].find(card => card.dataset.id === id);
        if (!card) return true;
        const style = getComputedStyle(card), rect = card.getBoundingClientRect();
        return style.visibility === 'hidden' || Number(style.opacity) === 0 || rect.right <= deck.left + 1;
      }, firstId);
      assert.equal(await position(page), '02', 'An incomplete drag must return to the current letter');
      assert.equal(await activeId(page), previousId);
      assert.ok(Math.abs((await cardState(page, previousId)).x - currentStart.x) < 1);

      await recordReturn(page, firstId);
      await page.locator('[data-previous]').click();
      await ready(page);
      await checkReturnFrames(page);
      assert.equal(await activeId(page), firstId);
      assert.equal(await page.locator('[data-previous]').isDisabled(), true);

      // Walk beyond the cache window, then return repeatedly without losing the preview.
      await page.emulateMedia({ reducedMotion: 'reduce' });
      for (let step = 0; step < 12; step++) await clickNext(page);
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      let releaseCold, coldRequested, coldId;
      if (!mobile && theme === 'light') {
        coldId = await page.evaluate(() => JSON.parse(document.getElementById('reader-data').textContent).order[5]);
        let requested;
        coldRequested = new Promise(resolve => { requested = resolve; });
        const hold = new Promise(resolve => { releaseCold = resolve; });
        await page.route('**/read/cards/**', async route => {
          if (new URL(route.request().url()).searchParams.get('ids').split(',').includes(coldId)) {
            requested();
            await hold;
          }
          await route.continue();
        });
      }
      try { for (let step = 0; step < 8; step++) {
        const before = Number(await position(page));
        if (coldRequested && step === 6) await Promise.race([
          coldRequested,
          pause(5000).then(() => { throw new Error('Expected a prefetch for the evicted previous letter'); }),
        ]);
        const back = await pointer(page, mobile);
        await back.move(110);
        if (coldRequested && step === 6) {
          const received = page.waitForResponse(response => response.url().includes('/read/cards/') && new URL(response.url()).searchParams.get('ids').split(',').includes(coldId));
          releaseCold();
          await received;
          await page.waitForFunction(id => document.querySelector('[data-return]')?.dataset.id === id, coldId);
          // Do not move the pointer again: the arriving prefetch must resume the held drag itself.
        }
        await page.waitForFunction(() => {
          const deck = document.querySelector('[data-deck]').getBoundingClientRect();
          const card = document.querySelector('[data-return]');
          if (!card) return false;
          const style = getComputedStyle(card);
          return style.visibility === 'visible' && Number(style.opacity) >= .99 && card.getBoundingClientRect().right > deck.left + 1;
        }, null, { timeout: 13000 });
        const visible = await page.evaluate(() => {
          const deck = document.querySelector('[data-deck]').getBoundingClientRect();
          return [...document.querySelectorAll('.reader-card')].filter(card => {
            const style = getComputedStyle(card), rect = card.getBoundingClientRect();
            return Number(style.zIndex) > 4 && style.visibility === 'visible' && Number(style.opacity) >= .99 && rect.right > deck.left + 1;
          }).length;
        });
        assert.ok(visible >= 1, 'Continuous return must prepare the previous letter after cache eviction');
        await back.end();
        await ready(page);
        assert.equal(Number(await position(page)), before - 1);
      } } finally {
        if (releaseCold) { releaseCold(); await page.unrouteAll({ behavior: 'ignoreErrors' }); }
      }
      await page.screenshot({ path: path.join(os.tmpdir(), `questionbox-return-${mobile ? 'mobile' : 'desktop'}-${theme}.png`) });
      results.push({ device: mobile ? 'mobile' : 'desktop', theme, passed: 'follow pointer, current sinks, no fade, snapback, buttons, continuous return after cache eviction' });
      await context.close();
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ url, results, pageErrors: errors }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
