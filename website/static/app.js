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
const state = { competitions: [], current: null, documents: [], upload: null, docOpen: false, pendingQuery: '', page: 'home', busy: false, status: {}, conversations: {} };
// 登录后由 /api/auth/session 填进来（不再用 localStorage 自报身份）。
let identity = null;
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 6500); }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const value = await response.json();
  if (response.status === 401) { identity = null; updateIdentity(); showLogin(value.error || '请先登录。'); }
  if (!response.ok) throw new Error(value.error || '请求失败，请重试。');
  return value;
}
function updateIdentity() { $('#identity-label').textContent = identity?.name || '未登录'; }
function authMode(mode) {
  // 注册与登录分开放：同一张卡片，两个 tab 切换（不做路由）。
  const register = mode === 'register';
  $('#login-form').hidden = register;
  $('#register-form').hidden = !register;
  $('#tab-login').classList.toggle('active', !register);
  $('#tab-register').classList.toggle('active', register);
  $('#login-error').hidden = true;
  $('#register-error').hidden = true;
}
function showLogin(message='', mode='login') {
  authMode(mode);
  const error = mode === 'register' ? $('#register-error') : $('#login-error');
  error.textContent = message; error.hidden = !message;
  $('#login-page').hidden = false;
  $(mode === 'register' ? '#register-form' : '#login-form').elements.account.focus();
}
function hideLogin() { $('#login-page').hidden=true; }
// 改了 .py 没重启服务时，页面会拿到"新前端 + 旧后端"的假象（例如招募详情明明有画像却写"还没有画像"）。
// 服务端在 /api/status 里报出自己比磁盘上的代码旧，这里就把话说明白，别让人以为是数据没存上。
function checkCodeStale(status){
  const stale = (status && status.code_stale) || [];
  let box = $('#code-stale');
  if (!stale.length) { box?.remove(); return; }
  if (!box) { box = document.createElement('div'); box.id = 'code-stale'; box.className = 'code-stale'; document.body.append(box); }
  box.innerHTML = `<strong>服务端还在跑改动前的代码</strong>（${escapeHTML(stale.join('、'))} 改过但没重启），页面上的内容可能对不上，不是数据丢了。先在任务管理器结束本项目 <code>server.py</code> 的 Python 进程（只结束这一个），再重新运行 start.cmd，然后刷新本页。`;
}
function closeSource() { $('#source-panel').hidden = true; $('#source-frame').src = 'about:blank'; document.body.classList.remove('source-open'); }
function openSource(url, title, page = 1) { $('#source-title').textContent = title; $('#source-frame').src = `/viewer.html?file=${encodeURIComponent(url)}&page=${page}`; $('#source-link').href = `${url}#page=${page}`; $('#source-panel').hidden = false; document.body.classList.add('source-open'); }
// 对话区一律停在最新一条：内嵌列表滚自己的，比赛页把最新一轮的结尾滚到输入框上面（不用从头往下滑）。
function scrollBoxToBottom(selector){ const box=$(selector); if(box) box.scrollTop=box.scrollHeight; }
function scrollLatestTurn(){ const turn=$('#answers')?.lastElementChild; if(!turn){ window.scrollTo(0,document.documentElement.scrollHeight); return; } const pad=($('#ask-form')?.offsetHeight||70)+28; window.scrollBy(0,turn.getBoundingClientRect().bottom+pad-window.innerHeight); }
// 提问一律用同一个「我」的气泡（比赛页和各处对话共用），回答那边颜色完全不同，一眼分得开。
function questionHTML(text){ return `<div class="chat-message chat-user chat-question"><span class="chat-role">我</span><p class="preserve-lines">${escapeHTML(text)}</p></div>`; }

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

function kbFromDocument(d){
  // 知识库状态现在存在资料表的 kb_* 列里（不再塞进 metadata）。
  return d.kb_status ? {status:d.kb_status, message:d.kb_status_desc||'',
                        filename:d.kb_file_name||'', chunk_count:d.chunk_count} : null;
}
function quoteKey(q){ return `${q.document_id||q.title||q.name||'未命名资料'}#${q.page||''}`; }
function uniqueQuotes(quotes){
  // 平台可能对同一份资料给出多条引用（同一个文件、同一个页码）；只留第一条，角标编号照平台原样保留。
  const seen=new Set(), kept=[];
  for(const q of quotes){ const key=quoteKey(q); if(seen.has(key)) continue; seen.add(key); kept.push(q); }
  return kept;
}
function quoteItem(q){
  // 引用优先指回本地资料：能唯一定位到原文片段才给页码，否则只到“查看这份资料”。
  const label=`${escapeHTML(q.title||q.name||'未命名资料')}${q.page?` · PDF 第 ${q.page} 页`:' · 查看这份资料'}`;
  return `<li>[${escapeHTML(String(q.index))}] <button class="text-btn" data-action="source" data-id="${q.document_id}" data-page="${q.page||1}">${label} ↗</button></li>`;
}
function quoteCards(result){
  // 角标是平台给的：回答实际用到的算“回答依据”，只是检索到的另列，不混为一谈。
  const quotes=uniqueQuotes(result.quotes||[]);
  const local=quotes.filter(q=>q.document_id);
  const remote=quotes.filter(q=>!q.document_id);   // 没对应上本地资料：只留一行小字，不再单独占一块
  const used=local.filter(q=>q.used);
  const rest=local.filter(q=>!q.used);
  const cards=[];
  if(used.length) cards.push(`<div class="evidence-group"><span class="evidence-label">回答依据 · 智能体实际引用</span><ul class="quote-list">${used.map(quoteItem).join('')}</ul></div>`);
  if(rest.length) cards.push(`<div class="evidence-group"><span class="evidence-label">检索到的资料 · 回答未使用</span><ul class="quote-list">${rest.map(quoteItem).join('')}</ul></div>`);
  if(remote.length) cards.push(`<p class="help-line">另有知识库文件：${remote.map(q=>`${escapeHTML(q.name||'未命名资料')}${q.url?` <a href="${escapeHTML(q.url)}" target="_blank" rel="noopener noreferrer">打开 ↗</a>`:''}`).join('　·　')}（没有对应到本地资料）</p>`);
  return cards.join('');
}
function evidenceBlock(result,hits,mode){
  // 回答之后只留这一块「依据与原文」：引用、提示、本地原文片段都收进来，原文片段排在最下面。
  const notes=quoteNote(result), groups=quoteCards(result), local=localEvidence(result,hits);
  const quoted=(result.quotes||[]).length;
  const meta=[mode==='agent'&&result.elapsed?`用时 ${result.elapsed} 秒`:'',quoted?(mode==='grounded'?'依据来自当前比赛原文':'依据来自平台知识库'):(mode==='keyword_evidence'?'依据来自本地资料检索':'')].filter(Boolean).join(' · ');
  if(!notes && !groups && !local) return '';
  return `<article class="card evidence-block"><div class="evidence-head"><span class="tag">依据与原文</span>${meta?`<span class="help-line">${escapeHTML(meta)}</span>`:''}</div>${notes}${groups}${local}</article>`;
}
function quoteNote(result){
  const quotes=result.quotes||[];
  if(!quotes.length) return result.answer?'<p class="help-line">本次回答未标注可核对引用，请谨慎采纳。</p>':'';
  if(!quotes.some(q=>q.used)) return '<p class="help-line">平台检索到了资料，但这次回答没有标注用到了哪一条，不能当作有据可查。</p>';
  return result.continued?'<p class="help-line">接着上文回答（同一场比赛的连续追问）。</p>':'';
}
function localEvidence(result,hits){
  // 本地原文片段：平台已经给了引用就收起来（要点一下才展开），没有引用时它才是唯一依据。
  if(!hits.length) return result.answer?'':'<div class="empty"><h3>未检索到相关片段</h3><p>可以换用更直接的关键词。未识别的扫描页不能检索；未命中不能证明原文没有相关要求。</p></div>';
  const cards=hits.map(h=>`<article class="card evidence"><span class="tag">${h.ocr?'扫描文字识别 · 请对照原图':'原文片段'}</span><pre>${escapeHTML(h.text)}</pre><div class="evidence-footer"><span>${escapeHTML(h.title)} · ${h.location_kind === 'section' ? '正文' : (h.location_kind === 'image' ? '图片 ' : '第 ') + h.page + (h.page_end>h.page?'-'+h.page_end:'') + (h.location_kind === 'image' ? '' : ' 页')}${h.chunk?' · 第 '+escapeHTML(h.chunk)+' 块':''}${h.heading?' 「'+escapeHTML(h.heading)+'」':''}</span><button class="secondary" data-action="source" data-id="${h.document_id}" data-page="${h.page}">查看原文 ↗</button></div></article>`).join('');
  const open=(result.quotes||[]).length?'':' open';
  return `<details class="evidence-fold"${open}><summary>本地原文片段（${hits.length} 块）</summary>${cards}</details>`;
}
async function newSession(){
  const cid=state.current?.id; if(!cid) return;
  await api('/api/session/reset',{competition_id:cid});
  state.conversations[cid]=''; state.pendingQuery=''; renderAsk();
  toast('已开始新会话，接下来的提问不带上文。');
}

function setPage(page, breadcrumb) { state.page = page; $('#breadcrumb').textContent = breadcrumb; document.querySelectorAll('.nav').forEach(n => n.classList.toggle('active', n.dataset.action === page)); }
function competitionCards(items) {
  if (!items.length) return `<div class="empty"><div class="symbol">▤</div><h3>从第一份比赛通知开始</h3><p>上传 PDF、图片或 Word，将资料整理到对应比赛，之后随时查看原文件与原文片段。</p><button class="primary" data-action="upload">＋ 添加比赛资料</button></div>`;
  return `<div class="cards">${items.map(c => `<button class="card competition" data-action="competition" data-id="${escapeHTML(c.id)}"><span class="tag">${escapeHTML(c.edition)} 届</span><h3>${escapeHTML(c.name)}</h3><p class="muted">${c.document_count} 份资料</p><span class="text-btn">进入比赛空间 →</span></button>`).join('')}</div>`;
}
function home() {
  closeSource(); setPage('home','工作空间 / 首页');
  $('#main').innerHTML = `<div class="welcome-row"><span>你好，${escapeHTML(identity?.name || '参赛者')}。</span><span>从一个想法，到一次出发。</span></div><section class="hero"><div class="hero-copy"><p class="eyebrow">YOUR NEXT CHAPTER</p><h1>下一场比赛，<br>从<span class="accent">看懂通知</span>开始。</h1><p class="muted">整理比赛资料，找到问题的依据。<br>少一点来回翻找，多一点准备的把握。</p><button class="primary" data-action="upload">＋ 上传比赛通知</button><button class="text-btn" data-action="library">浏览比赛库 ↗</button></div><div class="hero-art" aria-hidden="true"><div class="orbit"></div><div class="paper-back"></div><div class="paper-front"><span class="paper-tag">COMPETITION BRIEF</span><div class="paper-heading">每个问题<br>都有迹可循。</div><div class="paper-line"></div><div class="paper-line short"></div><div class="paper-highlight">✓ 找到原文依据</div><div class="paper-line"></div><div class="paper-line short"></div><span class="paper-page">01 / 06</span></div><div class="float-note">❝ <span>原文，就在旁边。</span></div><span class="art-star">✳</span></div></section><form id="home-question" class="hero-search"><span class="search-spark">✧</span><input name="query" autocomplete="off" required maxlength="1000" placeholder="想了解什么？例如：一个人能参加吗？" aria-label="输入你的问题"><button class="primary">选择比赛 →</button></form><p class="help-line">${state.status.ai_configured ? 'AI 接口已配置 · 选择比赛后，基于资料回答。' : '当前可查原文 · AI 回答待连接。'} 不会默认替你选择比赛。</p><div class="section-head"><h2>比赛资料库 <span class="count">${state.competitions.length}</span></h2><button class="text-btn" data-action="library">查看全部 →</button></div><div id="competition-cards">${competitionCards(state.competitions)}</div><div class="benefits"><div><span>01</span><strong>上传一份资料</strong><p>原文件完整保留</p></div><div><span>02</span><strong>确认所属比赛</strong><p>新旧通知有序整理</p></div><div><span>03</span><strong>带着依据了解规则</strong><p>点击引用，直达原文</p></div></div>`;
}
function library() {
  closeSource(); setPage('library','工作空间 / 比赛库');
  $('#main').innerHTML = `<div class="section-head"><div><p class="eyebrow">COMPETITIONS</p><h1>比赛库</h1></div><button class="primary" data-action="upload">＋ 添加资料</button></div>${state.pendingQuery ? `<p class="query-label">你的问题：${escapeHTML(state.pendingQuery)}<br>请选择要查询的比赛。</p>` : ''}<input id="library-filter" class="search-input library-search" autocomplete="off" placeholder="搜索比赛名称或届次" aria-label="搜索比赛"><div id="competition-cards">${competitionCards(state.competitions)}</div>`;
}
function uploadPage() {
  if (!identity) { showLogin(); return; }
  closeSource(); setPage('upload','工作空间 / 添加比赛资料');
  $('#main').innerHTML = `<p class="eyebrow">ADD DOCUMENT</p><h1>把比赛通知，放到这里。</h1><p class="muted">保留原始文件，确认所属比赛后，加入资料列表。</p><div class="steps"><span>01 上传资料</span><span>02 核对信息</span><span>03 加入比赛</span></div><label class="upload-box" id="drop-zone"><div class="upload-icon">⇧</div><h2>点击选择，或拖入比赛资料</h2><p class="muted">PDF / Word / PNG、JPG、WebP、BMP、TIFF / TXT、MD · 每份最多 20 MB</p><input id="pdf-input" type="file" accept=".pdf,.docx,.doc,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.txt,.md"></label><p class="help-line">上传者：${escapeHTML(identity.name)}</p><div class="notice">${state.status.ai_configured ? 'AI 会整理资料信息，提交前请对照右侧原文确认。' : 'AI 尚未连接：资料信息由文件名预填，请手动核对。'} ${state.status.ocr_available ? '扫描页会自动进行本地文字识别，请以原图为准。' : '扫描页需要文字识别后才能检索。'}</div>`;
}
async function uploadFile(file) {
  if (!file || state.busy) return;
  if (file.size > 20 * 1024 * 1024) { toast('文件不能超过 20 MB。'); return; }
  state.busy = true;
  $('#main').innerHTML = `<div class="loading">正在读取资料并记录来源位置…<p class="muted">扫描文件还需要识别图片中的文字${state.status.ai_configured ? '；AI 正在提取资料信息，可能需要一会儿，请勿关闭页面' : ''}。</p></div>`;
  try {
    const content = await new Promise((resolve,reject) => { const r = new FileReader(); r.onload = () => resolve(r.result.split(',')[1]); r.onerror = () => reject(new Error('文件读取失败。')); r.readAsDataURL(file); });
    const submitted = await api('/api/upload', {filename:file.name, content, background:true});
    let result = submitted;
    if(submitted.job_id){
      sessionStorage.setItem('pending-upload-job',submitted.job_id);
      result = await waitForUpload(submitted.job_id);
      sessionStorage.removeItem('pending-upload-job');
    }
    state.upload = result;
    confirmPage();
  } catch(error) { uploadPage(); toast(error.message); }
  finally { state.busy = false; }
}
async function waitForUpload(id){
  while(true){
    const job=await api(`/api/upload-jobs/${encodeURIComponent(id)}`);
    if(job.status==='completed') return job.result;
    if(job.status==='failed') {sessionStorage.removeItem('pending-upload-job');throw new Error(job.error);}
    const label=job.stage==='recognizing' ? `正在识别：已处理 ${job.completed || 0} / ${job.total} 页`
      : job.stage==='identifying' ? '文字提取完成，正在识别比赛信息…'
      : job.stage==='queued' ? '资料已接收，等待开始识别…' : '正在读取资料并准备页面…';
    $('#main').innerHTML=`<div class="loading"><h2>${escapeHTML(label)}</h2><p class="muted">长文件会自动分批处理，不需要手动拆分。完成后进入资料确认。</p></div>`;
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
}
function confirmPage() {
  const u = state.upload;
  $('#main').innerHTML = `<p class="eyebrow">CHECK DOCUMENT</p><h1>确认这份资料属于哪里</h1><p class="muted">只需核对资料身份，不必逐条审核比赛规则。</p><div class="notice" id="upload-notice"></div>${aiResultPanel(u)}<form id="confirm-form" class="card"><div class="form-grid"><input type="hidden" name="competition_id" id="competition-select"><input type="hidden" name="create_new_confirmed" value=""><label id="new-name">比赛名称<input name="name" autocomplete="off" maxlength="200" placeholder="例如：慧科杯 AI 创新大赛" required></label><label id="new-edition">届次 / 年份<input name="edition" autocomplete="off" maxlength="40" value="${escapeHTML(u.suggested.edition)}" placeholder="例如：2026" required></label><label class="full">资料标题<input name="title" autocomplete="off" required maxlength="300" value="${escapeHTML(u.suggested.title)}"></label><label>发布主体<input name="publisher" autocomplete="off" maxlength="120" placeholder="未识别，可留空"></label><label class="full">适用赛道（如整份资料仅针对某赛道）<input name="track" autocomplete="off" maxlength="200" placeholder="没有单独限制可留空"><span class="help-line">只有整份资料都属于同一赛道才填写；混合通知留空，系统按明确的小节标题识别。</span></label></div><div class="actions"><button type="button" class="secondary" data-action="cancel-upload">取消提交</button><button class="primary">确认添加</button></div></form><button class="text-btn" data-action="preview-upload">查看上传的原文件 ↗</button>`;
  const form=$('#confirm-form');
  form.insertAdjacentHTML('afterbegin','<section id="document-update-panel" class="document-update-panel" aria-live="polite" hidden></section>');
  DocumentUpdates.reset();
  const candidates=u.competition_matches||[];
  form.querySelector('.form-grid').insertAdjacentHTML('beforebegin',
    `<section class="notice" id="competition-match-note"><h3>${candidates.length ? '发现可能相同的比赛，请你确认' : !u.has_text ? '尚未判断所属比赛' : '未发现相似比赛'}</h3>
    ${candidates.length ? `<p>这份资料可能属于下面的已有比赛。是否加入其中？确认比赛后，再选择新增或更新旧规定。</p>
      ${candidates.map(c=>`<article class="card"><strong>${escapeHTML(c.name)}</strong><p>${escapeHTML(c.edition)} · 已有 ${Number(state.competitions.find(item=>item.id===c.id)?.document_count||0)} 份资料</p><button type="button" class="text-btn" data-action="inspect-candidate" data-id="${escapeHTML(c.id)}">查看已有比赛及资料 ↗</button><div class="candidate-documents" hidden></div><button type="button" class="primary" data-action="associate-existing" data-id="${escapeHTML(c.id)}">是，加入这场比赛</button></article>`).join('')}
      <button type="button" class="secondary" data-action="associate-new">${candidates.length===1?'不是，这是另一场比赛':'都不是，这是另一场比赛'}</button>`
      : `<p>${!u.has_text ? '未提取到可用文字，不能据此判断这是一场新比赛。建议先重新识别资料。' : '请核对比赛名称，确认后再创建比赛。'}</p><button type="button" class="secondary" data-action="associate-new">确认创建新比赛</button>`}
    </section>`);
  $('#ai-panel').before($('#competition-match-note'));
  $('#competition-match-note').insertAdjacentHTML('afterend','<section id="association-decision" class="association-result" role="status" aria-live="polite" tabindex="-1" hidden></section>');
  applyAiSuggestions(form,u);
  const notices=[];
  if(u.extraction_method==='ai') notices.push('AI 已整理资料信息。请对照原文确认，未知字段可留空。');
  else if(!state.status.ai_configured) notices.push('AI 尚未连接：以下信息由文件名预填，请手动核对比赛归属。');
  else if(!u.has_text) notices.push('这份资料 未提取到可用文字，无法进行 AI 识别，请手动填写资料信息。');
  else notices.push('AI 未能完成识别：以下信息由文件名预填，请手动填写并核对。');
  if(!u.has_text) notices.push('你仍可保存和预览，暂时不能检索其正文。');
  if(u.metadata?.text_extraction_method?.includes('deepseek_vision')) notices.push('图片内容由 DeepSeek 识别，正文和表格保留来源位置；请重点核对日期、人数和金额。');
  if(u.metadata?.ocr_pages?.length) notices.push(`已识别 ${u.metadata.ocr_pages.length} 个图片或扫描页面，识别文字可能存在误差。`);
  if(u.metadata?.unreadable_pages?.length) notices.push(`${u.metadata?.location_kind === 'page' ? '第 ' + u.metadata.unreadable_pages.join('、') + ' 页' : '部分内容'}未识别到文字，不能用于回答。`);
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
  const actions = '<div class="ai-actions"><button class="primary" data-action="ai-accept">确认信息并添加资料</button><button class="secondary" data-action="ai-edit">修改资料信息</button></div>';
  if (u.extraction_method === 'ai') {
    return `<section class="ai-panel" id="ai-panel" data-mode="ai"><div class="ai-head"><span class="ai-badge">AI 识别结果 · 待你确认</span></div><h3>AI 识别结果</h3><p class="help-line">AI 只从这份资料（${u.metadata?.location_kind === 'page' ? u.page_count + ' 页' : '按原文顺序提取'}）中提取信息，不会自动创建比赛。可对照右侧原文核对，未知字段留空即可。</p><dl class="ai-grid">${rows}</dl>${actions}<p class="help-line">下方表单已按以上内容预填，可以直接修改。点击“确认”或“确认添加”后才会保存，原文件始终保留。</p></section>`;
  }
  const warning = String(u.metadata?.ai_warning ?? '').trim();
  const reason = !state.status.ai_configured ? '当前没有配置可用的 AI 服务，资料身份由文件名预填。'
    : warning ? warning
    : !u.has_text ? '这份资料 没有可识别文字，无法进行 AI 提取。'
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
  const isNew = $('#competition-select').value === '__new__';
  for (const name of ['name','edition']) { const input = $('#confirm-form').elements[name]; input.required = isNew; input.disabled = !isNew; input.parentElement.hidden = !isNew; }
  const selected = $('#competition-select').value;
  const form = $('#confirm-form');
  form.elements.create_new_confirmed.value = isNew ? '1' : '';
  form.querySelector('.actions button.primary').disabled = !selected;
  const candidate = (state.upload?.competition_matches||[]).find(item=>item.id===selected);
  $('#competition-match-note').hidden = Boolean(selected);
  const decision = $('#association-decision');
  decision.hidden = !selected;
  decision.innerHTML = selected ? `<div><span class="association-label">✓ ${candidate ? '已选择已有比赛' : '已选择创建新比赛'}</span><h3>${escapeHTML(candidate?.name || '作为另一场比赛添加')}</h3><p><strong>${candidate?'下一步：核对新旧变化，选择新增或更新，再确认提交。':'下一步：核对下方资料信息，再确认添加。'}</strong></p><p>资料尚未提交${candidate ? '，未确认不会改变旧规定' : '，请填写比赛名称和届次'}。</p></div>${state.upload?.competition_matches?.length ? '<button type="button" class="secondary" data-action="reselect-competition">重新选择</button>' : ''}` : '';
  const accept = $('[data-action="ai-accept"]');
  if(accept) accept.disabled = !selected;
  DocumentUpdates.buttons();
}
async function selectCompetition(id) {
  const current = state.competitions.find(c=>c.id===id);
  if (!current) throw new Error('比赛不存在。');
  const summary=await api(`/api/competitions/${encodeURIComponent(id)}/summary`);
  state.documents=summary.documents; state.recruitmentCount=summary.recruitment_count;
  state.current = current; state.docOpen = false; closeSource(); competitionPage();   // 进比赛先收着，页面留给问答
}
function competitionPage() {
  setPage('library',`比赛库 / ${state.current.name}`);
  $('#main').innerHTML = `<p class="eyebrow">COMPETITION SPACE · ${escapeHTML(state.current.edition)}</p><div class="subtitle-row"><h1>${escapeHTML(state.current.name)}</h1><button class="primary" data-action="upload">＋ 添加资料</button></div><p class="muted">${state.documents.length} 份资料 · 回答只依据这场比赛当前生效的资料</p><div class="competition-actions"><button class="text-btn" data-action="recruit-plaza" data-cid="${escapeHTML(state.current.id)}">这场比赛招募（${state.recruitmentCount||0} 条）→</button></div>${DocumentUpdates.notice(state.documents)}${documentFold()}<div id="space-content"></div>`;
  renderAsk();
}
function documentFold() {
  // 资料不再单独占一栏：收成一行放在标题下面，需要翻原文或重传知识库时再点开。
  return `<details class="doc-fold"${state.docOpen?' open':''}><summary>这场比赛的全部资料（${state.documents.length} 份）· 需要原文时点开</summary><div class="doc-list">${documentCards()}</div></details>`;
}
function documentCards() {
  return DocumentUpdates.cards(state.documents);
}
function renderAsk() {
  $('#space-content').innerHTML = `<div class="question-chips"><button data-action="question" data-query="一个人能参加吗？">一个人能参加吗？</button><button data-action="question" data-query="需要提交什么材料？">需要提交什么材料？</button><button data-action="question" data-query="报名条件是什么？">报名条件是什么？</button></div>${state.status.answer_mode==='grounded'?'<div class="session-row"><span class="help-line">简短追问可接着这场比赛的上文；点“新会话”就重新开始。</span><button class="text-btn" data-action="new-session">新会话</button></div>':''}<div id="answers">${state.conversations[state.current.id] || '<div class="empty"><div class="symbol">❝</div><h3>让依据就在手边</h3><p>有可定位的引用时，可以在旁边打开原始资料；页码仅在能够核实时展示。</p></div>'}</div><form id="ask-form" class="chat-input"><input name="query" class="search-input" autocomplete="off" required maxlength="1000" value="${escapeHTML(state.pendingQuery)}" placeholder="继续提问，例如：那需要提交什么材料？" aria-label="检索问题"><button class="primary">${state.status.ai_configured?'提问':'查原文'}</button></form><div class="profile-entry"><button class="secondary" data-action="inline-profile">补充我的情况</button><button class="text-btn" data-action="profile">查看我的竞赛画像 →</button></div><div id="inline-profile"></div>`;
}
async function profile() { await profilePage(); }
async function navigate(action) {
  if (state.busy) { toast('正在处理，请稍候。'); return; }
  if (state.upload) {
    if (!window.confirm('离开会取消本次提交，需要重新上传。确定离开？')) return;
    await api('/api/cancel',{upload_id:state.upload.upload_id}); state.upload = null;
  }
  if (action === 'home') { state.current = null; state.pendingQuery=''; home(); }
  if (action === 'library') { state.current = null; library(); }
  if (action === 'upload') uploadPage();
  if (action === 'profile') await profile();
  if (action === 'recruit') await recruitmentPage();
}
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]'); if (!button) return;
  const action = button.dataset.action;
  try {
    if(state.busy && !['close-source','source'].includes(action)){toast('正在处理，请稍候。');return;}
    if (['home','library','upload','profile','recruit'].includes(action)) { captureDraft(); return await navigate(action); }
    if (await featureClick(button)) return;
    if (await DocumentUpdates.click(button)) return;
    if (action==='auth-login') return authMode('login');
    if (action==='auth-register') return authMode('register');
    if (action==='logout') { await api('/api/auth/logout',{}); location.reload(); return; }
    if (action==='close-source') return closeSource();
    if (action==='competition') return await selectCompetition(button.dataset.id);
    if (action==='inspect-candidate') {
      const candidate=(state.upload?.competition_matches||[]).find(item=>item.id===button.dataset.id);
      if(!candidate) return;
      const list=button.parentElement.querySelector('.candidate-documents');
      if(!list.hidden) {list.hidden=true;button.textContent='查看已有比赛及资料 ↗';return;}
      button.disabled=true;
      try {
        const summary=await api(`/api/competitions/${encodeURIComponent(candidate.id)}/summary`);
        if(!list.isConnected) return;
        list.innerHTML=(summary.documents||[]).map(d=>`<article class="card"><strong>${escapeHTML(d.title)}</strong><p>${escapeHTML(d.publisher||'发布主体未标注')} · ${escapeHTML(d.published_at||'发布时间未标注')}</p><button type="button" class="secondary" data-action="candidate-source" data-id="${escapeHTML(d.id)}" data-title="${escapeHTML(d.title)}">查看原文件 ↗</button></article>`).join('') || '<p>这场比赛暂时没有资料。</p>';
        list.hidden=false;
        button.textContent='收起已有资料';
      } finally {button.disabled=false;}
      return;
    }
    if (action==='candidate-source') return openSource(`/api/documents/${encodeURIComponent(button.dataset.id)}/file`,button.dataset.title,Number(button.dataset.page||1));
    if (action==='associate-existing' || action==='associate-new') {
      const cid=action==='associate-new'?'__new__':button.dataset.id;
      if(cid!=='__new__' && !(state.upload?.competition_matches||[]).some(item=>item.id===cid)) return;
      $('#competition-select').value=cid;
      toggleNewCompetition();
      $('#association-decision').focus({preventScroll:true});
      $('#association-decision').scrollIntoView({block:'start',behavior:'smooth'});
      await DocumentUpdates.load(cid);
      return;
    }
    if (action==='reselect-competition') {
      $('#competition-select').value='';
      DocumentUpdates.reset();
      toggleNewCompetition();
      $('#competition-match-note').scrollIntoView({block:'start',behavior:'smooth'});
      $('#competition-match-note [data-action="associate-existing"]')?.focus({preventScroll:true});
      return;
    }
    if (action==='preview-upload') return openSource(state.upload.preview_url,state.upload.filename,Number(button.dataset.page||1));
    if (action==='question') {$('#ask-form').elements.query.value=button.dataset.query;$('#ask-form').requestSubmit();return;}
    if (action==='new-session') return await newSession();
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
    // 通知和问答已经合在一页：资料收进折叠行，不再有 tab 切换。
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
    if (await featureSubmit(form)) return;
    if (form.id==='login-form') {
      const error=$('#login-error');
      try { await api('/api/auth/login',{account:data.account,password:data.password}); location.reload(); }
      catch(e) { error.textContent=e.message; error.hidden=false; }
      return;
    }
    if (form.id==='register-form') {
      const error=$('#register-error');
      if (data.password !== data.confirm) { error.textContent='两次输入的密码不一样，请重新输入。'; error.hidden=false; return; }
      try { await api('/api/auth/register',{account:data.account,password:data.password,name:data.name}); location.reload(); }
      catch(e) { error.textContent=e.message; error.hidden=false; }
      return;
    }
    if (form.id==='home-question') { state.pendingQuery=data.query.trim(); library(); return; }
    if (submit) submit.disabled=true;
    if (form.id==='confirm-form') {
      if (!data.competition_id) { toast('请先确认这份资料属于哪场比赛。'); return; }
      Object.assign(data,DocumentUpdates.payload());
      if (data.competition_id === '__new__') data.competition_id = '';
      state.busy=true;
      const result=await api('/api/confirm',{...data, upload_id:state.upload.upload_id,uploader:identity.name});
      let update=null;
      if(result.update_id) {
        const panel=$('#document-update-panel');
        panel.hidden=false;panel.innerHTML='<h2>资料已保存，正在准备当前问答依据…</h2><p role="status">准备完成后才切换。现在继续保留旧依据。</p>';
        state.upload=null;
        update=await DocumentUpdates.watch(result.update_id).catch(error=>({status:'syncing',message:'资料已保存，状态暂未获取：'+error.message}));
        if(update.status==='ready') state.conversations[result.competition_id]='';
      }
      state.upload=null; state.competitions=await api('/api/competitions'); state.pendingQuery=''; await selectCompetition(result.competition_id);
      toast(result.duplicate?'这场比赛已有相同文件，已打开已有资料。':update?.message || kbToast(result.knowledge_base));
    }
    if (form.id==='ask-form') {
      state.busy=true;
      const cid=state.current.id;
      state.pendingQuery=data.query;
      if (!state.conversations[cid]) $('#answers').innerHTML='';
      $('#answers').insertAdjacentHTML('beforeend',`<section id="pending-turn" class="chat-turn">${questionHTML(data.query)}<div class="loading" role="status">正在回答这个问题…</div></section>`);
      $('#pending-turn').scrollIntoView({block:'start'});
      const result=await api('/api/ask',{competition_id:cid,query:data.query});
      const hits=result.citations?.length ? result.citations : (result.hits||[]);
      const mode=result.mode||'';
      const answerTag=mode==='grounded'?'当前比赛资料回答 · 请核对依据':mode==='agent'?'智能体回答 · 请核对依据':'AI 回答 · 请核对依据';
      const notice=mode.endsWith('_failed')?`<p class="error">这次没有回答成功：${escapeHTML(result.agent_error||'未知原因')}</p>`:'';
      const evidence=evidenceBlock(result,hits,mode);
      // 依据、提示和本地原文片段都由 evidenceBlock 一起渲染，紧跟在回答之后。
      const html=questionHTML(data.query)+notice+(result.answer?`<article class="card ai-answer"><div class="answer-head"><span class="chat-role">AI</span><span class="tag">${answerTag}</span></div><p>${escapeHTML(result.answer).replaceAll('\n','<br>')}</p></article>`:'')+answerExtras(result,cid)+evidence;
      state.conversations[cid]=(state.conversations[cid]||'')+`<section class="chat-turn">${html}</section>`;
      state.pendingQuery=''; form.elements.query.value='';
      if(state.current?.id===cid && $('#answers')) {$('#answers').innerHTML=state.conversations[cid];scrollLatestTurn();}
    }
  } catch(error) { toast(error.message); if(form.id==='ask-form' && $('#answers')) {
      const cid=state.current.id;
      state.conversations[cid]=(state.conversations[cid]||'')+`<section class="chat-turn">${questionHTML(data.query)}<p class="error">${escapeHTML(error.message)}</p><p class="help-line">之前的对话已保留，可直接重试，不需要新建会话。</p></section>`;
      $('#answers').innerHTML=state.conversations[cid];
      scrollLatestTurn();
    } if(form.id==='profile-form' && $('#profile-answers')) $('#profile-answers').innerHTML=`<p class="error">${escapeHTML(error.message)}</p>`; }
  finally { state.busy=false; if(submit) submit.disabled=false; DocumentUpdates.buttons(); }
});
document.addEventListener('change',event=>{ if(event.target.id==='pdf-input') uploadFile(event.target.files[0]); if(event.target.id==='competition-select') toggleNewCompetition(); DocumentUpdates.change(event.target); });
// 折叠行是原生 details，toggle 不冒泡，所以用捕获阶段记住开合状态（重传知识库后重渲染也不会自己合上）。
document.addEventListener('toggle',event=>{ if(event.target?.classList?.contains('doc-fold')) state.docOpen=event.target.open; },true);
document.addEventListener('input',event=>{ if(event.target.id==='library-filter') { const q=event.target.value.toLowerCase(); $('#competition-cards').innerHTML=competitionCards(state.competitions.filter(c=>(c.name+c.edition).toLowerCase().includes(q))); } if(event.target.closest('#confirm-form')) syncAiPanel(); });
document.addEventListener('dragover',event=>{ const zone=event.target.closest('#drop-zone'); if(zone){event.preventDefault();zone.classList.add('drag');} });
document.addEventListener('dragleave',event=>{event.target.closest('#drop-zone')?.classList.remove('drag');});
document.addEventListener('drop',event=>{const zone=event.target.closest('#drop-zone');if(zone){event.preventDefault();zone.classList.remove('drag');uploadFile(event.dataTransfer.files[0]);}});
window.addEventListener('beforeunload',event=>{if(state.upload || state.busy){event.preventDefault();event.returnValue='';}});
document.querySelector('.brand').addEventListener('click',event=>{event.preventDefault();navigate('home').catch(e=>toast(e.message));});
updateIdentity();
api('/api/auth/session').then(async session=>{
  identity=session.user; updateIdentity();
  if(!identity){showLogin();return;}
  hideLogin();
  const [items,status]=await Promise.all([api('/api/competitions'),api('/api/status')]);
  state.competitions=items; state.status=status; checkCodeStale(status); home();
  const pending=sessionStorage.getItem('pending-upload-job');
  if(pending){
    state.busy=true;
    try{state.upload=await waitForUpload(pending);sessionStorage.removeItem('pending-upload-job');confirmPage();}
    catch(error){sessionStorage.removeItem('pending-upload-job');uploadPage();toast(error.message);}
    finally{state.busy=false;}
  }
}).catch(error=>{$('#main').innerHTML=`<div class="error">服务连接失败：${escapeHTML(error.message)}。请确认启动窗口仍然打开，再刷新页面。</div>`;});
