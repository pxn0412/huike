const $ = (selector) => document.querySelector(selector);
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// AI 只负责提取：面板展示提取快照，表单才是用户确认并提交的内容。
const AI_FIELDS = [['name', '比赛名称'], ['edition', '届次'], ['publisher', '主办方'], ['published_at', '发布时间'], ['track', '适用赛道'], ['title', '资料标题']];
function normalizeDate(value) {
  const text = String(value ?? '').trim();
  if (!text) return '';
  const match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/) || text.match(/(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})/);
  if (!match) return '';
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(Date.UTC(year, month - 1, day));
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return '';
  return `${match[1]}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
}
const state = { competitions: [], current: null, documents: [], upload: null, tab: 'notices', pendingQuery: '', page: 'home', busy: false, status: {}, conversations: {}, resumeUpload: false };
let identity;
try { identity = JSON.parse(localStorage.getItem('huike-identity') || 'null'); } catch { identity = null; }
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 6500); }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '请求失败，请重试。');
  return value;
}
function updateIdentity() { $('#identity-label').textContent = identity ? `${identity.name} · ${identity.role === 'teacher' ? '老师' : '学生'}` : '设置演示身份'; }
function showIdentity() { const form = $('#identity-form'); form.elements.name.value = identity?.name || ''; form.elements.role.value = identity?.role || 'student'; $('#identity-dialog').showModal(); }
function closeSource() { $('#source-panel').hidden = true; $('#source-frame').src = 'about:blank'; document.body.classList.remove('source-open'); }
function openSource(url, title, page = 1) { $('#source-title').textContent = title; $('#source-frame').src = `/viewer.html?file=${encodeURIComponent(url)}&page=${page}`; $('#source-link').href = `${url}#page=${page}`; $('#source-panel').hidden = false; document.body.classList.add('source-open'); }
function kbLabel(kb){
  if(!kb) return '未同步到知识库';
  if(kb.status==='uploaded') return '已进知识库' + (kb.chunk_count ? `（${kb.chunk_count} 块）` : '') + (kb.filename ? ` · ${kb.filename}` : '');
  if(kb.status==='duplicate') return '知识库已有同名文档，没有重复导入' + (kb.filename ? ` · ${kb.filename}` : '');
  if(kb.status==='skipped') return '未进知识库：' + (kb.message || '未配置');
  return '进知识库失败：' + (kb.message || '未知原因');
}

function kbToast(kb){
  const label = kbLabel(kb);
  if(!kb) return '资料已保存，可查看原文。';
  if(kb.status==='uploaded') return '资料已保存，并已进知识库。';
  if(kb.status==='duplicate') return '资料已保存；知识库里已有同名文档，没有重复导入。';
  return '资料已保存；' + label + '。';
}

function setPage(page, breadcrumb) { state.page = page; $('#breadcrumb').textContent = breadcrumb; document.querySelectorAll('.nav').forEach(n => n.classList.toggle('active', n.dataset.action === page)); }
function competitionCards(items) {
  if (!items.length) return `<div class="empty"><div class="symbol">▤</div><h3>从第一份比赛通知开始</h3><p>上传一份 PDF，将资料整理到对应比赛，之后随时查看原文件与原文片段。</p><button class="primary" data-action="upload">＋ 添加比赛资料</button></div>`;
  return `<div class="cards">${items.map(c => `<button class="card competition" data-action="competition" data-id="${escapeHTML(c.id)}"><span class="tag">${escapeHTML(c.edition)} 届</span><h3>${escapeHTML(c.name)}</h3><p class="muted">${c.document_count} 份资料</p><span class="text-btn">进入比赛空间 →</span></button>`).join('')}</div>`;
}
function home() {
  closeSource(); setPage('home','工作空间 / 首页');
  $('#main').innerHTML = `<div class="welcome-row"><span>你好，${escapeHTML(identity?.name || '参赛者')}。</span><span>从一个想法，到一次出发。</span></div><section class="hero"><div class="hero-copy"><p class="eyebrow">YOUR NEXT CHAPTER</p><h1>下一场比赛，<br>从<span class="accent">看懂通知</span>开始。</h1><p class="muted">整理比赛资料，找到问题的依据。<br>少一点来回翻找，多一点准备的把握。</p><button class="primary" data-action="upload">＋ 上传比赛通知</button><button class="text-btn" data-action="library">浏览比赛库 ↗</button></div><div class="hero-art" aria-hidden="true"><div class="orbit"></div><div class="paper-back"></div><div class="paper-front"><span class="paper-tag">COMPETITION BRIEF</span><div class="paper-heading">每个问题<br>都有迹可循。</div><div class="paper-line"></div><div class="paper-line short"></div><div class="paper-highlight">✓ 找到原文依据</div><div class="paper-line"></div><div class="paper-line short"></div><span class="paper-page">01 / 06</span></div><div class="float-note">❝ <span>原文，就在旁边。</span></div><span class="art-star">✳</span></div></section><form id="home-question" class="hero-search"><span class="search-spark">✧</span><input name="query" required maxlength="1000" placeholder="想了解什么？例如：一个人能参加吗？" aria-label="输入你的问题"><button class="primary">选择比赛 →</button></form><p class="help-line">${state.status.ai_configured ? 'AI 接口已配置 · 选择比赛后，基于资料回答。' : '当前可查原文 · AI 回答待连接。'} 不会默认替你选择比赛。</p><div class="section-head"><h2>比赛资料库 <span class="count">${state.competitions.length}</span></h2><button class="text-btn" data-action="library">查看全部 →</button></div><div id="competition-cards">${competitionCards(state.competitions)}</div><div class="benefits"><div><span>01</span><strong>上传一份资料</strong><p>原文件完整保留</p></div><div><span>02</span><strong>确认所属比赛</strong><p>新旧通知有序整理</p></div><div><span>03</span><strong>带着依据了解规则</strong><p>点击引用，直达原文</p></div></div>`;
}
function library() {
  closeSource(); setPage('library','工作空间 / 比赛库');
  $('#main').innerHTML = `<div class="section-head"><div><p class="eyebrow">COMPETITIONS</p><h1>比赛库</h1></div><button class="primary" data-action="upload">＋ 添加资料</button></div>${state.pendingQuery ? `<p class="query-label">你的问题：${escapeHTML(state.pendingQuery)}<br>请选择要查询的比赛。</p>` : ''}<input id="library-filter" class="search-input library-search" placeholder="搜索比赛名称或届次" aria-label="搜索比赛"><div id="competition-cards">${competitionCards(state.competitions)}</div>`;
}
function uploadPage() {
  if (!identity) { state.resumeUpload=true; showIdentity(); return; }
  closeSource(); setPage('upload','工作空间 / 添加比赛资料');
  $('#main').innerHTML = `<p class="eyebrow">ADD DOCUMENT</p><h1>把比赛通知，放到这里。</h1><p class="muted">保留原始 PDF，确认所属比赛后，加入资料列表。</p><div class="steps"><span>01 上传 PDF</span><span>02 核对信息</span><span>03 加入比赛</span></div><label class="upload-box" id="drop-zone"><div class="upload-icon">⇧</div><h2>点击选择，或拖入 PDF</h2><p class="muted">最多 20 MB · 200 页 · 原文件完整保留</p><input id="pdf-input" type="file" accept="application/pdf,.pdf"></label><div class="sample-row"><span>想先体验一下？</span><button class="text-btn" data-action="example">使用桌面上的慧科杯通知 →</button></div><p class="help-line">上传者：${escapeHTML(identity.name)} · ${identity.role === 'teacher' ? '老师' : '学生'}（自行选择）</p><div class="notice">${state.status.ai_configured ? 'AI 会整理资料信息，提交前请对照右侧原文确认。' : 'AI 尚未连接：资料信息由文件名预填，请手动核对。'} ${state.status.ocr_available ? '扫描页会自动进行本地文字识别，请以 PDF 原图为准。' : '扫描页需要文字识别后才能检索。'}</div>`;
}
async function uploadFile(file) {
  if (!file || state.busy) return;
  if (file.size > 20 * 1024 * 1024) { toast('文件不能超过 20 MB。'); return; }
  state.busy = true;
  $('#main').innerHTML = `<div class="loading">正在读取 PDF 并保留页码…<p class="muted">扫描文件还需要识别图片中的文字${state.status.ai_configured ? '；AI 正在提取资料信息，可能需要一会儿，请勿关闭页面' : ''}。</p></div>`;
  try {
    const content = await new Promise((resolve,reject) => { const r = new FileReader(); r.onload = () => resolve(r.result.split(',')[1]); r.onerror = () => reject(new Error('文件读取失败。')); r.readAsDataURL(file); });
    state.upload = await api('/api/upload', {filename:file.name, content});
    confirmPage();
  } catch(error) { uploadPage(); toast(error.message); }
  finally { state.busy = false; }
}
function confirmPage() {
  const u = state.upload;
  $('#main').innerHTML = `<p class="eyebrow">CHECK DOCUMENT</p><h1>确认这份资料属于哪里</h1><p class="muted">只需核对资料身份，不必逐条审核比赛规则。</p><div class="notice" id="upload-notice"></div>${aiResultPanel(u)}<form id="confirm-form" class="card"><div class="form-grid"><label class="full">所属比赛<select name="competition_id" id="competition-select"><option value="">创建新比赛</option>${state.competitions.map(c=>`<option value="${escapeHTML(c.id)}" ${state.current?.id===c.id?'selected':''}>${escapeHTML(c.name)} · ${escapeHTML(c.edition)}</option>`).join('')}</select></label><label id="new-name">比赛名称<input name="name" maxlength="200" placeholder="例如：慧科杯 AI 创新大赛" required></label><label id="new-edition">届次 / 年份<input name="edition" maxlength="40" value="${escapeHTML(u.suggested.edition)}" placeholder="例如：2026" required></label><label class="full">资料标题<input name="title" required maxlength="300" value="${escapeHTML(u.suggested.title)}"></label><label>发布主体<input name="publisher" maxlength="120" placeholder="未识别，可留空"></label><label>发布时间<input name="published_at" type="date"></label><label class="full">适用赛道（资料身份备注）<input name="track" maxlength="200" placeholder="暂不确定，可留空"><span class="help-line">这里只记录备注；当前检索全比赛资料，尚不支持按赛道筛选。</span></label></div><div class="actions"><button type="button" class="secondary" data-action="cancel-upload">取消提交</button><button class="primary">确认添加</button></div></form><button class="text-btn" data-action="preview-upload">查看上传的 PDF ↗</button>`;
  const form=$('#confirm-form');
  applyAiSuggestions(form,u);
  const matched=state.competitions.filter(c=>c.name===u.suggested.name && c.edition===u.suggested.edition);
  if(!state.current && matched.length===1) form.elements.competition_id.value=matched[0].id;
  const notices=[];
  if(u.extraction_method==='ai') notices.push('AI 已整理资料信息。请对照原文确认，未知字段可留空。');
  else if(!state.status.ai_configured) notices.push('AI 尚未连接：以下信息由文件名预填，请手动核对比赛归属。');
  else if(!u.has_text) notices.push('这份 PDF 未提取到可用文字，无法进行 AI 识别，请手动填写资料信息。');
  else notices.push('AI 未能完成识别：以下信息由文件名预填，请手动填写并核对。');
  if(!u.has_text) notices.push('你仍可保存和预览，暂时不能检索其正文。');
  if(u.metadata?.ocr_pages?.length) notices.push(`已识别 ${u.metadata.ocr_pages.length} 个扫描页，识别文字可能存在误差。`);
  if(u.metadata?.unreadable_pages?.length) notices.push(`第 ${u.metadata.unreadable_pages.join('、')} 页未识别到文字，不能用于回答。`);
  if(u.metadata?.warning) notices.push(u.metadata.warning);
  if(u.metadata?.ai_warning) notices.push(u.metadata.ai_warning);
  $('#upload-notice').textContent=notices.join(' ');
  toggleNewCompetition(); openSource(u.preview_url,u.filename);
}
function aiResultPanel(u) {
  const rows = AI_FIELDS.map(([key, label]) => {
    const raw = String(u.suggested[key] ?? '').trim();
    const note = key === 'published_at' && raw && !normalizeDate(raw)
      ? '<span class="ai-note">原文写法无法直接作为日期，请在表单中手动选择</span>' : '';
    return `<div class="ai-item" data-ai-item="${key}"><dt>${label}</dt><dd class="${raw ? '' : 'ai-blank'}" data-ai-field="${key}">${raw ? escapeHTML(raw) : '未识别，可留空'}</dd>${note}</div>`;
  }).join('');
  const actions = '<div class="ai-actions"><button class="primary" data-action="ai-accept">确认</button><button class="secondary" data-action="ai-edit">修改</button></div>';
  if (u.extraction_method === 'ai') {
    return `<section class="ai-panel" id="ai-panel" data-mode="ai"><div class="ai-head"><span class="ai-badge">AI 识别结果 · 待你确认</span></div><h3>AI 识别结果</h3><p class="help-line">AI 只从这份 PDF（${u.page_count} 页）中提取信息，不会自动创建比赛。可对照右侧原文核对，未知字段留空即可。</p><dl class="ai-grid">${rows}</dl>${actions}<p class="help-line">下方表单已按以上内容预填，可以直接修改。点击“确认”或“确认添加”后才会保存，原 PDF 始终保留。</p></section>`;
  }
  const warning = String(u.metadata?.ai_warning ?? '').trim();
  const reason = !state.status.ai_configured ? '当前没有配置可用的 AI 服务，资料身份由文件名预填。'
    : warning ? warning
    : !u.has_text ? '这份 PDF 没有可识别文字，无法进行 AI 提取。'
    : '本次没有得到 AI 识别结果，请手动填写资料信息。';
  const fallbackActions = '<div class="ai-actions"><button class="secondary" data-action="ai-edit">手动填写</button></div>';
  return `<section class="ai-panel fallback" id="ai-panel" data-mode="fallback"><div class="ai-head"><span class="ai-badge warn">${state.status.ai_configured ? 'AI 识别未完成' : 'AI 尚未连接'}</span></div><h3>资料信息（文件名预填）</h3><p class="help-line">${escapeHTML(reason)}</p><dl class="ai-grid">${rows}</dl>${fallbackActions}</section>`;
}
function applyAiSuggestions(form, u) {
  const applied = {};
  for (const [key] of AI_FIELDS) {
    const input = form.elements[key];
    if (!input) continue;
    let value = String(u.suggested[key] ?? '').trim();
    if (key === 'published_at') value = normalizeDate(value);
    input.value = value;
    applied[key] = value;
  }
  state.upload.applied = applied;
}
function syncAiPanel() {
  const panel = $('#ai-panel'); const form = $('#confirm-form');
  if (!panel || !form || panel.dataset.mode !== 'ai') return;
  const applied = state.upload?.applied || {};
  let edited = false;
  for (const [key] of AI_FIELDS) {
    const item = panel.querySelector(`[data-ai-item="${key}"]`);
    if (!item) continue;
    const changed = String(form.elements[key]?.value ?? '').trim() !== String(applied[key] ?? '');
    item.classList.toggle('ai-changed', changed);
    edited = edited || changed;
  }
  const badge = panel.querySelector('.ai-badge');
  if (badge) badge.textContent = edited ? 'AI 识别结果 · 已按你的修改调整' : 'AI 识别结果 · 待你确认';
}
function toggleNewCompetition() {
  const existing = Boolean($('#competition-select').value);
  for (const name of ['name','edition']) { const input = $('#confirm-form').elements[name]; input.required = !existing; input.disabled = existing; input.parentElement.hidden = existing; }
}
async function selectCompetition(id) {
  const current = state.competitions.find(c=>c.id===id);
  if (!current) throw new Error('比赛不存在。');
  state.documents = await api(`/api/documents?competition_id=${encodeURIComponent(id)}`);
  state.current = current; state.tab = state.pendingQuery ? 'ask' : 'notices'; closeSource(); competitionPage();
}
function competitionPage() {
  setPage('library',`比赛库 / ${state.current.name}`);
  $('#main').innerHTML = `<p class="eyebrow">COMPETITION SPACE · ${escapeHTML(state.current.edition)}</p><div class="subtitle-row"><h1>${escapeHTML(state.current.name)}</h1><button class="primary" data-action="upload">＋ 添加资料</button></div><p class="muted">${state.documents.length} 份资料 · 查询范围：当前比赛全部已添加资料</p><div class="tabs"><button class="tab ${state.tab==='notices'?'active':''}" data-action="notices">全部通知</button><button class="tab ${state.tab==='ask'?'active':''}" data-action="ask">问答与原文</button></div><div id="space-content"></div>`;
  if (state.tab === 'notices') renderNotices(); else renderAsk();
}
function renderNotices() {
  const dates = state.documents.map(d=>d.published_at).filter(Boolean).sort(); const latest = dates.at(-1);
  $('#space-content').innerHTML = state.documents.map(d=>`<article class="card document"><div class="doc-info"><h3>${escapeHTML(d.title)}</h3>${d.published_at && d.published_at===latest ? '<span class="tag">最近发布</span>' : ''}<p>发布：${escapeHTML(d.published_at || '未确认')}　上传：${escapeHTML(new Date(d.uploaded_at).toLocaleString('zh-CN'))}</p><p>来源：用户上传 · ${escapeHTML(d.uploader)} / ${d.role==='teacher'?'老师':'学生'}（自行选择）</p><p>${d.page_count} 页 · ${d.has_text?'可检索原文':'未提取到文字，需要 OCR'} · ${d.chunk_count||0} 块 · 赛道备注：${escapeHTML(d.track||'未确认')}</p><p>知识库：${escapeHTML(kbLabel((d.metadata||{}).knowledge_base))}</p></div><button class="secondary" data-action="source" data-id="${d.id}">查看原文 ↗</button> <button class="text-btn" data-action="sync" data-id="${d.id}">重新上传到知识库</button></article>`).join('') || '<div class="empty">这场比赛还没有资料。</div>';
}
function renderAsk() {
  $('#space-content').innerHTML = `<div class="notice">${state.status.answer_mode==='agent' ? '回答由已发布的智能体给出（与扫码体验同一个）；下面同时列出本地原文片段，点引用可查看对应页。' : state.status.ai_configured ? '回答基于当前比赛资料。点击引用可以查看对应页，OCR 文字请核对原图。' : 'AI 尚未连接，当前展示原文检索结果，不生成比赛结论。未检索到不代表资料里没有规定。'}</div><div class="question-chips"><button data-action="question" data-query="一个人能参加吗？">一个人能参加吗？</button><button data-action="question" data-query="需要提交什么材料？">需要提交什么材料？</button><button data-action="question" data-query="报名条件是什么？">报名条件是什么？</button></div><form id="ask-form" class="chat-input"><input name="query" class="search-input" required maxlength="1000" value="${escapeHTML(state.pendingQuery)}" placeholder="输入问题或关键词，例如：人数、提交材料" aria-label="检索问题"><button class="primary">${state.status.ai_configured?'提问':'查原文'}</button></form><div id="answers">${state.conversations[state.current.id] || '<div class="empty"><div class="symbol">❝</div><h3>让依据就在手边</h3><p>结果会显示文件名和 PDF 页码，点击即可在旁边打开原文。</p></div>'}</div>`;
}
async function navigate(action) {
  if (state.busy) { toast('正在处理，请稍候。'); return; }
  if (state.upload) {
    if (!window.confirm('离开会取消本次提交，需要重新上传。确定离开？')) return;
    await api('/api/cancel',{upload_id:state.upload.upload_id}); state.upload = null;
  }
  if (action === 'home') { state.current = null; state.pendingQuery=''; home(); }
  if (action === 'library') { state.current = null; library(); }
  if (action === 'upload') uploadPage();
}
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]'); if (!button) return;
  const action = button.dataset.action;
  try {
    if(state.busy && !['close-source','source'].includes(action)){toast('正在处理，请稍候。');return;}
    if (['home','library','upload'].includes(action)) return await navigate(action);
    if (action==='identity') return showIdentity();
    if (action==='close-identity') {state.resumeUpload=false;return $('#identity-dialog').close();}
    if (action==='close-source') return closeSource();
    if (action==='competition') return await selectCompetition(button.dataset.id);
    if (action==='preview-upload') return openSource(state.upload.preview_url,state.upload.filename);
    if (action==='question') {$('#ask-form').elements.query.value=button.dataset.query;$('#ask-form').requestSubmit();return;}
    if (action==='example') {
      if(state.busy)return;
      state.busy=true;$('#main').innerHTML='<div class="loading">正在读取慧科杯通知…<p class="muted">识别扫描页，保留原始文件和页码。</p></div>';
      try{state.upload=await api('/api/example',{});confirmPage();}catch(e){uploadPage();throw e;}finally{state.busy=false;}
      return;
    }
    if (action==='ai-accept') {
      const form=$('#confirm-form'); if(!form) return;
      const existing=Boolean(form.elements.competition_id.value);
      if(!existing && !(form.elements.name.value.trim() && form.elements.edition.value.trim())) return toast('AI 未能确认比赛名称或届次，请先点“修改”手动补充。');
      if(!form.elements.title.value.trim()) return toast('请先补充资料标题，再确认添加。');
      return form.requestSubmit();
    }
    if (action==='ai-edit') {
      const form=$('#confirm-form'); if(!form) return;
      form.classList.add('editing'); $('#ai-panel')?.classList.add('editing');
      form.scrollIntoView({block:'start',behavior:'smooth'});
      (form.elements.competition_id.value ? form.elements.title : form.elements.name)?.focus({preventScroll:true});
      return toast('可以直接修改下面的信息；点击“确认添加”后才会保存。');
    }
    if (action==='cancel-upload') { await api('/api/cancel',{upload_id:state.upload.upload_id}); state.upload=null; closeSource(); uploadPage(); }
    if (action==='notices' || action==='ask') { state.tab=action; competitionPage(); }
    if (action==='sync') {
      const id=button.dataset.id; button.disabled=true; button.textContent='正在上传…';
      const result=await api(`/api/documents/${id}/sync`,{});
      state.documents=await api(`/api/documents?competition_id=${encodeURIComponent(state.current.id)}`);
      competitionPage();
      toast(kbLabel(result.knowledge_base));
    }
    if (action==='source') { const d=state.documents.find(d=>d.id===button.dataset.id); openSource(`/api/documents/${d.id}/file`,d.title,Number(button.dataset.page || 1)); }
  } catch(error) { toast(error.message); }
});
document.addEventListener('submit', async event => {
  event.preventDefault(); if(state.busy)return; const form=event.target; const data=Object.fromEntries(new FormData(form));
  const submit=form.querySelector('button:not([type="button"])');
  try {
    if (form.id==='identity-form') { if (!data.name.trim()) return toast('请输入姓名。'); identity={name:data.name.trim(),role:data.role}; localStorage.setItem('huike-identity',JSON.stringify(identity)); updateIdentity(); $('#identity-dialog').close(); if(state.resumeUpload){state.resumeUpload=false;uploadPage();}else if(state.page==='home')home(); toast('演示身份已保存，可以添加资料。'); return; }
    if (form.id==='home-question') { state.pendingQuery=data.query.trim(); library(); return; }
    if (submit) submit.disabled=true;
    if (form.id==='confirm-form') {
      state.busy=true;
      const result=await api('/api/confirm',{...data, upload_id:state.upload.upload_id,uploader:identity.name,role:identity.role});
      state.upload=null; state.competitions=await api('/api/competitions'); state.pendingQuery=''; await selectCompetition(result.competition_id);
      toast(result.duplicate?'这场比赛已有相同文件，已打开已有资料。':kbToast(result.knowledge_base));
    }
    if (form.id==='ask-form') {
      state.busy=true;
      const cid=state.current.id;
      state.pendingQuery=data.query;
      $('#answers').innerHTML='<div class="loading">正在查找答案…</div>';
      const result=await api('/api/ask',{competition_id:cid,query:data.query});
      const hits=result.citations?.length ? result.citations : result.hits;
      const mode=result.mode||'';
      const answerTag=mode==='agent'?'智能体回答 · 请核对依据':mode==='agent_failed'?'智能体暂时没有回答':'AI 回答 · 请核对依据';
      const notice=mode==='agent_failed'?`<p class="error">智能体没有回答成功：${escapeHTML(result.agent_error||'未知原因')}</p>`:'';
      const missing=/(没有找到|未找到|没有提及|未提及|资料中没有|无法确认|无法据此判断)/.test(result.answer||'');
      const quotes=(!missing && (result.quotes||[]).length)?`<article class="card evidence"><span class="tag">智能体引用的知识库文件</span><ul class="quote-list">${result.quotes.map(q=>`<li>[${escapeHTML(String(q.index))}] <a href="${escapeHTML(q.url||'#')}" target="_blank" rel="noopener noreferrer">${escapeHTML(q.name||'未命名')}</a></li>`).join('')}</ul></article>`:'';
      const quotesNote=(missing && (result.quotes||[]).length)?'<p class="help-line">这次回答没有采用任何引用（平台检索到的文件与问题无关，已隐藏）。</p>':'';
      const html=`<p class="query-label">${escapeHTML(data.query)}</p>${notice}`+(result.answer?`<article class="card ai-answer"><span class="tag">${answerTag}</span><p>${escapeHTML(result.answer).replaceAll('\n','<br>')}</p>${mode==='agent'&&result.elapsed?`<p class="help-line">用时 ${result.elapsed} 秒 · 依据来自平台知识库</p>`:''}</article>`:'')+quotesNote+quotes+(hits.length ? hits.map(h=>`<article class="card evidence"><span class="tag">${h.ocr?'扫描文字识别 · 请对照原图':'PDF 原文片段'}</span><pre>${escapeHTML(h.text)}</pre><div class="evidence-footer"><span>${escapeHTML(h.title)} · PDF 第 ${h.page}${h.page_end>h.page?'-'+h.page_end:''} 页${h.chunk?' · 第 '+escapeHTML(h.chunk)+' 块':''}${h.heading?' 「'+escapeHTML(h.heading)+'」':''}<br>赛道备注：${escapeHTML(h.track||'未确认')}</span><button class="secondary" data-action="source" data-id="${h.document_id}" data-page="${h.page}">查看原文 ↗</button></div></article>`).join('') : '<div class="empty"><h3>未检索到相关片段</h3><p>可以换用更直接的关键词。未识别的扫描页不能检索；未命中不能证明原文没有相关要求。</p></div>');
      state.conversations[cid]=(state.conversations[cid]||'')+html;
      if(state.current?.id===cid && $('#answers'))$('#answers').innerHTML=state.conversations[cid];
    }
  } catch(error) { toast(error.message); if(form.id==='ask-form') $('#answers').innerHTML=`<p class="error">${escapeHTML(error.message)}</p>`; }
  finally { state.busy=false; if(submit) submit.disabled=false; }
});
document.addEventListener('change',event=>{ if(event.target.id==='pdf-input') uploadFile(event.target.files[0]); if(event.target.id==='competition-select') toggleNewCompetition(); });
document.addEventListener('input',event=>{ if(event.target.id==='library-filter') { const q=event.target.value.toLowerCase(); $('#competition-cards').innerHTML=competitionCards(state.competitions.filter(c=>(c.name+c.edition).toLowerCase().includes(q))); } if(event.target.closest('#confirm-form')) syncAiPanel(); });
document.addEventListener('dragover',event=>{ const zone=event.target.closest('#drop-zone'); if(zone){event.preventDefault();zone.classList.add('drag');} });
document.addEventListener('dragleave',event=>{event.target.closest('#drop-zone')?.classList.remove('drag');});
document.addEventListener('drop',event=>{const zone=event.target.closest('#drop-zone');if(zone){event.preventDefault();zone.classList.remove('drag');uploadFile(event.dataTransfer.files[0]);}});
window.addEventListener('beforeunload',event=>{if(state.upload || state.busy){event.preventDefault();event.returnValue='';}});
document.querySelector('.brand').addEventListener('click',event=>{event.preventDefault();navigate('home').catch(e=>toast(e.message));});
updateIdentity();
Promise.all([api('/api/competitions'),api('/api/status')]).then(([items,status])=>{state.competitions=items;state.status=status;$('#service-label').textContent=status.ai_configured?'AI 接口已配置':'AI 尚未连接 · 可查原文';home();}).catch(error=>{$('#main').innerHTML=`<div class="error">服务连接失败：${escapeHTML(error.message)}。请确认启动窗口仍然打开，再刷新页面。</div>`;});
