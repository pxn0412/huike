// Upload comparison is a proposal. Only the final form confirmation applies it.
const DocumentUpdates = (() => {
  let comparison = null;
  let loading = false;
  let generation = 0;
  const panel = () => $('#document-update-panel');

  function reset() {
    generation += 1; comparison = null; loading = false;
    if (panel()) { panel().innerHTML = ''; panel().hidden = true; }
    buttons();
  }
  function mode() { return $('#document-update-panel [name=update_mode]:checked')?.value || ''; }
  function valid() {
    if (loading) return false;
    if (!comparison?.documents?.length) return true;
    return Boolean(mode()) && (mode() !== 'partial' || Boolean($('#document-update-panel [name=selected_changes]:checked')))
      && (mode() !== 'replace' || Boolean($('#document-update-panel [name=target_document_ids]:checked')));
  }
  function buttons() {
    const form = $('#confirm-form'); if (!form) return;
    const disabled = state.busy || !form.elements.competition_id.value || !valid();
    form.querySelector('.actions button.primary').disabled = disabled;
    const accept = $('[data-action=ai-accept]'); if (accept) accept.disabled = disabled;
    form.querySelector('.actions button.primary').textContent = ['partial','replace'].includes(mode()) ? '确认更新资料' : '确认添加';
  }
  function payload() {
    if (!valid()) throw new Error('请先选择资料关系，并勾选要更新的事项或旧资料。');
    if (!comparison?.documents?.length) return {};
    return {update_mode: mode(), comparison_id: comparison.comparison_id,
      selected_changes: [...document.querySelectorAll('#document-update-panel [name=selected_changes]:checked')].map(el=>el.value),
      target_document_ids: [...document.querySelectorAll('#document-update-panel [name=target_document_ids]:checked')].map(el=>el.value)};
  }
  const source = (id,title,label,page=1) => `<button type="button" class="text-btn" data-action="candidate-source" data-id="${escapeHTML(id)}" data-title="${escapeHTML(title)}" data-page="${Number(page)||1}">${label}</button>`;
  function render() {
    const p = panel(); if (!p || !comparison) return;
    if (!comparison.documents.length) {p.hidden = true; buttons(); return;}
    p.hidden = false;
    const modes = [['add','新增资料','和原有资料一起使用，原有规定不会自动失效。'],
      ['partial','更新部分内容','勾选变化项：只更新这些旧规定，未变内容继续使用。'],
      ['replace','整份替换','选择被替换的旧资料，其全部正文退出默认问答。'],
      ['uncertain','暂不确定，保留两份','保留两方原文，回答时不得擅自认定新版有效。']];
    p.innerHTML = `<h2>这份资料与旧资料是什么关系？</h2><p>先核对下面的新旧原文，再选择处理方式。<strong>上传得晚，不代表规定更新。</strong></p>
      ${comparison.message ? `<p class="notice">${escapeHTML(comparison.message)}</p>` : ''}
      <div class="update-choices">${modes.map(([value,title,note])=>`<label class="update-choice"><input type="radio" name="update_mode" value="${value}" ${value==='partial' && !comparison.changes.length?'disabled':''}><span><strong>${title}</strong><small>${note}</small></span></label>`).join('')}</div>
      <div id="replace-targets" hidden><p class="notice"><strong>整份替换会停用所选旧资料的全部规定。</strong>仅改变日期或某个要求，请选“更新部分内容”。原文件仍保留在历史资料中。</p>
      ${comparison.documents.map(d=>`<label class="update-target"><input type="checkbox" name="target_document_ids" value="${escapeHTML(d.id)}"><strong>${escapeHTML(d.title)}</strong>${source(d.id,d.title,'查看旧原文 ↗')}</label>`).join('')}</div>
      <h3>AI 发现的候选变化（${comparison.changes.length} 项）</h3><p class="help-line">只列能够逐字回查的片段。没有列出变化，不代表没有变化；请对照原件核对范围和环节。</p>
      ${comparison.changes.map(c=>`<article class="change-card"><label class="change-heading"><input type="checkbox" name="selected_changes" value="${escapeHTML(c.id)}" disabled><strong>${escapeHTML(c.subject)}</strong></label>
      <p class="change-scope">适用范围：<strong>${escapeHTML(c.scope)}</strong> · 环节：<strong>${escapeHTML(c.stage)}</strong></p>
      <div class="change-quotes"><div><span class="tag">旧资料</span><p>${escapeHTML(c.old_title)}</p><blockquote>${escapeHTML(c.old_quote)}</blockquote>${source(c.old_document_id,c.old_title,'查看旧原文 ↗',c.old_page)}</div>
      <div><span class="tag">新上传资料</span><blockquote>${escapeHTML(c.new_quote)}</blockquote><button type="button" class="text-btn" data-action="preview-upload" data-page="${Number(c.new_page)||1}">查看新原文 ↗</button></div></div>
      <p class="help-line">${escapeHTML(c.reason)}</p></article>`).join('')}
      <p id="update-selection-note" class="update-selection-note" role="status">请选择处理方式，资料尚未提交。</p>
      <button type="button" class="text-btn" data-action="update-compare">重新对比</button>`;
    buttons();
  }
  async function load(cid) {
    reset(); if (!cid || cid==='__new__' || !state.upload) return;
    const token = generation; loading = true;
    panel().hidden = false;
    panel().innerHTML = '<h2>正在对比这场比赛的新旧资料…</h2><p role="status">对比原文中的规定、适用范围和环节。资料尚未提交。</p>';
    buttons();
    const uploadId = state.upload.upload_id;
    try {
      const result = await api('/api/uploads/compare',{upload_id:uploadId,competition_id:cid});
      if (token!==generation || state.upload?.upload_id!==uploadId || $('#competition-select')?.value!==cid) return;
      comparison = result; loading = false; render();
    } catch(error) {
      if (token!==generation || !panel()?.isConnected) return;
      loading = true;
      panel().innerHTML = `<h2>资料对比没有完成</h2><p class="error">${escapeHTML(error.message)}</p><button type="button" class="secondary" data-action="update-compare">重试对比</button>`;
      buttons();
    }
  }
  function change(target) {
    if (!target.closest('#document-update-panel')) return;
    const current = mode();
    $('#replace-targets').hidden = current!=='replace';
    for (const checkbox of document.querySelectorAll('[name=selected_changes]')) checkbox.disabled = current!=='partial';
    for (const checkbox of document.querySelectorAll('[name=target_document_ids]')) checkbox.disabled = current!=='replace';
    const notes = {add:'将新增资料，原有规定继续保留。', partial:'只切换勾选的变化项；未勾选的候选新规定暂不使用。其他未变规定继续保留。',
      replace:'所选旧资料将退出默认问答，原件保留在历史资料中。', uncertain:'暂不判断新旧优先级；有不同规定时保留两方来源。'};
    $('#update-selection-note').textContent = notes[current] || '请选择处理方式。'; buttons();
  }
  async function watch(id) {
    // Refresh recovery is through the saved document card; no endless request loop.
    for (let i=0;i<12;i++) {
      const op = await api(`/api/document-updates/${id}`);
      if (['ready','failed'].includes(op.status)) return op;
      await new Promise(resolve=>setTimeout(resolve,i<3?1200:2500));
    }
    return {status:'syncing',message:'资料已保存，知识库仍在处理；可在资料列表查看进度。'};
  }
  async function click(button) {
    if (button.dataset.action==='update-compare') {await load($('#competition-select')?.value); return true;}
    if (button.dataset.action==='update-details') {const fold=$('.doc-fold');if(fold){fold.open=true;fold.scrollIntoView({block:'start',behavior:'smooth'});}return true;}
    if (button.dataset.action==='update-refresh' || button.dataset.action==='update-retry') {
      const cid=state.current?.id;if(!cid) return true;
      button.disabled = true;
      try {
        if (button.dataset.action==='update-retry') await api(`/api/document-updates/${button.dataset.id}/retry`,{});
        const result = await api(`/api/document-updates/${button.dataset.id}`);
        const documents = await api(`/api/documents?competition_id=${encodeURIComponent(cid)}`);
        if(state.current?.id!==cid) return true;
        state.documents=documents;
        if (result.status==='ready') state.conversations[cid]='';
        competitionPage(); toast(result.message);
      } finally {if(button.isConnected) button.disabled=false;}
      return true;
    }
    return false;
  }
  function notice(documents) {
    const pending=documents.find(d=>d.update_operation && d.update_operation.status!=='ready');
    const recent=documents.filter(d=>d.update_operation?.status==='ready' && ['partial','replace'].includes(d.update_operation.mode))
      .sort((a,b)=>b.update_operation.created_at.localeCompare(a.update_operation.created_at))[0];
    if(pending) return `<div class="notice"><strong>资料更新${pending.update_operation.status==='failed'?'失败，尚未生效':'正在准备，尚未生效'}。</strong>网站继续使用旧依据。<button type="button" class="text-btn" data-action="update-details">查看更新状态 →</button></div>`;
    return recent?`<div class="association-result"><div><strong>最近确认更新：${escapeHTML(recent.title)}</strong><p>${recent.update_operation.mode==='partial'?'只更新已勾选的规定，其他未变内容继续使用。':'所选旧资料已退出默认问答，原文件保留在历史资料中。'}</p></div><button type="button" class="text-btn" data-action="update-details">查看变化与原文 →</button></div>`:'';
  }
  function cards(documents) {
    const current = documents.filter(d=>d.document_status!=='superseded' && d.document_status!=='withdrawn');
    const history = documents.filter(d=>!current.includes(d));
    const latest = current.map(d=>d.published_at).filter(Boolean).sort().at(-1);
    const latestConfirmed = current.filter(d=>d.update_operation?.status==='ready').sort((a,b)=>b.update_operation.created_at.localeCompare(a.update_operation.created_at))[0]?.id;
    function card(d) {
      const op = d.update_operation;
      const updates = d.updates || [];
      const waiting = d.document_status==='candidate';
      const historical = ['superseded','withdrawn'].includes(d.document_status);
      const label = historical ? '历史资料 · 不参与默认问答' : waiting ? '更新尚未生效' : updates.length ? '部分规定已更新' : '当前资料';
      return `<article class="card document"><div class="doc-info"><span class="tag ${waiting?'warn':''}">${label}</span><h3>${escapeHTML(d.title)}</h3>
        ${!historical && d.published_at && d.published_at===latest?'<span class="tag">最近发布（按标注时间）</span>':''}
        ${d.id===latestConfirmed?`<span class="tag">${['partial','replace'].includes(op.mode)?'最近确认更新':'最近添加'}</span>`:''}
        <p>发布：${escapeHTML(d.published_at||'未确认')}　上传：${escapeHTML(new Date(d.uploaded_at).toLocaleString('zh-CN'))}</p><p>来源：用户上传 · ${escapeHTML(d.uploader)}</p>
        <p>${d.metadata?.location_kind === 'section' ? '正文资料' : d.page_count + (d.metadata?.location_kind === 'image' ? ' 张图片' : ' 页')} · ${d.has_text?'已提取正文':'未识别到文字'} · 整份资料范围：${escapeHTML(d.track||'未单独限制')}</p>
        ${op ? `<p class="${op.status==='failed'?'error':'help-line'}"><strong>${op.status==='ready'?'已生效':op.status==='failed'?'切换失败':'正在准备依据'}</strong> · ${escapeHTML(op.message)}</p>` : `<p>知识库：${d.uses_current_evidence?'使用本比赛的当前依据':escapeHTML(kbLabel(kbFromDocument(d)))}</p>`}
        ${updates.length?`<details class="update-history"><summary>查看已确认的更新（${updates.length} 项）</summary>${updates.map(c=>`<div><strong>${escapeHTML(c.subject)}</strong><p>${escapeHTML(c.scope)} · ${escapeHTML(c.stage)} · 确认：${escapeHTML(new Date(c.confirmed_at).toLocaleString('zh-CN'))}</p><p>原规定：${escapeHTML(c.old_quote)}</p><p>更新为：<strong>${escapeHTML(c.new_quote)}</strong></p>${source(c.old_document_id,c.old_title,'查看旧原文 ↗')}${source(c.new_document_id,'更新资料','查看新原文 ↗')}</div>`).join('')}</details>`:''}
        ${waiting?'<p class="help-line">原有问答依据保持不变。新资料准备完成后才生效。</p>':''}
        <div class="actions"><button type="button" class="secondary" data-action="source" data-id="${escapeHTML(d.id)}">查看原文 ↗</button>
        ${op && op.status!=='ready'?`<button type="button" class="text-btn" data-action="${op.status==='failed' && op.can_retry?'update-retry':'update-refresh'}" data-id="${escapeHTML(op.id)}">${op.status==='failed' && op.can_retry?'重试更新':'刷新更新状态'}</button>`:!d.uses_current_evidence && !historical?`<button type="button" class="text-btn" data-action="sync" data-id="${escapeHTML(d.id)}">重新上传到知识库</button>`:''}</div></div></article>`;
    }
    return current.map(card).join('') + (history.length ? `<details class="history-documents"><summary>历史资料（${history.length} 份）· 原件保留</summary>${history.map(card).join('')}</details>`:'') || '<div class="empty">这场比赛还没有资料。</div>';
  }
  return {reset,load,buttons,payload,watch,click,change,cards,notice};
})();
