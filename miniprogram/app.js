const config = require('./config');

App({
  globalData: { demo: config.demo, preference: 'system', systemTheme: 'light' },
  onLaunch() {
    const preference = wx.getStorageSync('qb-theme');
    if (['light', 'dark', 'system'].includes(preference)) this.globalData.preference = preference;
    this.globalData.systemTheme = wx.getSystemInfoSync().theme || 'light';
    if (wx.onThemeChange) wx.onThemeChange(({ theme }) => {
      this.globalData.systemTheme = theme;
      this.paintAll();
    });
  },
  theme() { return this.globalData.preference === 'system' ? this.globalData.systemTheme : this.globalData.preference; },
  setTheme(preference) {
    this.globalData.preference = preference;
    wx.setStorageSync('qb-theme', preference);
    this.paintAll();
  },
  paint(page) {
    const theme = this.theme();
    page.setData({ theme, demo: config.demo, themeIndex: ['system', 'light', 'dark'].indexOf(this.globalData.preference),
      themeLabel: { system: '跟随系统', light: '明亮', dark: '黑暗' }[this.globalData.preference] });
    const dark = theme === 'dark';
    wx.setNavigationBarColor({ frontColor: dark ? '#ffffff' : '#000000', backgroundColor: dark ? '#20242d' : '#f4f255' });
    wx.setBackgroundColor({ backgroundColor: dark ? '#20242d' : '#f4f255' });
    const syncTabBar = () => {
      const tabBar = page.getTabBar && page.getTabBar();
      if (tabBar) {
        tabBar.setData({ theme });
        if (tabBar.sync) tabBar.sync(page.route);
      }
    };
    syncTabBar();
    if (wx.nextTick) wx.nextTick(syncTabBar);
  },
  paintAll() { getCurrentPages().forEach(page => this.paint(page)); }
});
