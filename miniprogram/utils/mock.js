// Frontend-only demo storage. Never sends requests, performs wx.login or authorises messages.
const KEY = 'qb-demo-v1';
const samples = [
  { id: '11111111-1111-4111-8111-111111111111', text: '最近有什么让你觉得生活很可爱的小事？', signature: '',
    answer: '傍晚走路回来的时候，发现路边的一棵树开花了。\n\n有些快乐很小，但认真看就会发现。',
    created_at: '2026-10-07T10:00:00+08:00', answered_at: '2026-10-08T19:30:00+08:00',
    answer_updated_at: '2026-10-08T19:30:00+08:00', version: 1, status: 'answered', mine: true,
    is_featured: true, tags: [{ id: 1, name: '日常' }] },
  { id: '22222222-2222-4222-8222-222222222222', text: '刚开始学编程时，应该怎样保持好奇心？', signature: '路过的同学',
    answer: '从一个你真的想解决的小问题开始。把学到的东西用起来，比一次学很多更容易坚持。',
    created_at: '2026-10-06T09:00:00+08:00', answered_at: '2026-10-07T12:00:00+08:00',
    answer_updated_at: '2026-10-07T12:00:00+08:00', version: 1, status: 'answered', mine: false,
    is_featured: false, tags: [{ id: 2, name: '学习' }] },
  { id: '33333333-3333-4333-8333-333333333333', text: '如果可以给过去的自己写一张便签，你会写什么？', signature: '',
    answer: null, created_at: '2026-10-09T09:00:00+08:00', answered_at: null, answer_updated_at: null,
    version: 0, status: 'pending', mine: true }
];

function state() {
  const saved = wx.getStorageSync(KEY);
  return saved && Array.isArray(saved.questions) ? saved : { questions: JSON.parse(JSON.stringify(samples)) };
}
function clean(q, privateResult) {
  const result = Object.assign({}, q, { display_name: q.signature || '匿名朋友',
    is_featured: q.status === 'answered' && !!q.is_featured, tags: q.status === 'answered' ? q.tags || [] : [] });
  delete result.mine; delete result.client_request_id; delete result.payload;
  if (!privateResult) delete result.wechat_reminder;
  else result.wechat_reminder = result.wechat_reminder || { enabled: false, status: 'none' };
  return result;
}
function failure(message, status) { const error = new Error(message); error.status = status; throw error; }

async function call(path, method, data) {
  const current = state();
  if (path === '/meta/') return { site_title: '站长的匿名提问箱', owner_name: '站长', max_characters: 700,
    max_signature: 30, capacity: 500, box_full: current.questions.length >= 500, wechat_enabled: true,
    email_enabled: true, reminders_enabled: true, reply_template_id: null };
  if (path === '/questions/' && method === 'POST') {
    const previous = current.questions.find(q => q.client_request_id === data.client_request_id);
    if (previous) {
      if (JSON.stringify(previous.payload) !== JSON.stringify(data)) failure('这次投递标识已用于其他内容。', 409);
      return { question: clean(previous, true), created: false };
    }
    if (!data.text.trim() || Array.from(data.text.trim()).length > 700) failure('问题需要填写 1 至 700 字。', 400);
    if (Array.from(data.signature || '').length > 30) failure('署名最多 30 字。', 400);
    if (data.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(data.email)) failure('请输入有效邮箱。', 400);
    if (current.questions.length >= 500) failure('演示提问箱已满。', 409);
    const question = { id: data.client_request_id, client_request_id: data.client_request_id, payload: Object.assign({}, data),
      text: data.text.trim(), signature: (data.signature || '').trim(), answer: null, created_at: new Date().toISOString(),
      answered_at: null, answer_updated_at: null, version: 0, status: 'pending', mine: true };
    current.questions.push(question); wx.setStorageSync(KEY, current);
    return { question: clean(question, true), created: true };
  }
  const list = path.match(/^\/(me\/)?questions\/\?(.+)$/);
  if (list) {
    const params = {};
    list[2].split('&').forEach(pair => { const [key, value] = pair.split('='); params[key] = decodeURIComponent(value || ''); });
    const mine = !!list[1], page = Number(params.page || 1), size = mine ? 10 : ({ 7: 7, 14: 14 }[params.per_page] || 5);
    const publicRows = current.questions.filter(q => q.status === 'answered');
    const tags = [];
    publicRows.forEach(q => (q.tags || []).forEach(t => { if (!tags.some(tag => tag.id === t.id)) tags.push(t); }));
    const view = params.view === 'featured' ? 'featured' : 'all';
    const selectedTag = view === 'all' && params.tag ? tags.find(t => String(t.id) === params.tag) : null;
    const rows = current.questions.filter(q => {
      if (mine) return q.mine;
      if (q.status !== 'answered') return false;
      if (view === 'featured') return q.is_featured;
      return !params.tag || (q.tags || []).some(t => String(t.id) === params.tag);
    }).sort((a, b) =>
      Date.parse(mine ? b.created_at : b.answer_updated_at) - Date.parse(mine ? a.created_at : a.answer_updated_at));
    return { items: rows.slice((page - 1) * size, page * size).map(q => clean(q, mine)), page,
      has_more: page * size < rows.length, count: rows.length,
      ...(!mine ? { tags, selected_view: view, selected_tag: selectedTag || null,
        public_answer_count: publicRows.length, public_topic_count: tags.length } : {}) };
  }
  const detail = path.match(/^\/(me\/)?questions\/([\w-]+)\/(wechat-reminder\/)?$/);
  if (detail) {
    const q = current.questions.find(item => item.id === detail[2] && (detail[1] ? item.mine : item.status === 'answered'));
    if (!q) failure('内容已不存在。', 404);
    if (detail[3]) {
      q.wechat_reminder = { enabled: data.enabled, status: 'none' };
      wx.setStorageSync(KEY, current);
    }
    return { question: clean(q, !!detail[1]) };
  }
  failure('演示接口不存在。', 404);
}
module.exports = { call };
