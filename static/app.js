(() => {
  const select = document.getElementById('theme-select');
  if (select) {
    select.value = document.documentElement.dataset.themePreference || 'system';
    select.addEventListener('change', () => {
      const theme = select.value;
      if (theme === 'system') delete document.documentElement.dataset.theme;
      else document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.themePreference = theme;
      try { localStorage.setItem('qb-theme', theme); } catch (_) {}
    });
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

  let answerWall = document.querySelector('[data-answer-wall]');
  if (answerWall) {
    // Match the breakpoint where the answer grid changes from two columns to one.
    const mobile = window.matchMedia('(max-width: 640px)');
    let pendingRequest;
    const updatePageSize = async () => {
      if (pendingRequest) pendingRequest.abort();
      const perPage = mobile.matches ? 7 : 14;
      const currentSize = Number(answerWall.dataset.perPage);
      if (perPage === currentSize) {
        answerWall.removeAttribute('aria-busy');
        return;
      }
      // Keep the first visible question when changing the number of cards per page.
      const firstIndex = (Number(answerWall.dataset.page) - 1) * currentSize;
      const url = new URL(answerWall.dataset.paginationUrl, window.location.origin);
      url.search = window.location.search;
      url.searchParams.set('per_page', perPage);
      url.searchParams.set('page', Math.floor(firstIndex / perPage) + 1);
      url.hash = window.location.hash;
      const controller = new AbortController();
      pendingRequest = controller;
      answerWall.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(url, { signal: controller.signal });
        if (!response.ok) return;
        const html = new DOMParser().parseFromString(await response.text(), 'text/html');
        const nextWall = html.querySelector('[data-answer-wall]');
        if (!nextWall || controller.signal.aborted) return;
        // Replace only the answers so resizing never clears a question being written.
        answerWall.replaceWith(nextWall);
        answerWall = nextWall;
        if (window.location.pathname === url.pathname) window.history.replaceState(null, '', url);
      } catch (_) {
        // Existing server-rendered answers and pagination remain usable on failure.
      } finally {
        if (pendingRequest === controller) {
          answerWall.removeAttribute('aria-busy');
          pendingRequest = null;
        }
      }
    };
    mobile.addEventListener('change', updatePageSize);
    updatePageSize();
  }
})();
