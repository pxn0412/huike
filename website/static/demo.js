// Confirmed profile is stored on the server; editable drafts live only in this tab.
const profileFields = [['major','专业'],['grade','年级'],['experiences','做过什么 / 本人负责什么'],['participation','希望参与什么'],['learning','正在学习 / 希望尝试什么']];
state.profileDraft = null;
state.profileChanges = [];
state.profileMessages = [];
state.recruitTab = 'plaza';
state.recruitFilter = '';
state.assist = {open:false, loading:false, brief:null, messages:[], draft:null, profileHits:[]};

function profileCard(payload) {
  if (!payload || !Object.values(payload).some(Boolean)) return '<p class="muted">尚未保存画像。可以先用自己的话说说经历。</p>';
  return `<div class="profile-background">${['major','grade'].filter(k=>payload[k]).map(k=>`<span>${escapeHTML(payload[k])}</span>`).join('')}</div>`+
    profileFields.slice(2).filter(([key])=>payload[key]).map(([key,label])=>`<section class="profile-section"><h3>${label}</h3><p class="preserve-lines">${escapeHTML(payload[key])}</p></section>`).join('');
}
function profileEditor(payload={}) {
  return profileFields.map(([key,label])=>`<label>${label}${['major','grade'].includes(key)?
    `<input name="${key}" autocomplete="off" maxlength="4000" value="${escapeHTML(payload[key]||'')}">`:
    `<textarea name="${key}" autocomplete="off" rows="3" maxlength="4000" placeholder="未提到的内容可以留空">${escapeHTML(payload[key]||'')}</textarea>`}</label>`).join('');
}
function captureDraft() {
  const form=$('#profile-draft-form');
  if (form) state.profileDraft=Object.fromEntries(new FormData(form));
}
function draftPanel(inline=false) {
  if (!state.profileDraft) return '';
  return `<article class="card profile-draft"><span class="tag">待本人确认 · 尚未保存</span><h2>画像草稿</h2>
    <p class="help-line">请核对并修改下面的内容；确认保存后才会进入你的画像。</p>
    <form id="profile-draft-form" data-inline="${inline?'1':'0'}" class="demo-form">${profileEditor(state.profileDraft)}
    ${state.profileChanges.length?`<div class="notice"><strong>本次修改</strong><ul>${state.profileChanges.map(c=>`<li>${escapeHTML(c)}</li>`).join('')}</ul></div>`:''}
    <div class="actions"><button class="primary">${inline?'确认加入我的画像':'确认并保存'}</button><button type="button" class="secondary" data-action="draft-discard">放弃草稿</button>
    ${inline?'<button type="button" class="text-btn" data-action="profile">去画像页详细编辑 →</button>':''}</div></form></article>`;
}
function chatLog() {
  if (!state.profileMessages.length) return '<p class="muted">还没有对话。先说一件你做过的事，我来帮你问清楚。</p>';
  return state.profileMessages.map(m=>`<div class="chat-message ${m.role==='user'?'chat-user':'chat-ai'}"><span class="chat-role">${m.role==='user'?'我':'AI'}</span><p class="preserve-lines">${escapeHTML(m.text)}</p></div>`).join('');
}
function profileAssistant(inline=false) {
  return `<section class="card organizer"><div class="subtitle-row"><h2>和 AI 聊聊你的经历</h2><span class="panel-tools"><button class="text-btn" data-action="chat-clear">清空对话</button><button class="text-btn" data-action="profile-edit">手动编辑</button></span></div>
    <p class="muted">说说做过什么、本人负责什么，或者正在学什么。AI 会追问、也会解释；聊清楚之后再点「整理成画像草稿」，草稿确认保存前不会动你的画像。</p>
    <div id="profile-messages" class="chat-list">${chatLog()}</div>
    <form id="chat-form" data-inline="${inline?'1':'0'}"><label class="sr-label" for="profile-message">说一句</label><textarea id="profile-message" name="text" autocomplete="off" rows="3" required maxlength="2000" placeholder="例如：参与过课程短片，本人负责剪辑。"></textarea><div class="actions"><button class="primary">发送</button><button type="button" class="secondary" data-action="draft-request">${state.profileDraft?'重新整理':'整理成画像草稿'}</button></div></form>
    <div id="profile-feedback" role="status"></div></section><div id="draft-panel">${draftPanel(inline)}</div>`;
}
function assistantInline() { const panel=$('#chat-form'); return Boolean(panel && $('#inline-profile')?.contains(panel)); }
async function requestDraft() {
  captureDraft();
  const inline=assistantInline(), note=$('#profile-feedback');
  if (note) note.textContent='正在整理草稿…';
  let result;
  try { result=await api('/api/profile/draft',{draft:state.profileDraft}); }
  catch(error) { if(note) note.textContent=error.message; throw error; }
  state.profileDraft=result.draft; state.profileChanges=result.changes||[];
  if(inline) await inlineProfile(); else renderProfilePage();
  $('#profile-draft-form')?.scrollIntoView({behavior:'smooth',block:'start'});
}
async function clearChat() {
  const inline=assistantInline();
  await api('/api/profile/chat/reset',{});
  state.profileMessages=[];
  if(inline) await inlineProfile(); else renderProfilePage();
  toast('对话已清空；已保存的画像没有变。');
}
async function profilePage() {
  captureDraft(); closeSource(); setPage('profile','工作空间 / 我的竞赛画像');
  $('#main').innerHTML='<div class="loading">正在读取画像…</div>';
  const [saved, chat] = await Promise.all([api('/api/profile'), api('/api/profile/chat')]);
  state.savedProfile=saved; state.profileMessages=chat.messages||[];
  renderProfilePage();
}
function renderProfilePage() {
  const saved=state.savedProfile||{};
  $('#main').innerHTML=`<p class="eyebrow">MY COMPETITION PROFILE</p><h1>我的竞赛画像</h1>
    <p class="muted">以下内容由本人填写或确认，不代表学校认证。</p>
    <article class="card saved-profile"><span class="tag">${saved.profile?'已确认画像':'还没有画像'}</span>${profileCard(saved.profile)}
    <p class="help-line">这份画像由你本人填写和确认，也只有你能改。<strong>发布招募后</strong>，这条招募的详情页会展示你的这份画像（方便对方了解你）；别的地方不展示。</p></article>${profileAssistant()}`;
  scrollBoxToBottom('#profile-messages');
}
async function inlineProfile() {
  const [saved, chat] = await Promise.all([api('/api/profile'), api('/api/profile/chat')]);
  state.savedProfile=saved; state.profileMessages=chat.messages||[];
  $('#inline-profile').innerHTML=`<p class="help-line">这里与“我的竞赛画像”共用一份画像和同一段对话。保存后可继续在本比赛提问。</p>${profileAssistant(true)}`;
  scrollBoxToBottom('#profile-messages');
  $('#inline-profile').scrollIntoView({behavior:'smooth',block:'start'});
}
function refreshDraft() {
  const target=$('#draft-panel');
  if (target) target.innerHTML=draftPanel(state.page!=='profile');
}
function eligibilityCard(rows) {
  if (!rows?.length) return '';
  const states={satisfied:['✅','满足'],unsatisfied:['❌','不满足'],not_specified:['⚪','资料未规定'],missing:['❓','缺少信息']};
  return `<article class="card eligibility-card"><h2>资格逐项核对</h2>${rows.map(r=>{
    const [icon,label]=states[r.state]||states.missing;
    return `<div class="eligibility-row ${escapeHTML(r.state)}"><span>${icon}</span><div><strong>${escapeHTML(r.item)} · ${label}</strong><p>${escapeHTML(r.detail)}</p><small>来源：${escapeHTML(r.source||'未标注，请核对资料')}</small></div></div>`;
  }).join('')}<p class="help-line">以上逐项对照资料，不构成完整报名结论；缺少的信息可补充后再核对。</p></article>`;
}
function answerExtras(result,cid) {
  return eligibilityCard(result.eligibility)+(result.recruit_hint?`<div class="notice">${escapeHTML(result.recruit_hint)} <button class="text-btn" data-action="${result.has_recruitment?'recruit-mine':'recruit-new'}" data-cid="${escapeHTML(cid)}">${result.has_recruitment?'查看我的招募':'去发布招募'} →</button></div>`:'');
}
async function recruitmentPage(tab=state.recruitTab, filter=state.recruitFilter) {
  captureDraft(); closeSource(); setPage('recruit','工作空间 / 队伍招募');
  state.recruitTab=tab; state.recruitFilter=filter||'';
  $('#main').innerHTML='<div class="loading">正在读取招募…</div>';
  const query=tab==='mine'?'mine=1':`competition_id=${encodeURIComponent(state.recruitFilter)}`;
  const result=await api('/api/recruitments?'+query);
  state.recruitments=result.recruitments;
  $('#main').innerHTML=`<p class="eyebrow">FIND YOUR TEAM</p><div class="subtitle-row"><h1>队伍招募</h1><button class="primary" data-action="recruit-new">＋ 发布招募</button></div>
    <p class="muted">找到感兴趣的项目，通过发布者留下的方式自行联系。</p>
    <div class="tabs"><button class="tab ${tab==='plaza'?'active':''}" data-action="recruit-plaza">招募广场</button><button class="tab ${tab==='mine'?'active':''}" data-action="recruit-mine">我的招募</button></div>
    ${tab==='plaza'?`<label class="recruit-filter">按比赛查看<select id="recruit-filter"><option value="">全部比赛</option>${competitionOptions(state.recruitFilter)}</select></label>`:''}
    <div class="cards recruit-cards">${result.recruitments.map(r=>`<article class="card recruitment"><div class="subtitle-row"><span class="tag">${escapeHTML(r.competition_name)}</span><span class="recruit-status">${r.status==='open'?'招募中':'已结束'}</span></div>
    <h3>${escapeHTML(r.title)}</h3><p class="preserve-lines">${escapeHTML(r.idea||'暂未填写项目想法')}</p><p>希望 ${r.need_count} 名伙伴参与：${escapeHTML(r.want_role||'具体分工可联系沟通')}</p>
    <p class="help-line">发布者：${escapeHTML(r.publisher_name||'参赛者')} · ${escapeHTML(new Date(r.created_at).toLocaleDateString('zh-CN'))}${Object.values(r.publisher_profile||{}).some(Boolean)?' · 已填写画像':''}</p>
    <div class="actions"><button class="secondary" data-action="recruit-detail" data-id="${escapeHTML(r.id)}">查看详情</button>${r.mine?`<button class="text-btn" data-action="recruit-edit" data-id="${escapeHTML(r.id)}">编辑</button><button class="text-btn" data-action="recruit-status" data-id="${escapeHTML(r.id)}" data-status="${r.status==='open'?'closed':'open'}">${r.status==='open'?'结束招募':'重新开启'}</button><button class="text-btn danger" data-action="recruit-delete" data-id="${escapeHTML(r.id)}">删除</button>`:''}</div></article>`).join('')||'<div class="empty"><h3>还没有招募</h3><p>可以发布自己的项目想法，留下希望队友参与的工作和联系方式。</p></div>'}</div>`;
}
function competitionOptions(selected) {
  return state.competitions.map(c=>`<option value="${escapeHTML(c.id)}" ${c.id===selected?'selected':''}>${escapeHTML(c.name)} · ${escapeHTML(c.edition)}</option>`).join('');
}
async function recruitmentForm(id='',cid='') {
  if (!identity) {showLogin();return;}
  const r=id?(await api('/api/recruitments/'+encodeURIComponent(id))).recruitment:{};
  if (id&&!r.mine) throw new Error('只能编辑自己发布的招募。');
  closeSource(); setPage('recruit','队伍招募 / '+(id?'编辑招募':'发布招募'));
  $('#main').innerHTML=`<button class="text-btn" data-action="recruit">← 返回招募</button><h1>${id?'编辑招募':'发布招募'}</h1>
    <p class="muted">由你填写项目想法和希望队友参与的工作，发布后在招募广场展示；详情页会一并展示你的<strong>已确认画像</strong>（方便对方了解你）。写不出来时可以点「和 AI 一起完善」。</p>
    <div class="recruit-layout"><form id="recruit-form" data-id="${escapeHTML(id)}" class="card demo-form"><label>对应比赛<select name="competition_id" id="recruit-competition" required ${id?'disabled':''}><option value="">请选择比赛</option>${competitionOptions(r.competition_id||cid||state.recruitFilter)}</select></label>
    <label>招募标题<input name="title" autocomplete="off" required maxlength="80" value="${escapeHTML(r.title||'')}" placeholder="例如：校园竞赛助手寻找网页开发伙伴"></label>
    <label>项目想法<button type="button" class="text-btn assist-open" data-action="assist-open" aria-label="和 AI 一起完善">和 AI 一起完善</button><textarea name="idea" autocomplete="off" maxlength="300" rows="3">${escapeHTML(r.idea||'')}</textarea></label>
    <label>希望队友参与什么<input name="want_role" autocomplete="off" maxlength="80" value="${escapeHTML(r.want_role||'')}" placeholder="例如：网页开发、视频制作"></label>
    <label>招募人数<input type="number" name="need_count" min="1" max="10" required value="${r.need_count||1}"></label>
    <label>联系方式<input name="contact" autocomplete="off" required maxlength="120" value="${escapeHTML(r.contact||'')}" placeholder="微信、QQ 或邮箱；发布后公开展示"></label>
    <p class="help-line">发布者：${escapeHTML(r.publisher_name||identity.name)}。请确认联系方式适合公开。</p>
    <div class="actions"><button class="primary">${id?'保存修改':'确认发布'}</button><button type="button" class="secondary" data-action="recruit">取消</button></div></form><div id="recruit-assist-slot"></div></div>`;
  renderAssist();
}
function currentRecruitCompetition() {
  const form=$('#recruit-form');
  return form ? form.elements.competition_id.value : '';
}
function assistLog() {
  if (!state.assist.messages.length) return '<p class="muted">说说你的想法：做给谁、解决什么问题、你想承担什么、需要什么队友。不清楚也没关系，一起理。</p>';
  return state.assist.messages.map(m=>`<div class="chat-message ${m.role==='user'?'chat-user':'chat-ai'}"><span class="chat-role">${m.role==='user'?'我':'AI'}</span><p class="preserve-lines">${escapeHTML(m.text)}</p></div>`).join('');
}
function briefCard() {
  const brief=state.assist.brief;
  if (!brief) return state.assist.loading?'<div class="notice">正在读这场比赛的要求…</div>':'';
  if (!brief.answer) return `<div class="notice">比赛助手这次没答上来：${escapeHTML(brief.agent_error||'请稍后重试')}。比赛规则可以到比赛页问，这里只按你的画像陪你聊。</div>`;
  const sources=[...new Set((brief.quotes||[]).map(q=>q.name).filter(Boolean))];
  return `<article class="brief-card"><span class="tag">比赛助手 · 依据</span><p class="preserve-lines">${escapeHTML(brief.answer)}</p>
    <p class="help-line">${sources.length?`引用资料：${escapeHTML(sources.join('、'))}`:'这次回答没有可核对的引用，请到比赛页核对原文。'}</p>
    <button type="button" class="text-btn" data-action="assist-refresh">重新查一次</button></article>`;
}
function assistPreview() {
  const d=state.assist.draft;
  if (!d) return '';
  const labels=[['title','招募标题'],['idea','项目想法'],['want_role','希望队友参与']];
  return `<article class="card assist-preview"><span class="tag">待确认 · 还没填进表单</span><h3>准备填进招募表单</h3>
    ${labels.map(([key,label])=>`<label class="check-label"><input type="checkbox" data-pick="${key}" checked>${label}</label><p class="preserve-lines">${escapeHTML(d[key]||'（这一栏空着）')}</p>`).join('')}
    ${(state.assist.profileHits||[]).length?`<p class="help-line">可能参考了你主画像里的：${state.assist.profileHits.map(h=>`“${escapeHTML(h)}”`).join('、')}（自己核对一下，不对就改）</p>`:'<p class="help-line">这段没看到直接用到你主画像的地方。</p>'}
    <p class="help-line">填入会覆盖表单里同名栏目（不勾选的不会动）。招募人数和联系方式请自己填。</p>
    <div class="actions"><button type="button" class="primary" data-action="assist-apply">填入表单</button></div></article>`;
}
function assistPanel() {
  return `<aside class="card recruit-assist" id="assist-panel"><div class="subtitle-row"><h2>和 AI 一起完善</h2><button type="button" class="text-btn" data-action="assist-close">收起</button></div>
    <p class="muted">依据是这场比赛的要求（比赛助手给出，带来源）和你的已确认画像。比赛规则是依据，项目方向和分工只是建议。</p>
    <div id="assist-brief">${briefCard()}</div>
    <div class="chat-list" id="assist-chat">${assistLog()}</div>
    <form id="assist-form" class="chat-input"><input name="text" autocomplete="off" required maxlength="1000" placeholder="例如：我想做校园服务智能体，但不太会代码"><button class="primary">发送</button></form>
    <div class="actions"><button type="button" class="secondary" data-action="assist-draft">整理到招募表单</button><button type="button" class="text-btn" data-action="assist-reset">清空对话</button></div>
    <div id="assist-preview">${assistPreview()}</div></aside>`;
}
function renderAssist(bottom=false) {
  const slot=$('#recruit-assist-slot');
  if (slot) slot.innerHTML=state.assist.open?assistPanel():'';
  if (bottom) scrollBoxToBottom('#assist-panel');   // 有新内容就滚到最后，正好停在刚出来的那句/预览上
}
async function openAssist(refresh=false) {
  state.assist.open=true; state.assist.loading=true; renderAssist();
  const cid=currentRecruitCompetition();
  if (!cid) { state.assist.loading=false; renderAssist(); return toast('请先选择对应的比赛。'); }
  try {
    const result=await api('/api/recruit/brief',{competition_id:cid,refresh});
    state.assist.brief=result.brief;
  } catch (error) {
    state.assist.brief={answer:null,quotes:[],agent_error:error.message};
  }
  state.assist.loading=false; renderAssist();
}
async function resetAssist() {
  const cid=currentRecruitCompetition();
  if (cid) await api('/api/recruit/reset',{competition_id:cid});
  state.assist.messages=[]; state.assist.draft=null; state.assist.profileHits=[];
  renderAssist();
}
async function requestAssistDraft() {
  const cid=currentRecruitCompetition(), form=$('#recruit-form');
  if (!cid||!form) return toast('请先选择对应的比赛。');
  const last=[...state.assist.messages].reverse().find(m=>m.role==='user');
  const note=$('#assist-preview');
  if (note) note.innerHTML='<p class="muted">正在整理…</p>';
  let result;
  try {
    result=await api('/api/recruit/draft',{competition_id:cid,
      text:last?last.text:'请按上面的对话记录整理成招募草稿。',
      title:form.elements.title.value, idea:form.elements.idea.value,
      want_role:form.elements.want_role.value});
  } catch (error) { renderAssist(); throw error; }
  state.assist.draft=result.draft; state.assist.profileHits=result.profile_hits||[];
  renderAssist(true);
}
function applyAssistDraft() {
  const form=$('#recruit-form'), draft=state.assist.draft;
  if (!form||!draft) return;
  const picks={};
  document.querySelectorAll('#assist-preview [data-pick]').forEach(box=>{picks[box.dataset.pick]=box.checked;});
  let applied=0;
  for (const key of ['title','idea','want_role']) {
    if (picks[key] && draft[key]) { form.elements[key].value=draft[key]; applied+=1; }
  }
  toast(applied?`已填入 ${applied} 栏，可以继续改，确认无误再发布。`:'没有勾选任何一栏。');
}
async function recruitmentDetail(id) {
  const {recruitment:r}=await api('/api/recruitments/'+encodeURIComponent(id));
  setPage('recruit','队伍招募 / 详情');
  const hasProfile=Object.values(r.publisher_profile||{}).some(Boolean);
  const profileCardBlock=hasProfile
    ? `<article class="card saved-profile"><span class="tag">发布者的竞赛画像</span>${profileCard(r.publisher_profile)}
       ${r.publisher_profile_updated_at?`<p class="help-line">这份画像由发布者本人填写并确认，更新于 ${escapeHTML(new Date(r.publisher_profile_updated_at).toLocaleDateString('zh-CN'))}。</p>`:''}
       <p class="help-line">不代表学校认证，也不作能力评价。</p></article>`
    : `<article class="card saved-profile"><span class="tag">还没有画像</span><p class="muted">发布者还没有填写竞赛画像，可以先按下面的联系方式聊聊。</p></article>`;
  $('#main').innerHTML=`<button class="text-btn" data-action="recruit">← 返回招募</button><p class="eyebrow">${escapeHTML(r.competition_name)}</p><h1>${escapeHTML(r.title)}</h1>
    <p class="muted">发布者：${escapeHTML(r.publisher_name||'参赛者')} · ${r.status==='open'?'招募中':'已结束'}</p>
    <article class="card"><h2>项目想法</h2><p class="preserve-lines">${escapeHTML(r.idea||'未填写')}</p><h2>希望队友参与</h2><p>${escapeHTML(r.want_role||'可联系沟通')} · ${r.need_count} 人</p><h2>联系方式</h2><p class="preserve-lines">${escapeHTML(r.contact)}</p><p class="help-line">请自行联系，平台不代做匹配与评价。</p></article>
    ${profileCardBlock}
    ${r.mine?`<div class="actions"><button class="secondary" data-action="recruit-edit" data-id="${escapeHTML(id)}">编辑招募</button><button class="text-btn danger" data-action="recruit-delete" data-id="${escapeHTML(id)}">删除招募</button></div>`:''}`;
}
async function featureClick(button) {
  const action=button.dataset.action;
  if (!['profile-edit','draft-discard','draft-request','chat-clear','inline-profile','assist-open','assist-close','assist-refresh','assist-draft','assist-apply','assist-reset','recruit','recruit-plaza','recruit-mine','recruit-new','recruit-edit','recruit-detail','recruit-status','recruit-delete'].includes(action)) return false;
  captureDraft();
  if (action==='profile-edit') {state.profileDraft=state.profileDraft||{...(state.savedProfile?.profile||{})};state.profileChanges=[];refreshDraft();$('#profile-draft-form')?.scrollIntoView({behavior:'smooth'});}
  if (action==='draft-discard') {state.profileDraft=null;state.profileChanges=[];refreshDraft();}
  if (action==='draft-request') await requestDraft();
  if (action==='chat-clear' && confirm('清空这段对话？已保存的画像不受影响。')) await clearChat();
  if (action==='assist-open') await openAssist();
  if (action==='assist-refresh') await openAssist(true);
  if (action==='assist-close') {state.assist.open=false;renderAssist();}
  if (action==='assist-draft') await requestAssistDraft();
  if (action==='assist-apply') applyAssistDraft();
  if (action==='assist-reset' && confirm('清空这段对话？表单里已经写的内容不受影响。')) await resetAssist();
  if (action==='inline-profile') await inlineProfile();
  if (action==='recruit') await recruitmentPage();
  if (action==='recruit-plaza') await recruitmentPage('plaza',button.dataset.cid??state.recruitFilter);
  if (action==='recruit-mine') await recruitmentPage('mine','');
  if (action==='recruit-new') await recruitmentForm('',button.dataset.cid||'');
  if (action==='recruit-edit') await recruitmentForm(button.dataset.id);
  if (action==='recruit-detail') await recruitmentDetail(button.dataset.id);
  if (action==='recruit-status') {await api('/api/recruitments/update',{id:button.dataset.id,status:button.dataset.status});await recruitmentPage();}
  if (action==='recruit-delete' && confirm('删除这条招募？删除后不再展示，也无法恢复。')) {await api('/api/recruitments/delete',{id:button.dataset.id});await recruitmentPage('mine','');toast('招募已删除。');}
  return true;
}
async function featureSubmit(form) {
  if (!['chat-form','profile-draft-form','recruit-form','assist-form'].includes(form.id)) return false;
  state.busy=true;
  const submit=form.querySelector('button'); if(submit)submit.disabled=true;
  try {
    if (form.id==='chat-form') {
      captureDraft();
      const text=form.elements.text.value.trim();
      form.elements.text.value='';
      $('#profile-feedback').textContent='AI 正在回应…';
      const result=await api('/api/profile/chat',{text,draft:state.profileDraft});
      state.profileMessages=result.messages||[];
      if (result.draft) {state.profileDraft=result.draft;state.profileChanges=result.changes||[];}
      if (form.dataset.inline==='1') await inlineProfile(); else renderProfilePage();
    }
    if (form.id==='profile-draft-form') {
      const inline=form.dataset.inline==='1';
      captureDraft(); state.savedProfile=await api('/api/profile/save',{payload:state.profileDraft});
      state.profileDraft=null;state.profileChanges=[];
      if(inline){$('#inline-profile').innerHTML='<div class="notice">画像已保存。可以继续在上方提问，比赛助手会读取这份已确认画像。</div>';$('#ask-form input')?.focus();}
      else renderProfilePage();
      toast('已保存到我的画像。');
    }
    if (form.id==='assist-form') {
      const cid=currentRecruitCompetition(), recruit=$('#recruit-form');
      if (!cid||!recruit) {toast('请先选择对应的比赛。');return;}
      const text=form.elements.text.value.trim();
      state.assist.messages.push({role:'user',text});
      renderAssist(true);
      const result=await api('/api/recruit/chat',{competition_id:cid,text,
        title:recruit.elements.title.value, idea:recruit.elements.idea.value,
        want_role:recruit.elements.want_role.value});
      state.assist.messages.push({role:'assistant',text:result.reply});
      renderAssist(true); $('#assist-form input')?.focus();
    }
    if (form.id==='recruit-form') {
      const data=Object.fromEntries(new FormData(form));
      data.publisher_name=identity?.name||'参赛者';
      const id=form.dataset.id;
      await api('/api/recruitments/'+(id?'update':'create'),{...data,...(id?{id}:{})});
      await recruitmentPage('mine','');toast(id?'招募已更新':'发布成功');
    }
  } catch(error) {toast(error.message);if($('#profile-feedback'))$('#profile-feedback').textContent=error.message;}
  finally {state.busy=false;if(submit)submit.disabled=false;}
  return true;
}
document.addEventListener('change',async event=>{
  try {
    if(event.target.id==='recruit-filter') await recruitmentPage('plaza',event.target.value);
    if(event.target.id==='recruit-competition') {
      state.assist.messages=[]; state.assist.draft=null; state.assist.profileHits=[];
      state.assist.brief=null;
      if(state.assist.open) await openAssist();      // 换比赛就换依据
    }
  } catch(error){toast(error.message);}
});
document.addEventListener('input',event=>{if(event.target.closest('#profile-draft-form'))captureDraft();});
