const api = require('../../utils/api');
const { definePage, decorate, subscribe } = require('../../utils/page');
const DRAFT_KEY = api.isDemo() ? 'qb-draft-demo' : 'qb-draft';
// 1
definePage({
  data: { text: '', signature: '', email: '', count: 0, meta: null, sending: false, reminding: false,
    success: null, uncertain: false },
  onLoad() {
    const saved = wx.getStorageSync(DRAFT_KEY);
    if (saved && typeof saved.text === 'string') {
      this.setData({ text: saved.text, signature: saved.signature || '', email: saved.email || '',
        count: Array.from(saved.text).length });
      this.submission = saved.submission;
    }
  },
  onShow() { this.loadMeta(); },
  async loadMeta() {
    try { this.setData({ meta: await api.meta() }); }
    catch (e) { this.setData({ error: e.message }); }
  },
  input(e) {
    const field = e.currentTarget.dataset.field;
    this.setData({ [field]: e.detail.value, success: null, error: '', uncertain: false,
      ...(field === 'text' ? { count: Array.from(e.detail.value).length } : {}) });
    // A changed payload gets a new id; unchanged retries retain the same one.
    this.saveDraft();
  },
  saveDraft() {
    wx.setStorageSync(DRAFT_KEY, { text: this.data.text, signature: this.data.signature, email: this.data.email,
      submission: this.submission });
  },
  async submit() {
    if (this.data.sending || !this.data.meta) return;
    const payload = { text: this.data.text.trim(), signature: this.data.signature.trim(), email: this.data.email.trim().toLowerCase() };
    if (!payload.text || Array.from(payload.text).length > 700) { this.setData({ error: '请填写 1 至 700 字的问题。' }); return; }
    if (Array.from(payload.signature).length > 30) { this.setData({ error: '署名最多 30 字。' }); return; }
    const fingerprint = JSON.stringify(payload);
    if (!this.submission || this.submission.fingerprint !== fingerprint) this.submission = { fingerprint, id: api.uuid() };
    this.saveDraft();
    this.setData({ sending: true, error: '', uncertain: false });
    try {
      const result = await api.ask({ ...payload, client_request_id: this.submission.id });
      this.submission = null;
      wx.removeStorageSync(DRAFT_KEY);
      this.setData({ success: decorate(result.question), text: '', signature: '', email: '', count: 0 });
    } catch (e) { this.setData({ error: e.message, uncertain: !!e.uncertain }); }
    finally { this.setData({ sending: false }); }
  },
  remind() {
    if (this.data.reminding || !this.data.success || !this.data.meta.reminders_enabled) return;
    // Subscribe immediately in the tap handler, before any network request.
    const permission = api.isDemo() ? Promise.resolve() : subscribe(this.data.meta);
    this.setData({ reminding: true, error: '' });
    permission.then(() => api.reminder(this.data.success.id, true)).then(result => {
      this.setData({ success: decorate(result.question) });
      wx.showToast({ title: api.isDemo() ? '演示：已开启提醒' : '已开启回复提醒', icon: 'none' });
    }).catch(e => this.setData({ error: e.message })).finally(() => this.setData({ reminding: false }));
  },
  readSuccess() { wx.navigateTo({ url: `/pages/detail/index?id=${this.data.success.id}&mine=1` }); }
});
