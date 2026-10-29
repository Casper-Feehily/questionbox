(() => {
  const form = document.querySelector('form[data-autosave]');
  if (!form) return;

  const answer = form.elements.answer;
  const revision = form.elements.expected_draft;
  const status = form.querySelector('[data-autosave-status]');
  // A rejected form contains submitted text, which may not have reached storage.
  let saved = form.dataset.autosaveDirty === 'true' ? null : answer.value;
  let timer;
  let pending = null;
  let retryRequest = null;
  let stopped = false;
  let submitting = false;
  let composing = false;

  const show = (message, failed = false) => {
    status.textContent = message;
    status.classList.toggle('field-error', failed);
  };
  const schedule = (delay = 1000) => {
    clearTimeout(timer);
    if (!stopped && !submitting && !composing && (answer.value !== saved || retryRequest)) {
      timer = setTimeout(save, delay);
    }
  };

  function save() {
    clearTimeout(timer);
    if (pending || stopped || submitting || composing || (answer.value === saved && !retryRequest)) return pending;
    const value = retryRequest ? retryRequest.value : answer.value;
    const body = retryRequest ? retryRequest.body : new URLSearchParams(new FormData(form));
    if (!retryRequest) {
      body.set('action', 'autosave');
      body.set('save_id', crypto.randomUUID());
      retryRequest = { value, body };
    }
    show('正在保存草稿…');
    pending = (async () => {
      let retryDelay = 1000;
      let retry = true;
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 15000);
      try {
        // The submit buttons named "action" shadow the form.action property.
        const response = await fetch(form.getAttribute('action') || window.location.href, {
          method: 'POST', body, credentials: 'same-origin',
          headers: { Accept: 'application/json' }, signal: controller.signal,
        });
        if (response.redirected || response.status === 403) {
          stopped = true;
          show('登录已失效，草稿未确认保存。请先复制内容，再重新登录。', true);
          return;
        }
        if (response.status === 404) {
          stopped = true;
          show('问题已不存在。请复制当前内容妥善保存。', true);
          return;
        }
        const data = await response.json();
        if (response.status === 409) {
          stopped = true;
          show(data.error, true);
          return;
        }
        if (response.status === 400) {
          retryRequest = null;
          retry = answer.value !== value;
          show(`草稿未保存：${data.error}`, true);
          return;
        }
        if (!response.ok || !data.draft_revision) throw new Error('Save failed');
        revision.value = data.draft_revision;
        retryRequest = null;
        saved = value;
        show(answer.value === saved ? '草稿已自动保存' : '有新修改，等待保存…');
      } catch (_) {
        retryDelay = 5000;
        show('自动保存失败，将重试。离开前请确认草稿已保存。', true);
      } finally {
        clearTimeout(timeout);
        pending = null;
        // Invalid content needs an edit before retrying; conflicts require a reload.
        if (retry) schedule(retryDelay);
      }
    })();
    return pending;
  }

  answer.addEventListener('input', () => {
    if (stopped) return;
    show(answer.value === saved && !pending ? '草稿没有未保存的修改' : '有新修改，等待保存…');
    schedule();
  });
  answer.addEventListener('compositionstart', () => {
    composing = true;
    clearTimeout(timer);
  });
  answer.addEventListener('compositionend', () => {
    composing = false;
    schedule();
  });
  answer.addEventListener('blur', save);
  window.addEventListener('online', () => schedule(0));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) save();
  });
  window.addEventListener('beforeunload', event => {
    if (!submitting && (answer.value !== saved || pending || retryRequest)) {
      event.preventDefault();
      event.returnValue = '';
    }
  });

  form.addEventListener('submit', async event => {
    // Finish the current save before publishing, so it cannot arrive after publication.
    if (submitting || stopped) {
      event.preventDefault();
      return;
    }
    clearTimeout(timer);
    if (pending) {
      event.preventDefault();
      submitting = true;
      answer.readOnly = true;
      const buttons = [...form.querySelectorAll('button[type="submit"]')];
      buttons.forEach(button => { button.disabled = true; });
      await pending;
      answer.readOnly = false;
      buttons.forEach(button => { button.disabled = false; });
      submitting = false;
      form.requestSubmit(event.submitter);
      return;
    }
    submitting = true;
  });
  if (saved === null) {
    show('有未保存的修改，等待保存…');
    schedule();
  }
})();
