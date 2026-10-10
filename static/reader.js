(() => {
  const root = document.querySelector('[data-reader]');
  const payload = document.getElementById('reader-data');
  if (!root || !payload) return;

  const data = JSON.parse(payload.textContent);
  let pool = [...data.order];
  let sequence = [...pool];
  let index = 0;
  let busy = false;
  let gesture = null;
  let dragFrame = 0;
  let returnCard = null;
  let returnRequest = null;
  let returnVersion = 0;
  let wheelTime = 0;
  let wheelDistance = 0;
  let wheelMoved = false;
  const cache = new Map(data.cards.map(card => [card.id, card]));
  // A letter keeps its color when promoted, revisited, or recreated after cache eviction.
  const tones = ['lavender', 'green', 'blue'];
  const cardTones = new Map(data.order.map((id, position) => [id, tones[position % tones.length]]));
  const pending = new Map();
  const deck = root.querySelector('[data-deck]');
  const previous = root.querySelector('[data-previous]');
  const next = root.querySelector('[data-next]');
  const shuffleButton = root.querySelector('[data-shuffle]');
  const status = root.querySelector('[data-status]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const scrollPositions = new Map();

  function shuffled(ids, last) {
    const result = [...ids];
    for (let i = result.length - 1; i > 0; i--) {
      const random = crypto.getRandomValues(new Uint32Array(1))[0] / 4294967296;
      const j = Math.floor(random * (i + 1));
      [result[i], result[j]] = [result[j], result[i]];
    }
    // Avoid seeing the same letter twice across a round boundary.
    if (result.length > 1 && result[0] === last) [result[0], result[1]] = [result[1], result[0]];
    return result;
  }

  function extend(to) {
    if (pool.length < 2) return;
    while (sequence.length <= to) sequence.push(...shuffled(pool, sequence.at(-1)));
  }

  function wanted(at = index) {
    extend(at + 2);
    return [...new Set(sequence.slice(at, at + Math.min(3, pool.length)))];
  }

  function controls() {
    previous.disabled = busy || index === 0;
    next.disabled = busy || pool.length < 2;
    if (shuffleButton) shuffleButton.disabled = busy || pool.length < 2;
    root.querySelector('[data-position]').textContent = String(pool.length ? index % pool.length + 1 : 0).padStart(2, '0');
    root.querySelector('[data-total]').textContent = String(pool.length).padStart(2, '0');
    deck.setAttribute('aria-busy', String(busy));
  }

  function unavailable(ids) {
    if (!ids.length) return;
    const missing = new Set(ids);
    const removedBefore = sequence.slice(0, index).filter(id => missing.has(id)).length;
    pool = pool.filter(id => !missing.has(id));
    sequence = sequence.filter(id => !missing.has(id));
    index = Math.max(0, Math.min(index - removedBefore, sequence.length - 1));
    ids.forEach(id => { cache.delete(id); scrollPositions.delete(id); });
  }

  async function load(ids) {
    const unknown = [...new Set(ids)].filter(id => !cache.has(id) && !pending.has(id));
    if (unknown.length) {
      const request = (async () => {
        const url = new URL(root.dataset.feedUrl, window.location.origin);
        url.searchParams.set('ids', unknown.join(','));
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 12000);
        try {
          const response = await fetch(url, { signal: controller.signal, headers: { Accept: 'application/json' } });
          if (!response.ok) throw new Error('Letter request failed');
          const result = await response.json();
          if (!Array.isArray(result.cards)) throw new Error('Invalid letters');
          for (const card of result.cards) {
            if (unknown.includes(card.id)) cache.set(card.id, card);
          }
          unavailable(unknown.filter(id => !cache.has(id)));
        } finally {
          clearTimeout(timeout);
          unknown.forEach(id => pending.delete(id));
        }
      })();
      unknown.forEach(id => pending.set(id, request));
    }
    await Promise.all([...new Set(ids.map(id => pending.get(id)).filter(Boolean))]);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function makeCard(card) {
    const article = element('article', 'reader-card');
    article.dataset.id = card.id;
    article.dataset.tone = cardTones.get(card.id);
    const top = element('div', 'reader-card-top');
    const stamp = element('span', 'reader-card-stamp', '一封回信 ');
    const arrow = element('span', '', '↗');
    arrow.setAttribute('aria-hidden', 'true');
    stamp.append(arrow);
    top.append(element('span', '', `来自 ${card.display_name}`), stamp);
    const scroll = element('div', 'reader-scroll');
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', '问题与完整回答');
    const reply = element('div', 'reader-reply');
    reply.append(element('span', 'reply-label', `${root.dataset.owner} 的回信`), element('div', 'reader-prose', card.answer));
    scroll.append(element('h2', 'reader-question', card.text), reply);
    const bottom = element('div', 'reader-card-bottom');
    const link = element('a', '', '单独打开 ↗');
    link.href = card.url;
    bottom.append(element('span', '', `${card.date}${card.updated ? ' · 已更新' : ''}`), link);
    const peek = element('div', 'reader-peek', `下一封 · ${card.text}`);
    peek.setAttribute('aria-hidden', 'true');
    article.append(top, scroll, bottom, peek);
    return article;
  }

  function slot(card, position) {
    card.dataset.slot = position;
    card.inert = position !== 0;
    if (position === 0) card.removeAttribute('aria-hidden');
    else card.setAttribute('aria-hidden', 'true');
  }

  function trimCache() {
    // Keep nearby letters, rather than every full answer in a long reading session.
    const keep = new Set(sequence.slice(Math.max(0, index - 6), index + 9));
    for (const id of cache.keys()) if (!keep.has(id)) cache.delete(id);
  }

  function render() {
    clearReturn(true);
    const ids = wanted();
    const existing = new Map([...deck.children].map(node => [node.dataset.id, node]));
    ids.forEach((id, position) => {
      let card = existing.get(id);
      if (!card && cache.has(id)) { card = makeCard(cache.get(id)); deck.append(card); }
      if (card) slot(card, position);
    });
    for (const card of [...deck.children]) if (!ids.includes(card.dataset.id)) card.remove();
    const activeScroll = deck.querySelector('[data-slot="0"] .reader-scroll');
    if (activeScroll) activeScroll.scrollTop = scrollPositions.get(sequence[index]) || 0;
    controls();
    if (!pool.length) {
      status.textContent = '这些回信已被移除，可以返回留言墙看看。';
    }
    trimCache();
  }

  async function prefetch() {
    if (pool.length < 2) return;
    extend(index + 5);
    // Include the previous letter so repeated returns stay ready after cache eviction.
    const start = Math.max(0, index - 1);
    const ids = [...new Set(sequence.slice(start, start + 6))];
    try {
      const count = pool.length;
      await load(ids);
      if (count !== pool.length && !busy) render();
    } catch (_) { /* Navigation exposes a retry if needed. */ }
  }

  function releaseGesture() {
    const pointerId = gesture?.pointerId;
    gesture = null;
    if (pointerId !== undefined && deck.hasPointerCapture(pointerId)) deck.releasePointerCapture(pointerId);
  }

  function clearReturn(keepActive = false) {
    returnVersion++;
    returnRequest = null;
    if (returnCard) {
      if (keepActive && returnCard.dataset.slot === '0') returnCard.removeAttribute('data-return');
      else returnCard.remove();
      returnCard = null;
    }
    deck.classList.remove('is-returning');
  }

  function prepareReturn(id = sequence[index - 1]) {
    if (!id) return null;
    if (!cache.has(id)) {
      if (returnRequest?.id !== id) {
        const request = { id };
        returnRequest = request;
        // Also wait for an existing prefetch: a stationary finger needs a new frame when it arrives.
        load([id]).then(() => {
          if (returnRequest !== request) return;
          returnRequest = null;
          if (!busy && gesture?.dx > 0) queueDrag();
        }).catch(() => {
          if (returnRequest === request) returnRequest = null;
          // Releasing the gesture exposes the normal navigation retry.
        });
      }
      return null;
    }
    returnVersion++;
    if (returnCard?.dataset.id !== id) {
      clearReturn();
      returnCard = makeCard(cache.get(id));
      returnCard.setAttribute('data-return', '');
      slot(returnCard, -1);
      deck.append(returnCard);
      returnCard.querySelector('.reader-scroll').scrollTop = scrollPositions.get(id) || 0;
    }
    deck.classList.add('is-returning');
    return returnCard;
  }

  function queueDrag() {
    if (dragFrame) return;
    dragFrame = requestAnimationFrame(() => {
      dragFrame = 0;
      if (!gesture) return;
      const card = deck.querySelector('[data-slot="0"]');
      deck.classList.add('is-dragging');
      if (!card || reducedMotion.matches) return;
      if (gesture.dx > 0) {
        const incoming = prepareReturn();
        if (!incoming) return;
        const width = deck.clientWidth;
        const distance = Math.min(width, gesture.dx);
        const progress = distance / width;
        const style = getComputedStyle(deck);
        const firstStep = parseFloat(style.getPropertyValue('--reader-step-one'));
        const secondStep = parseFloat(style.getPropertyValue('--reader-step-two'));
        incoming.style.transform = `translate3d(${distance - width}px,0,0)`;
        card.style.transform = `translate3d(0,${firstStep * progress}px,0) scale(${1 - .035 * progress}) rotate(${-progress}deg)`;
        const middle = deck.querySelector('[data-slot="1"]');
        if (middle) middle.style.transform = `translate3d(0,${firstStep + (secondStep - firstStep) * progress}px,0) scale(${.965 - .04 * progress}) rotate(${-1 + 2 * progress}deg)`;
        const bottom = deck.querySelector('[data-slot="2"]');
        if (bottom) {
          bottom.style.transform = `translate3d(0,${secondStep + 15 * progress}px,0) scale(${.925 - .035 * progress}) rotate(1deg)`;
          bottom.style.opacity = String(1 - progress);
        }
      } else {
        clearReturn();
        for (const layer of deck.children) if (layer !== card) {
          layer.style.removeProperty('transform');
          layer.style.removeProperty('opacity');
        }
        const distance = Math.max(-190, Math.min(190, gesture.dx * .65));
        card.style.transform = `translate3d(${distance}px,0,0) rotate(${distance * .008}deg)`;
      }
    });
  }

  function settle() {
    cancelAnimationFrame(dragFrame);
    dragFrame = 0;
    deck.classList.remove('is-dragging');
    for (const card of deck.children) { card.style.removeProperty('transform'); card.style.removeProperty('opacity'); }
    releaseGesture();
    if (returnCard) {
      const version = ++returnVersion;
      waitForTransition(returnCard).then(() => {
        if (version === returnVersion && !busy && !gesture) clearReturn();
      });
    }
  }

  function waitForTransition(card) {
    if (reducedMotion.matches || !card) return Promise.resolve();
    return new Promise(resolve => {
      const finish = event => {
        if (event && (event.target !== card || event.propertyName !== 'transform')) return;
        clearTimeout(timeout);
        card.removeEventListener('transitionend', finish);
        resolve();
      };
      const timeout = setTimeout(finish, 560);
      card.addEventListener('transitionend', finish);
    });
  }

  async function move(direction, reshuffle = false) {
    if (busy || (!reshuffle && (pool.length < 2 || direction < 0 && index === 0))) { settle(); return; }
    busy = true;
    controls();
    let target = reshuffle ? 0 : index + direction;
    const currentId = sequence[index];
    const oldSequence = sequence;
    const oldIndex = index;
    if (reshuffle) sequence = shuffled(pool, currentId);
    extend(target + 2);
    const targetId = sequence[target];
    const outgoing = deck.querySelector('[data-slot="0"]');
    const focusWasInCard = outgoing && outgoing.contains(document.activeElement);
    let loadingMessage;
    try {
      loadingMessage = setTimeout(() => { status.textContent = '下一封回信正在路上…'; }, 200);
      await load(wanted(target));
      clearTimeout(loadingMessage);
      // Deletions between requests must not leave a blank card or a stale index.
      if (!pool.length) { settle(); render(); return; }
      if (sequence[target] !== targetId) {
        const nearby = sequence.indexOf(targetId, Math.max(0, target - 6));
        target = nearby < 0 ? Math.min(target, sequence.length - 1) : nearby;
      }
      const ids = wanted(target);
      await load(ids);
      if (outgoing) scrollPositions.set(currentId, outgoing.querySelector('.reader-scroll').scrollTop);
      const backwards = direction < 0 && !reshuffle;
      if (!backwards) clearReturn();
      const returning = backwards ? prepareReturn(ids[0]) : null;
      const existing = new Map([...deck.children].filter(card => card !== returnCard).map(card => [card.dataset.id, card]));
      if (returning) {
        // A small deck may also contain this letter in a future slot; promote the preview once.
        const duplicate = existing.get(ids[0]);
        if (duplicate) duplicate.remove();
        existing.set(ids[0], returning);
      }
      ids.forEach(id => {
        if (!existing.has(id) && cache.has(id)) {
          const card = makeCard(cache.get(id));
          slot(card, direction < 0 ? -1 : 3);
          deck.append(card);
          existing.set(id, card);
        }
      });
      const incoming = existing.get(ids[0]);
      if (!incoming) { settle(); render(); return; }
      if (focusWasInCard) next.focus({ preventScroll: true });

      // 提交初始变换，然后让合成器为每一层添加动画

      void deck.offsetHeight;
      deck.classList.remove('is-dragging');
      cancelAnimationFrame(dragFrame);
      dragFrame = 0;
      releaseGesture();
      for (const card of deck.children) { card.style.removeProperty('transform'); card.style.removeProperty('opacity'); }
      if (outgoing && outgoing !== incoming) {
        slot(outgoing, direction < 0 ? 3 : -1);
      }
      ids.forEach((id, position) => { if (existing.has(id)) slot(existing.get(id), position); });
      incoming.querySelector('.reader-scroll').scrollTop = scrollPositions.get(ids[0]) || 0;
      index = target;
      status.textContent = reshuffle ? '重新洗牌，从一封新回信开始。' : `第 ${index % pool.length + 1} 封，共 ${pool.length} 封。`;
      await waitForTransition(incoming);
      render();
      prefetch();
    } catch (_) {
      if (reshuffle) { sequence = oldSequence; index = oldIndex; }
      settle();
      status.textContent = '回信暂时没送到，请再点一次试试。';
    } finally {
      clearTimeout(loadingMessage);
      busy = false;
      controls();
    }
  }

  previous.addEventListener('click', () => move(-1));
  next.addEventListener('click', () => move(1));
  if (shuffleButton) { shuffleButton.hidden = false; shuffleButton.addEventListener('click', () => move(1, true)); }
  root.querySelector('[data-controls]').hidden = false;
  root.querySelector('[data-hint]').hidden = false;

  document.addEventListener('keydown', event => {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.target.closest('input,textarea,select,button,a,[contenteditable]')) return;
    const forward = event.key === 'ArrowLeft';
    const backward = event.key === 'ArrowRight';
    if (!forward && !backward) return;
    event.preventDefault();
    move(forward ? 1 : -1);
  });

  deck.addEventListener('wheel', event => {
    if (event.ctrlKey || Math.abs(event.deltaX) <= Math.abs(event.deltaY)) return;
    event.preventDefault();
    const now = performance.now();
    if (now - wheelTime > 240) { wheelDistance = 0; wheelMoved = false; }
    wheelTime = now;
    // Consume the momentum tail so one trackpad swipe changes only one letter.
    if (busy || pool.length < 2) { wheelMoved = true; return; }
    if (wheelMoved) return;
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? deck.clientWidth : 1;
    wheelDistance += event.deltaX * unit;
    if (Math.abs(wheelDistance) < 64) return;
    wheelMoved = true;
    // Positive horizontal scrolling moves the card left, matching a left swipe.
    move(wheelDistance > 0 ? 1 : -1);
  }, { passive: false });

  deck.addEventListener('pointerdown', event => {
    if (!event.isPrimary) { settle(); return; }
    if (busy || event.button !== 0 || pool.length < 2 || event.target.closest('a,button') || window.getSelection()?.toString()) return;
    gesture = {
      pointerId: event.pointerId, x: event.clientX, y: event.clientY,
      dx: 0, time: performance.now(), locked: false,
    };
  });

  deck.addEventListener('pointermove', event => {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const dx = event.clientX - gesture.x;
    const dy = event.clientY - gesture.y;
    if (!gesture.locked) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 9) return;
      // Vertical movement belongs to the scrollable answer, at every scroll position.
      if (Math.abs(dy) >= Math.abs(dx) || dx > 0 && index === 0) { settle(); return; }
      gesture.locked = true;
      deck.setPointerCapture(event.pointerId);
    }
    event.preventDefault();
    gesture.dx = dx;
    queueDrag();
  });

  deck.addEventListener('pointerup', event => {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const { dx, time, locked } = gesture;
    const elapsed = Math.max(1, performance.now() - time);
    releaseGesture();
    cancelAnimationFrame(dragFrame);
    dragFrame = 0;
    if (locked && (Math.abs(dx) > 64 || Math.abs(dx) > 28 && Math.abs(dx) / elapsed > .45)) move(dx < 0 ? 1 : -1);
    else settle();
  });
  deck.addEventListener('pointercancel', settle);
  deck.addEventListener('lostpointercapture', event => {

    // Touch 事件从子事件开始隐式捕获；将其转移到牌组
    if (gesture?.pointerId === event.pointerId && event.target === deck && !deck.hasPointerCapture(event.pointerId)) settle();
  });
  deck.addEventListener('pointerleave', () => { if (gesture && !gesture.locked) settle(); });
  window.addEventListener('resize', settle, { passive: true });
  window.addEventListener('pagehide', settle);

  render();
  prefetch();
})();
