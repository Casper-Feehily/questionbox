Component({
  data: { selected: 0, theme: 'light', keyboardVisible: false, tabs: [
    { path: '/pages/wall/index', text: '公开回信', icon: 'mail' },
    { path: '/pages/ask/index', text: '向我提问', icon: 'write' },
    { path: '/pages/mine/index', text: '我的提问', icon: 'mine' }
  ] },
  lifetimes: {
    attached() {
      this.sync();
      if (wx.nextTick) wx.nextTick(() => this.sync());
      this.keyboardListener = ({ height }) => this.setData({ keyboardVisible: height > 0 });
      if (wx.onKeyboardHeightChange) wx.onKeyboardHeightChange(this.keyboardListener);
    },
    detached() {
      if (wx.offKeyboardHeightChange) wx.offKeyboardHeightChange(this.keyboardListener);
    }
  },
  pageLifetimes: { show() { this.sync(); } },
  methods: {
    sync(route) {
      const pages = getCurrentPages();
      const page = pages[pages.length - 1];
      const currentRoute = route || (page && page.route);
      const index = this.data.tabs.findIndex(tab => tab.path === `/${currentRoute}`);
      this.setData({ theme: getApp().theme(), ...(index >= 0 ? { selected: index } : {}) });
    },
    switchTab(e) {
      const index = Number(e.currentTarget.dataset.index);
      if (this.switching || !Number.isInteger(index) || index < 0 || index >= this.data.tabs.length || index === this.data.selected) return;
      this.switching = true;
      wx.switchTab({ url: this.data.tabs[index].path, complete: () => { this.switching = false; this.sync(); } });
    }
  }
});
