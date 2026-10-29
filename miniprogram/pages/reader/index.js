const api = require('../../utils/api');
const { definePage, decorate } = require('../../utils/page');
const { Reader, motion } = require('../../utils/reader');

definePage({
  share() { return { title: '随便读读｜一些好奇，一些认真回答', path: '/pages/reader/index', query: '' }; },
  data: { cards: [], styles: {}, meta: null, position: '00', total: 0, canPrevious: false, canNext: false, busy: false, dragging: false },
  onLoad() {
    this.alive = true;
    this.width = 300;
    this.scrolls = new Map();
    this.reload();
  },
  onReady() { this.measure(); },
  onResize() { this.cancel(); this.measure(); },
  measure() {
    wx.createSelectorQuery().in(this).select('.reader-deck').boundingClientRect(rect => {
      if (rect && this.alive) { this.width = rect.width; if (!this.data.busy) this.paint(); }
    }).exec();
  },
  async reload() {
    if (this.data.loading || this.data.busy) return;
    this.setData({ loading: true, error: '' });
    const reader = new Reader(api.wall);
    this.reader = reader;
    api.meta().then(meta => { if (this.alive) this.setData({ meta }); }).catch(() => {});
    try {
      await reader.load();
      if (this.alive && reader === this.reader) { this.paint(true); this.prefetch(); }
    } catch (e) { if (this.alive) this.setData({ error: e.message }); }
    finally { if (this.alive) this.setData({ loading: false }); }
  },
  paint(measureAfter = false) {
    if (!this.reader) return;
    const cards = this.reader.visible().map(card => Object.assign({}, card, {
      question: decorate(card.question), scrollTop: this.scrolls.get(card.question.id) || 0
    }));
    this.setData({ cards, styles: motion(cards, this.width), dragging: false,
      position: String(this.reader.pool.length ? this.reader.index % this.reader.pool.length + 1 : 0).padStart(2, '0'),
      total: this.reader.total, canPrevious: this.reader.index > 0,
      canNext: this.reader.total > 1 }, () => { if (measureAfter && this.alive) this.measure(); });
  },
  prefetch() {
    const reader = this.reader;
    if (!reader.hasMore || reader.sequence.length > reader.index + 5) return;
    reader.load().then(() => {
      if (this.alive && reader === this.reader && !this.data.busy && !this.gesture) this.paint();
    }).catch(() => {});
  },
  next() { return this.move(1); },
  previous() { return this.move(-1); },
  async move(direction) {
    if (this.data.busy || !this.reader) return;
    // A release can beat the queued drag frame. Start at the finger's latest position.
    const initial = this.gesture && this.gesture.locked
      ? motion(this.data.cards, this.width, this.gesture.dx) : Object.assign({}, this.data.styles);
    this.gesture = null;
    clearTimeout(this.dragTimer);
    this.dragTimer = null;
    this.animating = false;
    this.setData({ busy: true, error: '', styles: initial, dragging: true });
    try {
      const target = await this.reader.plan(direction);
      if (!this.alive) return;
      if (target === null) { this.cancel(); return; }
      const targetCards = this.reader.visible(target).map(card => Object.assign({}, card, {
        question: decorate(card.question), scrollTop: this.scrolls.get(card.question.id) || 0
      }));
      const existing = new Map(this.data.cards.map(card => [card.key, card]));
      // Keep each mounted card's content/scroll role until motion finishes. The next
      // hidden history card is mounted at rest by paint(), never flown across the deck.
      targetCards.forEach(card => {
        if (card.slot >= 0 && !existing.has(card.key)) existing.set(card.key, card);
      });
      const cards = Array.from(existing.values());
      const final = motion(targetCards, this.width);
      if (direction < 0) {
        const incoming = targetCards.find(card => card.slot === 0);
        initial[incoming.key] = initial[incoming.key].replace(/opacity:[\d.]+;/, 'opacity:1;');
      }
      const sunk = motion(this.data.cards, this.width, direction < 0 ? this.width : 0);
      cards.forEach(card => {
        if (!initial[card.key]) initial[card.key] = final[card.key].replace('opacity:1;', 'opacity:0;');
        if (!final[card.key]) final[card.key] = sunk[card.key].replace('opacity:1;', 'opacity:0;');
      });
      const outgoing = this.data.cards.find(card => card.slot === 0);
      if (direction > 0 && outgoing) final[outgoing.key] = `transform:translate3d(${-this.width * 1.1}px,0,0) scale(.96) rotate(-3deg);opacity:0;z-index:5;`;
      this.animating = true;
      // Returning reuses all mounted cards; avoid resending full answers to the view.
      this.setData({ ...(cards.length !== this.data.cards.length ? { cards } : {}), styles: initial, dragging: true }, () => {
        this.startTimer = setTimeout(() => {
          if (!this.alive) return;
          this.setData({ styles: final, dragging: false });
          this.endTimer = setTimeout(() => {
            if (!this.alive) return;
            this.startTimer = null;
            this.endTimer = null;
            this.animating = false;
            this.reader.commit(target);
            this.paint();
            this.setData({ busy: false });
            this.prefetch();
          }, 560);
        }, 32);
      });
    } catch (e) {
      if (this.alive) { this.cancel(); this.setData({ error: e.message }); }
    } finally {
      if (this.alive && !this.animating) { this.setData({ busy: false }); this.cancel(); }
    }
  },
  async shuffle() {
    if (this.data.busy || !this.reader) return;
    this.cancel();
    this.setData({ busy: true, error: '' });
    try { await this.reader.shuffle(); if (this.alive) this.paint(); }
    catch (e) { if (this.alive) this.setData({ error: e.message }); }
    finally { if (this.alive) { this.setData({ busy: false }); this.prefetch(); } }
  },
  touchStart(e) {
    if (this.data.busy || !this.data.canNext || e.touches.length !== 1) return;
    const point = e.touches[0];
    this.gesture = { x: point.clientX, y: point.clientY, dx: 0, time: Date.now(), locked: false };
  },
  touchMove(e) {
    if (!this.gesture) return;
    if (e.touches.length !== 1) { this.cancel(); return; }
    const point = e.touches[0], dx = point.clientX - this.gesture.x, dy = point.clientY - this.gesture.y;
    if (!this.gesture.locked) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 9) return;
      if (Math.abs(dy) >= Math.abs(dx) || dx > 0 && !this.data.canPrevious) { this.cancel(); return; }
      this.gesture.locked = true;
    }
    this.gesture.dx = dx;
    if (!this.dragTimer) this.dragTimer = setTimeout(() => {
      this.dragTimer = null;
      if (this.gesture && this.alive) this.setData({ styles: motion(this.data.cards, this.width, this.gesture.dx), dragging: true });
    }, 16);
  },
  touchEnd() {
    if (!this.gesture) return;
    const { dx, time, locked } = this.gesture;
    if (locked && (Math.abs(dx) > 64 || Math.abs(dx) > 28 && Math.abs(dx) / Math.max(1, Date.now() - time) > .45)) this.move(dx < 0 ? 1 : -1);
    else this.cancel();
  },
  cancel() {
    clearTimeout(this.dragTimer);
    this.dragTimer = null;
    this.gesture = null;
    if (this.alive && this.reader && !this.data.busy) this.setData({ dragging: false, styles: motion(this.data.cards, this.width) });
  },
  scroll(e) {
    const card = this.data.cards.find(item => item.key === e.currentTarget.dataset.key);
    if (card && card.slot === 0) this.scrolls.set(card.question.id, e.detail.scrollTop);
  },
  back() {
    if (getCurrentPages().length > 1) wx.navigateBack();
    else wx.switchTab({ url: '/pages/wall/index' });
  },
  onHide() { this.cancel(); },
  onUnload() {
    this.alive = false;
    [this.dragTimer, this.startTimer, this.endTimer].forEach(timer => clearTimeout(timer));
    this.gesture = null;
  }
});
