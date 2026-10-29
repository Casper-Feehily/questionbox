(() => {
  const form = document.querySelector('[data-classification]');
  if (!form) return;
  const status = form.querySelector('[data-classification-status]');
  const button = form.querySelector('button[type="submit"]');
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (button.disabled) return;
    // Snapshot before locking the fields: disabled controls are omitted by FormData.
    const body = new URLSearchParams(new FormData(form));
    const controls = [...form.querySelectorAll('input, button, select, textarea')];
    const disabled = controls.map(control => control.disabled);
    controls.forEach(control => { control.disabled = true; });
    status.classList.remove('field-error');
    status.textContent = '正在保存分类…';
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(form.getAttribute('action'), {
        method: 'POST', body,
        credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: controller.signal,
      });
      if (response.redirected || response.status === 403) throw new Error('登录已失效，请先确认回答草稿已保存，再重新登录。');
      if (response.status === 404) throw new Error('问题已不存在，请先复制当前回答。');
      const data = await response.json();
      if (!response.ok) {
        const errors = Object.values(data.errors || {}).flat().map(error => error.message);
        throw new Error(errors.join(' ') || '分类未保存，请稍后重试。');
      }
      // Use text nodes for owner-supplied names; never interpolate labels as HTML.
      const options = form.querySelector('[data-tag-options]');
      options.replaceChildren();
      data.tags.forEach(tag => {
        const label = document.createElement('label');
        label.className = 'classification-tag';
        const input = document.createElement('input');
        input.type = 'checkbox'; input.name = 'tags'; input.value = tag.id;
        input.checked = data.selected.includes(tag.id);
        label.append(input, document.createTextNode(` ${tag.name}`));
        options.append(label);
      });
      if (!data.tags.length) options.textContent = '还没有标签，添加第一个吧。';
      form.elements.is_featured.checked = data.is_featured;
      form.elements.new_tags.value = '';
      status.textContent = data.message;
    } catch (error) {
      status.classList.add('field-error');
      status.textContent = error.name === 'AbortError' ? '保存超时，分类状态未确认，请重试。' : error.message;
    } finally {
      clearTimeout(timeout);
      controls.forEach((control, index) => { control.disabled = disabled[index]; });
    }
  });
})();
