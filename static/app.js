(() => {
  const originalTitle = document.title;
  let titleTime;
  document.addEventListener('visibilitychange', () => {
    clearTimeout(titleTime);
    if (document.hidden) {
      document.title = 'Σ(°Д°; 你怎么不见啦！';
    } else {
      document.title = '(*´∀`)ﾉﾟ回来啦！' + originalTitle;
      titleTime = setTimeout(() => {
        document.title = originalTitle;
      }, 2000);
    }
  });

  const themeOptions = document.querySelectorAll('[data-theme-option]');
  if (themeOptions.length) {
    const updateThemeOptions = theme => {
      themeOptions.forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.themeOption === theme));
      });
    };
    updateThemeOptions(document.documentElement.dataset.themePreference || 'system');
    themeOptions.forEach(button => button.addEventListener('click', () => {
      const theme = button.dataset.themeOption;
      if (theme === 'system') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.themePreference = theme;
      updateThemeOptions(theme);
      try { localStorage.setItem('qb-theme', theme); } catch (_) {}
    }));
  }
  document.querySelectorAll('[data-counter]').forEach(input => {
    const count = document.getElementById(input.dataset.counter);
    const update = () => {
      const length = Array.from(input.value).length;
      const maximum = Number(input.dataset.maxLength);
      count.textContent = `${length} / ${maximum}`;
      input.setCustomValidity(length > maximum ? `提问最多 ${maximum} 字。` : '');
    };
    input.addEventListener('input', update);
    update();
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  const readerTag = document.querySelector('[data-reader-tag]');
  if (readerTag) readerTag.addEventListener('change', () => readerTag.form.requestSubmit());

  const answerWall = document.querySelector('[data-answer-wall]');
  if (answerWall) {
    const mobile = window.matchMedia('(max-width: 640px)');
    const homePath = new URL(answerWall.dataset.paginationUrl, location.origin).pathname;
    let displayedUrl = new URL(location.href);
    let pendingRequest;
    let failedRequest;

    const loadAnswers = async (target, mode = 'push', startAtHeading = false) => {
      const url = new URL(target, location.href);
      const previousSize = url.searchParams.get('per_page') === '7' ? 7 : 14;
      const perPage = mobile.matches ? 7 : 14;
      const page = Number(url.searchParams.get('page'));
      // History entries can come from a different screen size. Preserve their first answer.
      if (previousSize !== perPage && Number.isInteger(page) && page > 0) {
        url.searchParams.set('page', Math.floor((page - 1) * previousSize / perPage) + 1);
      }
      url.searchParams.set('per_page', perPage);
      pendingRequest?.controller.abort();
      const request = { controller: new AbortController(), url, mode, startAtHeading };
      pendingRequest = request;
      failedRequest = null;
      answerWall.setAttribute('aria-busy', 'true');
      answerWall.querySelector('[data-browse-feedback]').hidden = true;
      answerWall.querySelector('[data-browse-status]').textContent = '正在更新问答。';
      // A stalled request should offer the same retry action as a network failure.
      const timeout = setTimeout(() => request.controller.abort(), 15000);
      try {
        const response = await fetch(url, { signal: request.controller.signal });
        if (!response.ok) throw new Error('Unable to load answers');
        const html = new DOMParser().parseFromString(await response.text(), 'text/html');
        const nextWall = html.querySelector('[data-answer-wall]');
        if (!nextWall) throw new Error('Missing answers');
        // Check identity as well as the signal, including after parsing the response.
        if (pendingRequest !== request || request.controller.signal.aborted) return;
        const active = document.activeElement;
        const focusedHref = answerWall.contains(active) ? active.closest('a')?.href : null;
        const retryFocused = active?.matches('[data-browse-retry]');
        if (mode === 'push' && url.href !== location.href) history.pushState(null, '', url);
        else if (mode === 'replace' || url.href !== location.href) history.replaceState(null, '', url);
        answerWall.replaceChildren(...nextWall.childNodes);
        answerWall.dataset.page = nextWall.dataset.page;
        answerWall.dataset.perPage = nextWall.dataset.perPage;
        displayedUrl = new URL(url);
        if (startAtHeading) {
          const heading = answerWall.querySelector('#answers-title');
          heading.focus({ preventScroll: true });
          heading.scrollIntoView({ block: 'start', behavior: 'instant' });
        } else if (focusedHref || retryFocused) {
          const links = [...answerWall.querySelectorAll('a')];
          const focus = links.find(link => link.href === focusedHref)
            || answerWall.querySelector('.view-tabs [aria-current]');
          focus?.focus({ preventScroll: true });
        }
        answerWall.querySelector('[data-browse-status]').textContent =
          `已更新，${answerWall.querySelector('.result-count').textContent.trim()}。`;
      } catch (_) {
        if (pendingRequest !== request) return;
        // A failed Back/Forward request must not leave the URL describing other content.
        if (mode === 'pop') history.replaceState(null, '', displayedUrl);
        failedRequest = { url, mode: mode === 'pop' ? 'replace' : mode, startAtHeading };
        answerWall.querySelector('[data-browse-feedback]').hidden = false;
        answerWall.querySelector('[data-browse-status]').textContent = '暂时没能加载，当前问答仍然保留。请重试。';
      } finally {
        clearTimeout(timeout);
        if (pendingRequest === request) {
          pendingRequest = null;
          answerWall.removeAttribute('aria-busy');
        }
      }
    };

    answerWall.addEventListener('click', event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      if (event.target.closest('[data-browse-retry]')) {
        if (failedRequest) loadAnswers(failedRequest.url, failedRequest.mode, failedRequest.startAtHeading);
        return;
      }
      const link = event.target.closest('a[href]');
      if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
      const url = new URL(link.href);
      if (url.origin !== location.origin || url.pathname !== homePath) return;
      event.preventDefault();
      if (!pendingRequest && link.hasAttribute('aria-current')) return;
      loadAnswers(url, 'push', !!link.closest('.pagination'));
    });

    window.addEventListener('popstate', () => {
      const url = new URL(location.href);
      if (url.pathname !== homePath) return;
      // An ordinary same-page anchor change needs no network request.
      if (!pendingRequest && url.search === displayedUrl.search) {
        displayedUrl = url;
        return;
      }
      loadAnswers(url, 'pop');
    });

    const updatePageSize = () => {
      const perPage = mobile.matches ? 7 : 14;
      // Resizing during a navigation keeps that requested view, rather than the old one.
      const url = new URL(pendingRequest?.url || location.href);
      const currentSize = pendingRequest ? Number(url.searchParams.get('per_page')) : Number(answerWall.dataset.perPage);
      if (perPage === currentSize) return;
      const page = pendingRequest ? Number(url.searchParams.get('page') || 1) : Number(answerWall.dataset.page);
      const firstIndex = (page - 1) * currentSize;
      url.searchParams.set('per_page', perPage);
      url.searchParams.set('page', Math.floor(firstIndex / perPage) + 1);
      loadAnswers(url, pendingRequest?.mode || 'replace', pendingRequest?.startAtHeading || false);
    };
    mobile.addEventListener('change', updatePageSize);
    updatePageSize();
  }
})();
