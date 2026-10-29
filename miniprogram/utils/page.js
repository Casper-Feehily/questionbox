const api = require('./api');
const HOME_SHARE = { title: '站长的提问箱｜一些好奇，一些认真回答', path: '/pages/wall/index',
  imageUrl: '/assets/share-cover.png', query: '' };

function definePage(spec) {
  const load = spec.onLoad;
  const show = spec.onShow;
  const sharing = spec.share;
  const pageSpec = Object.assign({}, spec);
  delete pageSpec.share;
  const content = page => Object.assign({}, HOME_SHARE, typeof sharing === 'function' ? sharing.call(page) : {});
  Page(Object.assign({}, pageSpec, {
    data: Object.assign({ theme: 'light', demo: true, themeIndex: 0, themeLabel: '跟随系统', singlePage: false,
      themeChoices: ['跟随系统', '明亮', '黑暗'], error: '', loading: false }, spec.data),
    onLoad(options) { getApp().paint(this); this.updateEntryMode(); if (load) load.call(this, options); this.refreshShareMenu(); },
    onShow() { getApp().paint(this); this.updateEntryMode(); this.refreshShareMenu(); if (show) show.call(this); },
    updateEntryMode() {
      const getOptions = wx.getEnterOptionsSync || wx.getLaunchOptionsSync;
      const options = getOptions ? getOptions.call(wx) : {};
      this.setData({ singlePage: Number(options.scene) === 1154 });
    },
    refreshShareMenu() {
      if (this.data.singlePage) return;
      const timeline = !!sharing && content(this).timeline !== false;
      if (wx.showShareMenu) wx.showShareMenu({ menus: timeline ? ['shareAppMessage', 'shareTimeline'] : ['shareAppMessage'] });
      if (!timeline && wx.hideShareMenu) wx.hideShareMenu({ menus: ['shareTimeline'] });
    },
    onShareAppMessage() {
      const { title, path, imageUrl } = content(this);
      return { title, path, imageUrl };
    },
    ...(sharing ? { onShareTimeline() {
      const { title, query, imageUrl } = content(this);
      return { title, query, imageUrl };
    } } : {}),
    changeTheme(e) { getApp().setTheme(e.currentTarget && e.currentTarget.dataset.preference || ['system', 'light', 'dark'][Number(e.detail.value)]); },
    openPrivacy() { wx.navigateTo({ url: '/pages/privacy/index' }); },
    goHome() { wx.switchTab({ url: '/pages/wall/index' }); },
    goMine() { wx.switchTab({ url: '/pages/mine/index' }); },
    goAsk() { wx.switchTab({ url: '/pages/ask/index' }); },
    openReader() { wx.navigateTo({ url: '/pages/reader/index' }); },
    openDetail(e) { wx.navigateTo({ url: `/pages/detail/index?id=${e.currentTarget.dataset.id}${e.currentTarget.dataset.mine ? '&mine=1' : ''}` }); }
  }));
}

function decorate(q) {
  const date = new Date(q.answer_updated_at || q.created_at);
  const dateLabel = Number.isNaN(date.getTime()) ? '' : `${date.getFullYear()}.${String(date.getMonth() + 1).padStart(2, '0')}.${String(date.getDate()).padStart(2, '0')}`;
  return Object.assign({}, q, { date_label: dateLabel,
    short_text: Array.from(q.text).length > 100 ? Array.from(q.text).slice(0, 99).join('') + '…' : q.text,
    preview: q.answer ? (Array.from(q.answer).length > 170 ? Array.from(q.answer).slice(0, 169).join('') + '…' : q.answer) : '',
    reminder_label: q.wechat_reminder && q.wechat_reminder.status === 'sent' ? '提醒已发送' : '已开启回复提醒' });
}

// Call synchronously from bindtap so iOS also recognises the user gesture.
function subscribe(meta) {
  return new Promise((resolve, reject) => wx.requestSubscribeMessage({
    tmplIds: [meta.reply_template_id],
    success(result) { result[meta.reply_template_id] === 'accept' ? resolve() : reject(new Error('未开启微信提醒，你仍可以在“我的提问”查看回信。')); },
    fail() { reject(new Error('订阅未完成，请稍后重试；提问已经保存。')); }
  }));
}

module.exports = { definePage, decorate, subscribe };
