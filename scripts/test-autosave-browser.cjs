// Run against a disposable local database with a test owner and question.
// Set AUTOSAVE_TEST_URL, AUTOSAVE_TEST_USER and AUTOSAVE_TEST_PASSWORD.
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const url = process.env.AUTOSAVE_TEST_URL;
if (!url || !['127.0.0.1', 'localhost'].includes(new URL(url).hostname)) {
  throw new Error('AUTOSAVE_TEST_URL must point at a disposable local question.');
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const input = page.locator('[name="answer"]');
  const saved = () => page.waitForFunction(() =>
    document.querySelector('[data-autosave-status]').textContent === '草稿已自动保存');
  const isAutosave = request => request.method() === 'POST' &&
    new URLSearchParams(request.postData()).get('action') === 'autosave';
  try {
    await page.goto(new URL('/owner/login/', url).href);
    await page.locator('[name="username"]').fill(process.env.AUTOSAVE_TEST_USER);
    await page.locator('[name="password"]').fill(process.env.AUTOSAVE_TEST_PASSWORD);
    await Promise.all([page.waitForURL('**/owner/'), page.locator('button[type="submit"]').click()]);
    await page.goto(url);

    await input.fill('第一段\n继续认真写回信');
    await saved();
    await page.reload();
    assert.equal(await input.inputValue(), '第一段\n继续认真写回信');
    console.log('PASS: autosave and reload');

    // Hold an actual server response while the author keeps typing.
    let release;
    const held = new Promise(resolve => { release = resolve; });
    await page.route(url, async route => {
      if (!isAutosave(route.request())) return route.continue();
      const response = await route.fetch();
      await held;
      await route.fulfill({ response });
    });
    const requestStarted = page.waitForRequest(isAutosave);
    await input.fill('保存中的第一版');
    await requestStarted;
    await input.fill('保存过程中继续输入的新版本');
    release();
    await saved();
    await page.unroute(url);
    await page.reload();
    assert.equal(await input.inputValue(), '保存过程中继续输入的新版本');
    console.log('PASS: typing during an in-flight save retains newest text');

    let dropResponse = true;
    await page.route(url, async route => {
      if (!isAutosave(route.request()) || !dropResponse) return route.continue();
      dropResponse = false;
      await route.fetch();
      await route.abort('failed');
    });
    await input.fill('已保存但响应丢失');
    await page.waitForFunction(() => document.querySelector('[data-autosave-status]').textContent.includes('失败'));
    await input.fill('响应丢失后继续编辑的新内容');
    await saved();
    await page.unroute(url);
    await page.reload();
    assert.equal(await input.inputValue(), '响应丢失后继续编辑的新内容');
    console.log('PASS: lost save response retries safely and saves newer edits');

    await context.setOffline(true);
    await input.fill('断网时也继续写');
    await page.waitForFunction(() => document.querySelector('[data-autosave-status]').textContent.includes('失败'));
    assert.equal(await input.inputValue(), '断网时也继续写');
    let warned = false;
    page.once('dialog', async dialog => { warned = dialog.type() === 'beforeunload'; await dialog.dismiss(); });
    await page.reload({ timeout: 3000 }).catch(() => {});
    assert.equal(warned, true);
    await context.setOffline(false);
    await saved();
    await page.reload();
    assert.equal(await input.inputValue(), '断网时也继续写');
    console.log('PASS: offline retry and unsaved navigation warning');

    const stale = await context.newPage();
    await stale.goto(url);
    await input.fill('另一个页面不能覆盖这份草稿');
    await saved();
    await stale.locator('[name="answer"]').fill('过期页面的文本');
    await stale.waitForFunction(() => document.querySelector('[data-autosave-status]').textContent.includes('其他页面'));
    assert.equal(await stale.locator('[name="answer"]').inputValue(), '过期页面的文本');
    await stale.close();
    await page.reload();
    assert.equal(await input.inputValue(), '另一个页面不能覆盖这份草稿');
    console.log('PASS: stale page conflict preserves both copies');

    // A publish click must wait for the outstanding autosave and use its revision.
    let releasePublish;
    const publishHeld = new Promise(resolve => { releasePublish = resolve; });
    await page.route(url, async route => {
      if (!isAutosave(route.request())) return route.continue();
      const response = await route.fetch();
      await publishHeld;
      await route.fulfill({ response });
    });
    const publishingSave = page.waitForRequest(isAutosave);
    await input.fill('浏览器验证正式回信');
    await publishingSave;
    await page.locator('button[value="publish"]').click();
    assert.equal(await input.getAttribute('readonly'), '');
    const published = page.waitForNavigation();
    releasePublish();
    await published;
    await page.unroute(url);
    assert.equal(await input.inputValue(), '浏览器验证正式回信');
    assert.match(await page.locator('.messages').textContent(), /回答已发布/);
    assert.equal(await page.locator('[name="expected_draft"]').inputValue(), '');
    console.log('PASS: publish waits for autosave and clears draft');

    await input.fill('');
    await saved();
    await page.reload();
    assert.equal(await input.inputValue(), '');
    const publicPage = await context.newPage();
    await publicPage.goto(await page.locator('.original-note .text-link').getAttribute('href')
      .then(path => new URL(path, url).href));
    assert.match(await publicPage.locator('.full-reply').textContent(), /浏览器验证正式回信/);
    await publicPage.close();
    console.log('PASS: empty draft persists without changing public answer');

    // Expired credentials during a publish click must keep the unsaved text visible.
    await page.route(url, route => isAutosave(route.request())
      ? route.fulfill({ status: 403, contentType: 'text/html', body: 'Session expired' })
      : route.continue());
    await input.fill('登录过期后不能丢失的文字');
    await page.locator('button[value="publish"]').click();
    await page.waitForFunction(() => document.querySelector('[data-autosave-status]')?.textContent.includes('登录已失效'));
    await pause(300);
    assert.equal(await input.inputValue(), '登录过期后不能丢失的文字');
    assert.match(await page.locator('[data-autosave-status]').textContent(), /登录已失效/);
    await page.locator('button[value="publish"]').click();
    await pause(300);
    assert.match(await page.locator('[data-autosave-status]').textContent(), /登录已失效/);
    console.log('PASS: expired login blocks publication and retains unsaved text');

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('PASS: mobile layout and no browser script errors');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
