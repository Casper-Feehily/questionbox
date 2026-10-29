const api = require('./api');
const { definePage, decorate } = require('./page');
const WALL_PAGE_SIZE = 7;

module.exports = function listPage(mine) {
  definePage({
    share: !mine,
    data: { mine, items: [], meta: null, page: 0, hasMore: false, count: 0,
      totalPages: 0, pageNumbers: [],
      view: 'featured', tag: '', tags: [], publicCount: null, topicCount: 0 },
    onShow() { return this.reload(); },
    async fetch(reset, requestedPage, scrollToHeading = false) {
      if (!reset && this.data.loading) return;
      if (reset || !this.loadVersion) this.loadVersion = (this.loadVersion || 0) + 1;
      const version = this.loadVersion;
      const page = mine ? (reset ? 1 : this.data.page + 1) : (requestedPage || this.data.page || 1);
      this.failedRequest = null;
      this.setData({ loading: true, error: '' });
      try {
        const result = await (mine ? api.mine(page) : api.wall(page, {
          view: this.data.view, tag: this.data.view === 'all' ? this.data.tag : '', per_page: WALL_PAGE_SIZE
        }));
        if (version !== this.loadVersion) return;
        const totalPages = Math.max(1, Math.ceil(result.count / WALL_PAGE_SIZE));
        // The API returns an empty page beyond the end; match the website's last-page fallback.
        if (!mine && page > totalPages) return this.fetch(true, totalPages, scrollToHeading);
        const start = Math.max(1, Math.min(page - 2, totalPages - 4));
        this.setData({ items: !mine || reset ? result.items.map(decorate) : this.data.items.concat(result.items.map(decorate)),
          page: result.page, count: result.count, hasMore: result.has_more,
          ...(!mine ? { tags: result.tags || [], publicCount: result.public_answer_count,
            topicCount: result.public_topic_count || 0, totalPages,
            pageNumbers: Array.from({ length: Math.min(5, totalPages) }, (_, index) => start + index) } : {}) });
        if (!mine && scrollToHeading) wx.nextTick(() => {
          if (version === this.loadVersion) wx.pageScrollTo({ selector: '#answers-title', duration: 0 });
        });
      } catch (e) {
        if (version === this.loadVersion) {
          this.failedRequest = { page, scrollToHeading };
          this.setData({ error: e.message });
        }
      }
      finally { if (version === this.loadVersion) this.setData({ loading: false }); }
    },
    reload() {
      api.meta().then(meta => this.setData({ meta })).catch(() => {});
      return this.fetch(true);
    },
    more() { if (mine && this.data.hasMore) return this.fetch(false); },
    changePage(e) {
      const page = Number(e.currentTarget.dataset.page);
      if (mine || !Number.isInteger(page) || page < 1 || page > this.data.totalPages
        || (page === this.data.page && !this.data.loading)) return;
      return this.fetch(true, page, true);
    },
    retry() {
      if (mine || !this.failedRequest) return this.reload();
      const { page, scrollToHeading } = this.failedRequest;
      return this.fetch(true, page, scrollToHeading);
    },
    selectView(e) {
      const view = e.currentTarget.dataset.view;
      if (mine || !['featured', 'all'].includes(view) || this.data.view === view) return;
      this.setData({ view, tag: '', items: [], page: 0, hasMore: false, count: 0, totalPages: 0, pageNumbers: [] });
      return this.fetch(true);
    },
    selectTag(e) {
      if (mine) return;
      const tag = String(e.currentTarget.dataset.tag || '');
      if (this.data.view === 'all' && this.data.tag === tag) return;
      this.setData({ view: 'all', tag, items: [], page: 0, hasMore: false, count: 0, totalPages: 0, pageNumbers: [] });
      return this.fetch(true);
    },
    onUnload() { this.loadVersion = (this.loadVersion || 0) + 1; },
    onReachBottom() { this.more(); },
    onPullDownRefresh() { Promise.resolve(this.reload()).finally(() => wx.stopPullDownRefresh()); }
  });
};
