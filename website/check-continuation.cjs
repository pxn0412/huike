// Deterministic UI regression: a follow-up must not leave the viewport on the previous answer.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
  const server=spawn('python',['demo_test_server.py'],{cwd:__dirname,windowsHide:true,
    env:{...process.env,HUIKE_PORT:'8778',PYTHONIOENCODING:'utf-8'},stdio:'pipe'});
  let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch('http://127.0.0.1:8778/api/status')).ok)break;}catch{}await new Promise(r=>setTimeout(r,150));}
    browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,'ms-playwright','chromium-1223','chrome-win64','chrome.exe')});
    const page=await browser.newPage({viewport:{width:1360,height:850}});
    const sent=[];let resets=0,release;
    page.on('request',r=>{if(r.url().endsWith('/api/session/reset'))resets++;});
    let secondStarted;
    const started=new Promise(resolve=>secondStarted=resolve);
    const pending=new Promise(resolve=>release=resolve);
    await page.route('**/api/ask',async route=>{
      const q=route.request().postDataJSON().query;sent.push(q);
      if(sent.length===2){secondStarted();await pending;}
      if(sent.length===3)return route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'模拟网络失败'})});
      await route.fulfill({contentType:'application/json',body:JSON.stringify({mode:'agent',continued:sent.length>1,
        answer:sent.length===1?'上一题人数回答\n'+'人数依据解释。\n'.repeat(45):'这一题材料回答：PPT、演示视频、在线链接。',hits:[],quotes:[]})});
    });
    await page.goto('http://127.0.0.1:8778');
    await page.locator('#login-page:not([hidden])').waitFor({timeout:5000});
    await page.getByRole('button',{name:'去注册 →',exact:true}).click();
    await page.locator('#register-form [name=account]').fill('zhuiwen@example.com');
    await page.locator('#register-form [name=name]').fill('追问测试');
    await page.locator('#register-form [name=password]').fill('huike123');
    await page.locator('#register-form [name=confirm]').fill('huike123');
    await page.locator('#register-form').getByRole('button',{name:'注册并进入',exact:true}).click();
    await page.getByRole('heading',{name:/下一场比赛/}).waitFor({timeout:10000});
    await page.locator('.competition').first().click();
    await page.locator('#ask-form input').waitFor();         // 通知与问答合成一页：不再点 tab
    await page.locator('#ask-form input').fill('一个人能参加吗？');await page.locator('#ask-form button').click();
    await page.locator('#answers').getByText(/上一题人数回答/).waitFor();
    await page.locator('#ask-form input').fill('需要提交什么材料？');await page.locator('#ask-form button').click();await started;
    const previousRetained=(await page.locator('#answers').innerText()).includes('上一题人数回答');
    release();await page.locator('#answers').getByText(/这一题材料回答/).waitFor();
    const latestVisible=await page.locator('#answers .ai-answer').last().evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.top<innerHeight;});
    const formAtBottom=await page.locator('#ask-form').evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight+2;});
    console.log({previousRetained,latestVisible,formAtBottom,sent,resets});
    assert(previousRetained,'Sending a follow-up must preserve the previous answer during loading');
    assert(latestVisible,'The new answer must be visible without starting a new conversation');
    assert(formAtBottom,'Ask-finish must land at the bottom of the latest exchange (input box on screen)');
    assert.equal(resets,0);assert.deepEqual(sent,['一个人能参加吗？','需要提交什么材料？']);
    assert.equal(await page.locator('#ask-form input').inputValue(),'');
    await page.locator('#ask-form input').fill('失败后还保留吗');await page.locator('#ask-form button').click();
    await page.locator('#answers').getByText('模拟网络失败',{exact:true}).waitFor();
    assert((await page.locator('#answers').innerText()).includes('这一题材料回答'));
    await page.getByRole('button',{name:'新会话',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#answers').innerText.includes('上一题人数回答'));
    assert.equal(resets,1);
    console.log('PASS: follow-up keeps history, latest answer visible, view ends at the latest exchange (input box at the bottom), no implicit reset, error preserves history, explicit new session clears it.');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
