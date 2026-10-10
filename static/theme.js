(() => {
  let theme = 'system';
  try { theme = localStorage.getItem('qb-theme') || 'system'; } catch (_) {}
  if (!['system', 'light', 'dark'].includes(theme)) theme = 'system';
  if (theme !== 'system') document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePreference = theme;
})();
