const api = require('../../utils/api');
const { definePage, decorate, subscribe } = require('../../utils/page');
const { createQuestionCover, questionCoverKey } = require('../../utils/share-cover');

definePage({
  share() {
    const question = this.data.question;
    if (this.data.mine || !question || question.status !== 'answered' || !question.answer) return { timeline: false };
    const query = `id=${encodeURIComponent(question.id)}`;
    const title = Array.from(question.text.replace(/\s+/g, ' ').trim()).slice(0, 60).join('');
    return { title: `${title}｜站长的提问箱`,
      path: `/pages/detail/index?${query}`, query,
      ...(this.questionCover && this.questionCover.key === questionCoverKey(question) ? { imageUrl: this.questionCover.path } : {}) };
  },
  data: { mine: false, question: null, meta: null, reminding: false, missing: false },
  onLoad(options) { this.id = options.id; this.setData({ mine: options.mine === '1' }); },
  onReady() { this.coverReady = true; this.prepareShareCover(); },
  onShow() { this.reload(); },
  prepareShareCover() {
    const question = this.data.question;
    if (!this.coverReady || this.data.mine || this.data.singlePage || !question || question.status !== 'answered' || !question.answer) {
      this.questionCover = null; this.coverPendingKey = null;
      this.coverGeneration = (this.coverGeneration || 0) + 1;
      return;
    }
    const key = questionCoverKey(question);
    if (this.questionCover && this.questionCover.key === key || this.coverPendingKey === key) return;
    const generation = this.coverGeneration = (this.coverGeneration || 0) + 1;
    this.coverPendingKey = key;
    this.coverJob = createQuestionCover(this, question).then(path => {
      if (generation === this.coverGeneration) this.questionCover = { key, path };
    }).catch(() => { /* The fixed cover remains available if drawing/export fails. */ })
      .finally(() => { if (generation === this.coverGeneration) this.coverPendingKey = null; });
  },
  async reload() {
    if (this.data.loading) return;
    this.setData({ loading: true, error: '' });
    api.meta().then(meta => this.setData({ meta })).catch(() => {});
    try {
      const result = await api.detail(this.id, this.data.mine);
      this.setData({ question: decorate(result.question), missing: false }, () => this.prepareShareCover());
    } catch (e) { this.setData({ error: e.message, ...(e.status === 404 ? { question: null, missing: true } : {}) }); }
    finally { this.setData({ loading: false }); this.refreshShareMenu(); }
  },
  onPullDownRefresh() { Promise.resolve(this.reload()).finally(() => wx.stopPullDownRefresh()); },
  remind() {
    if (this.data.reminding || !this.data.meta || !this.data.meta.reminders_enabled) return;
    const permission = api.isDemo() ? Promise.resolve() : subscribe(this.data.meta);
    this.setData({ reminding: true, error: '' });
    permission.then(() => api.reminder(this.id, true)).then(result => {
      this.setData({ question: decorate(result.question) });
      wx.showToast({ title: api.isDemo() ? '演示：已开启提醒' : '已开启回复提醒', icon: 'none' });
    }).catch(e => this.setData({ error: e.message })).finally(() => this.setData({ reminding: false }));
  },
  async stopReminder() {
    if (this.data.reminding) return;
    this.setData({ reminding: true, error: '' });
    try { this.setData({ question: decorate((await api.reminder(this.id, false)).question) }); }
    catch (e) { this.setData({ error: e.message }); }
    finally { this.setData({ reminding: false }); }
  },
  onUnload() {
    this.coverReady = false; this.questionCover = null;
    this.coverGeneration = (this.coverGeneration || 0) + 1;
  }
});
