const {chromium} = require('playwright');
const {spawn,execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');

(async () => {
  const server = spawn('python', ['demo_test_server.py'], {cwd: __dirname, windowsHide: true,
    env: {...process.env, HUIKE_PORT: '8777', PYTHONIOENCODING: 'utf-8'}, stdio: 'pipe'});
  let browser;
  try {
    for (let i=0;i<60;i++) {
      try { if ((await fetch('http://127.0.0.1:8777/api/status')).ok) break; } catch {}
      await new Promise(r=>setTimeout(r,150));
    }
    browser = await chromium.launch({headless:true, executablePath:path.join(process.env.LOCALAPPDATA,
      'ms-playwright','chromium-1223','chrome-win64','chrome.exe')});
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('dialog', dialog => dialog.accept());      // 删除/清空这些确认框一律点"确定"
    const errors=[]; page.on('pageerror', e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8777');
    await page.locator('#login-page:not([hidden])').waitFor({timeout:5000});
    assert(await page.locator('#login-form').isVisible(),'默认停在「登录」tab');
    assert(await page.locator('#register-form').isHidden(),'注册表单默认收起');
    assert.equal(await page.locator('#login-form select').count(),0,'登录页不该让用户选角色');
    assert.equal(await page.evaluate(async()=> (await fetch('/api/profile')).status),401,'未登录必须挡住接口');
    // 没注册过就登录：明确提示去注册，也不会被顺手建出账号
    await page.locator('#login-form [name=account]').fill('zhang@example.com');
    await page.locator('#login-form [name=password]').fill('huike123');
    await page.locator('#login-form').getByRole('button',{name:'登录',exact:true}).click();
    await page.getByText('这个账号还没注册',{exact:false}).waitFor({timeout:5000});
    fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-login.png'),fullPage:true});
    const anonymous=await page.evaluate(async()=> (await (await fetch('/api/auth/session')).json()).user);
    assert.equal(anonymous,null,'登录失败不该留下会话');
    // 切到注册 tab，注册并进入
    await page.getByRole('button',{name:'去注册 →',exact:true}).click();
    assert(await page.locator('#register-form').isVisible(),'点「去注册」要切到注册表单');
    await page.locator('#register-form [name=account]').fill('zhang@example.com');
    await page.locator('#register-form [name=name]').fill('小张');
    await page.locator('#register-form [name=password]').fill('huike123');
    await page.locator('#register-form [name=confirm]').fill('huike123');
    fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-register.png'),fullPage:true});
    await page.locator('#register-form').getByRole('button',{name:'注册并进入',exact:true}).click();
    await page.getByRole('heading',{name:/下一场比赛/}).waitFor({timeout:10000});
    await page.locator('nav [data-action=profile]').click();
    // 画像页：先聊清楚（不出草稿），再点「整理成画像草稿」
    await page.locator('#chat-form textarea').fill('参与过短片');
    await page.locator('#chat-form').getByRole('button',{name:'发送',exact:true}).click();
    await page.getByText('这件事里你本人主要负责什么？',{exact:true}).waitFor();
    assert.equal(await page.locator('#profile-draft-form').count(),0,'只是聊一句，不该直接出草稿');
    await page.locator('#chat-form textarea').fill('本人剪辑');
    await page.locator('#chat-form').getByRole('button',{name:'发送',exact:true}).click();
    await page.getByText('明白，你本人负责剪辑。这个短片是课程作业还是自己做的？',{exact:true}).waitFor();
    assert.equal(await page.locator('#profile-draft-form').count(),0,'补充一句也不该出草稿');
    await page.getByRole('button',{name:'整理成画像草稿',exact:true}).click();
    await page.locator('#profile-draft-form').waitFor();
    assert.equal(await page.evaluate(async()=> (await (await fetch('/api/profile')).json()).profile),null,'草稿不等于保存');
    await page.getByRole('button',{name:'确认并保存',exact:true}).click();
    await page.getByText('已确认画像',{exact:true}).waitFor();
    assert.equal(await page.locator('#profile-public').count(),0,'画像页不该再有公开开关');
    await page.reload();
    await page.locator('nav [data-action=profile]').click();
    assert((await page.locator('.saved-profile').innerText()).includes('本人负责：剪辑'),'「本人负责」要和「做过什么」分开成行');
    assert((await page.locator('#profile-messages').innerText()).includes('本人剪辑'),'刷新后对话记录还在');
    fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-profile.png'),fullPage:true});
    await page.locator('nav [data-action=recruit]').click();
    await page.getByRole('button',{name:'＋ 发布招募',exact:true}).click();
    await page.locator('#recruit-form [name=competition_id]').selectOption('c1');
    // 自己写不出来时：「和 AI 一起完善」→ 比赛助手给依据 → 画像助手陪聊 → 整理进表单
    await page.locator('#recruit-form [data-action=assist-open]').click();
    await page.getByText(/资料未规定：赛道方向/).waitFor();
    assert((await page.locator('#assist-brief').innerText()).includes('演示通知'),'依据卡要标出来源');
    // 依据卡可能很长，但面板不比左边表单长：两边底部对齐，面板自己滚（标题栏钉住，「收起」滚下去也点得到）
    const panel=page.locator('#assist-panel');
    assert.equal(await panel.evaluate(el=>getComputedStyle(el).overflowY),'auto','面板要能自己滚');
    assert(await panel.evaluate(el=>Math.abs(el.getBoundingClientRect().height-document.querySelector('#recruit-form').getBoundingClientRect().height)<=1),'面板要和左边表单一样高（两边对齐）');
    assert.equal(await page.locator('#assist-chat').evaluate(el=>getComputedStyle(el).overflowY),'visible','面板里只留面板这一条滚动条');
    assert.equal(await page.locator('#assist-form input').getAttribute('autocomplete'),'off','输入框不再弹浏览器的"保存的信息"');
    await page.locator('#assist-form input').fill('我想做校园服务智能体，但不太会代码');
    await page.locator('#assist-form').getByRole('button',{name:'发送',exact:true}).click();
    await page.getByText('可以，先想一个具体的校园场景：你最想先解决哪个问题？',{exact:true}).waitFor();
    assert(await page.locator('#assist-chat .chat-ai').last().evaluate(el=>{const r=el.getBoundingClientRect();const p=document.querySelector('#assist-panel').getBoundingClientRect();return r.top>=p.top-1&&r.bottom<=p.bottom+1;}),'面板里的新回复要落在可视区里');
    await page.locator('#recruit-assist-slot [data-action=assist-draft]').click();
    await page.locator('#assist-preview').getByText('校园服务智能体找搭子',{exact:true}).waitFor();
    assert((await page.locator('#assist-preview').innerText()).includes('主画像'),'预览要说清有没有用到主画像');
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-recruit-assist.png'),fullPage:true});
    await page.locator('#assist-preview [data-action=assist-apply]').click();
    assert.equal(await page.locator('#recruit-form [name=title]').inputValue(),'校园服务智能体找搭子','标题应被填入');
    assert((await page.locator('#recruit-form [name=idea]').inputValue()).includes('本人负责前端与演示脚本'),'项目想法应被填入');
    assert((await page.locator('#recruit-form [name=want_role]').inputValue()).includes('会做后端接口的同学'),'队友要求应被填入');
    await page.locator('#recruit-form [name=contact]').fill('QQ 123456');
    assert.equal(await page.locator('#recruit-form [name=show_profile]').count(),0,'招募表单不该再有"展示画像"勾选');
    await page.locator('#recruit-form').getByRole('button',{name:'确认发布',exact:true}).click();
    await page.getByRole('heading',{name:'校园服务智能体找搭子',exact:true}).waitFor();
    await page.getByRole('button',{name:'招募广场',exact:true}).click();
    await page.getByRole('button',{name:'查看详情',exact:true}).click();
    await page.getByText('QQ 123456',{exact:true}).waitFor();
    // 「看画像」：详情页要能看到发布者的已确认画像（默认展示，没有开关）
    assert(await page.getByText('发布者的竞赛画像',{exact:true}).isVisible(),'招募详情要能看到发布者的画像');
    assert((await page.locator('.saved-profile').innerText()).includes('数字媒体'),'详情页展示的是发布者已确认的画像内容');
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-recruitment.png'),fullPage:true});
    // 招募能删：列表卡片上就有「删除」，删完列表和服务端都不再有这条
    await page.getByRole('button',{name:'← 返回招募',exact:true}).click();
    await page.getByRole('button',{name:'我的招募',exact:true}).click();
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-recruit-mine.png'),fullPage:true});
    await page.locator('.recruitment [data-action=recruit-delete]').click();
    await page.getByText('招募已删除。',{exact:true}).waitFor();
    await page.getByText('还没有招募',{exact:true}).waitFor();
    assert.equal(await page.locator('.recruitment').count(),0,'删除后「我的招募」应该空了');
    const left=await page.evaluate(async()=> (await (await fetch('/api/recruitments?mine=1')).json()).recruitments.length);
    assert.equal(left,0,'服务端也要真的删掉');
    await page.locator('nav [data-action=library]').click();
    await page.locator('.competition').first().click();
    // 通知和问答已经合成一页：没有 tab，资料收在标题下面的一行折叠里
    await page.locator('.doc-fold>summary').waitFor();
    assert.equal(await page.getByRole('button',{name:'全部通知',exact:true}).count(),0,'通知不再单独占一栏');
    assert(await page.locator('.doc-fold').isVisible(),'资料收成一行放在标题下面');
    assert.equal(await page.locator('#ask-form input').getAttribute('autocomplete'),'off','提问框不再弹浏览器的"保存的信息"');
    await page.locator('#ask-form input').fill('一个人可以参加吗？想找队友');
    await page.locator('#ask-form button').click();
    await page.locator('.eligibility-card').waitFor();
    assert.equal(await page.locator('.eligibility-row').count(),2);
    assert.equal(await page.locator('.evidence-fold').count(),0,'这场比赛还没有资料，不该出现本地片段区');
    assert.equal(await page.getByText('未检索到相关片段').count(),0,'有回答时不该再显示"未检索到相关片段"占位');
    assert((await page.locator('#answers').innerText()).includes('另有知识库文件'),'没对应到本地资料的引用要降级成一行小字');
    // 提问是「我」的气泡、回答是卡片：一轮问答后面只留一块「依据与原文」
    const lastTurn=page.locator('.chat-turn').last();
    assert.equal(await lastTurn.locator('.chat-question').count(),1,'提问要单独成气泡');
    assert((await lastTurn.locator('.chat-question').innerText()).includes('一个人可以参加吗？想找队友'),'气泡里是用户自己的问题');
    const lastEvidence=lastTurn.locator('.evidence-block');
    assert.equal(await lastEvidence.count(),1,'依据、提示、原文片段合成一块');
    assert((await lastEvidence.innerText()).includes('另有知识库文件'),'引用和提示在同一块里');
    assert((await lastEvidence.innerText()).includes('依据来自平台知识库'),'用时与依据说明也收进同一块');
    assert(await lastEvidence.evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight+2;}),'回答完停在最新一轮的结尾，输入框跟到屏幕底部');
    await page.getByRole('button',{name:'补充我的情况',exact:true}).click();
    await page.locator('#inline-profile #chat-form').waitFor();
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-competition.png'),fullPage:true});
    await page.locator('nav [data-action=upload]').click();
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-upload.png'),fullPage:true});
    assert(!/老师|学生/.test(await page.locator('#main').innerText()));
    assert.equal(await page.getByText('使用桌面上的慧科杯通知',{exact:false}).count(),0,'上传页不该再出现桌面样例入口');
    const pdf=execFileSync('python',['-c','from test_store import pdf_bytes; import sys; sys.stdout.buffer.write(pdf_bytes())'],{cwd:__dirname,windowsHide:true});
    await page.locator('#pdf-input').setInputFiles({name:'notice.pdf',mimeType:'application/pdf',buffer:pdf});
    await page.locator('#confirm-form').waitFor();
    await page.locator('#competition-select').selectOption('');
    await page.locator('#confirm-form [name=name]').fill('新上传比赛');
    await page.locator('#confirm-form [name=edition]').fill('2026');
    await page.locator('#confirm-form [name=title]').fill('人数通知');
    await page.getByRole('button',{name:'确认添加',exact:true}).click();
    await page.getByRole('heading',{name:'新上传比赛',exact:true}).waitFor();
    await page.locator('.doc-fold>summary').click();          // 资料收在标题下面的折叠行里，先点开
    await page.getByRole('button',{name:'查看原文 ↗',exact:true}).click();
    await page.frameLocator('#source-frame').locator('#total').filter({hasText:'2'}).waitFor();
    await page.getByRole('button',{name:'关闭原文',exact:true}).click();
    assert(await page.locator('.doc-fold').getAttribute('open')!==null,'点开过的资料折叠行不该自己合上');
    await page.locator('#ask-form input').fill('members');
    await page.locator('#ask-form button').click();
    await page.locator('.evidence-fold[open]').waitFor();
    await page.locator('.evidence').filter({hasText:'1-5'}).waitFor();
    await page.locator('#ask-form input').fill('members 队友');
    await page.locator('#ask-form button').click();
    const fold=page.locator('.chat-turn').last().locator('.evidence-fold');
    await fold.waitFor();
    await page.screenshot({path:path.join(__dirname,'artifacts','demo-answer.png'),fullPage:true});
    assert.equal(await fold.getAttribute('open'),null,'平台给了引用时本地片段应默认收起');
    await fold.locator('summary').click();
    await fold.locator('.evidence').filter({hasText:'1-5'}).waitFor();
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'比赛页 mobile overflow');
    await page.locator('nav [data-action=profile]').click();
    await page.locator('#chat-form').waitFor();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'mobile overflow');
    // 退出 → 用刚注册的账号登录（走一遍「登录」这条路）
    await page.setViewportSize({width:1440,height:1000});
    await page.locator('.identity').click();
    await page.locator('#login-page:not([hidden])').waitFor({timeout:5000});
    await page.locator('#login-form [name=account]').fill('zhang@example.com');
    await page.locator('#login-form [name=password]').fill('huike123');
    await page.locator('#login-form').getByRole('button',{name:'登录',exact:true}).click();
    await page.getByRole('heading',{name:/下一场比赛/}).waitFor({timeout:10000});
    assert.deepEqual(errors,[]);
    console.log('PASS: 比赛页把通知和问答合成一页（资料收成一行折叠、用户提问单独成气泡、依据与原文合成一块、回答完停在最新一轮结尾）/登录门禁(未登录 401)/注册与登录/画像页"先聊清楚再整理"(聊天不出草稿、点整理才出草稿、刷新后对话还在)/公开开关已删除(画像页只有自己看、招募详情改看发布者画像)/招募协助(比赛助手给依据带来源 → 画像助手陪聊 → 整理到表单 → 预览确认才填入)/招募发布·查看·删除(列表卡片上就能删)/资格卡片/比赛页就地补画像/PDF 上传确认预览检索/手机宽度。All AI responses are offline fixtures.');
  } finally {if(browser) await browser.close(); server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
