const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {once}=require('node:events');

(async()=>{
  const port=8779;
  const server=spawn('python',['document_update_test_server.py'],{cwd:__dirname,windowsHide:true,
    env:{...process.env,HUIKE_PORT:String(port),PYTHONIOENCODING:'utf-8'},stdio:'ignore'});
  let browser;
  try {
    let ready=false;
    for(let i=0;i<60;i++) {
      try {ready=(await fetch(`http://127.0.0.1:${port}/api/status`)).ok;} catch {}
      if(ready) break;
      await new Promise(resolve=>setTimeout(resolve,150));
    }
    assert(ready,'offline fixture did not start');
    browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,
      'ms-playwright','chromium-1223','chrome-win64','chrome.exe')});
    const page=await browser.newPage({viewport:{width:1480,height:980}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(`http://127.0.0.1:${port}`);
    await page.getByRole('button',{name:'去注册 →'}).click();
    await page.locator('#register-form [name=account]').fill('updates@example.com');
    await page.locator('#register-form [name=name]').fill('资料更新测试者');
    await page.locator('#register-form [name=password]').fill('huike123');
    await page.locator('#register-form [name=confirm]').fill('huike123');
    await page.getByRole('button',{name:'注册并进入'}).click();
    async function upload(content) {
      await page.waitForFunction(()=>!state.busy);
      await page.locator('nav [data-action=upload]').click();
      await page.locator('#pdf-input').setInputFiles({name:'关于举办演示比赛2026的通知.txt',mimeType:'text/plain',buffer:Buffer.from(content)});
      await page.locator('#confirm-form').waitFor();
      await page.getByRole('button',{name:'是，加入这场比赛',exact:true}).click();
      await page.getByRole('heading',{name:'这份资料与旧资料是什么关系？',exact:true}).waitFor();
    }
    await upload('初赛截止：9月24日。每队2至6人。');
    assert(await page.locator('#confirm-form .actions button.primary').isDisabled());
    await page.locator('[name=update_mode][value=partial]').check();
    const changes=page.locator('.change-card');assert.equal(await changes.count(),2);
    await changes.filter({hasText:'初赛截止时间'}).locator('[name=selected_changes]').check();
    assert(await changes.filter({hasText:'队伍人数'}).locator('[name=selected_changes]').isEnabled());
    assert(!(await changes.filter({hasText:'队伍人数'}).locator('[name=selected_changes]').isChecked()));
    await changes.first().getByRole('button',{name:'查看旧原文 ↗'}).click();
    assert.match(await page.locator('#source-frame').getAttribute('src'),/documents/);
    await changes.first().getByRole('button',{name:'查看新原文 ↗'}).click();
    assert.match(await page.locator('#source-frame').getAttribute('src'),/uploads/);
    fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});
    await page.screenshot({path:path.join(__dirname,'artifacts','document-update-comparison.png'),fullPage:true});
    await page.getByRole('button',{name:'确认更新资料',exact:true}).click();
    await page.getByRole('heading',{name:'演示比赛',exact:true}).waitFor();
    const details=await page.evaluate(async()=>{
      const post=async query=>(await (await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({competition_id:'c1',query})})).json()).hits;
      const docs=await (await fetch('/api/documents?competition_id=c1')).json();
      const text=(await post('初赛截止人数材料')).map(h=>h.text).join('\n');
      const original=await (await fetch(`/api/documents/${docs.find(d=>d.title==='初赛原通知').id}/preview`)).json();
      return {text,docs,original};
    });
    assert.match(details.text,/9月24日/);assert.match(details.text,/1至5人/);assert.match(details.text,/PPT/);
    assert(!details.text.includes('8月22日'));assert(!details.text.includes('2至6人'));
    assert.match(details.original.pages.join('\n'),/8月22日/);
    assert.equal(details.docs.find(d=>d.update_operation)?.update_operation.status,'ready');
    await page.locator('.doc-fold > summary').click();
    assert(await page.getByText('部分规定已更新',{exact:true}).count()>=1);
    await page.locator('.update-history').first().locator('summary').click();
    await page.screenshot({path:path.join(__dirname,'artifacts','document-update-applied.png'),fullPage:true});
    await upload('决赛说明：现场演示。');
    await page.locator('[name=update_mode][value=replace]').check();
    for(const target of await page.locator('[name=target_document_ids]').all()) await target.check();
    await page.getByRole('button',{name:'确认更新资料',exact:true}).click();
    await page.getByRole('heading',{name:'演示比赛',exact:true}).waitFor();
    await page.locator('.doc-fold > summary').click();
    assert.match(await page.locator('.history-documents > summary').innerText(),/2 份/);
    const remaining=await page.evaluate(async()=>(await (await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({competition_id:'c1',query:'人数'})})).json()).hits);
    assert.equal(remaining.length,0);
    assert.deepEqual(errors,[]);
    console.log('Partial selection + unchanged rules + original links + whole replacement + history: OK');
  } finally {if(browser) await browser.close();if(server.exitCode===null && server.signalCode===null){const stopped=once(server,'close');server.kill();await stopped;}}
})().catch(error=>{console.error(error);process.exitCode=1;});
