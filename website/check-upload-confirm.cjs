// Browser regression for explicit competition association. Uses an offline temp DB.
const {chromium} = require('playwright');
const {spawn, execFileSync} = require('node:child_process');
const assert = require('node:assert/strict');
const path = require('node:path');

(async () => {
  const port = 8778;
  const server = spawn('python', ['demo_test_server.py'], {
    cwd: __dirname, windowsHide: true,
    env: {...process.env, HUIKE_PORT: String(port), PYTHONIOENCODING: 'utf-8'},
    stdio: 'ignore',
  });
  let browser;
  try {
    let ready = false;
    for (let i = 0; i < 60; i++) {
      try { ready = (await fetch(`http://127.0.0.1:${port}/api/status`)).ok; } catch {}
      if (ready) break;
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    assert(ready, 'offline test server did not start');
    browser = await chromium.launch({headless: true, executablePath: path.join(
      process.env.LOCALAPPDATA, 'ms-playwright', 'chromium-1223', 'chrome-win64', 'chrome.exe')});
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}`);
    await page.getByRole('button', {name: '去注册 →'}).click();
    await page.locator('#register-form [name=account]').fill('upload-test@example.com');
    await page.locator('#register-form [name=name]').fill('测试者');
    await page.locator('#register-form [name=password]').fill('huike123');
    await page.locator('#register-form [name=confirm]').fill('huike123');
    await page.getByRole('button', {name: '注册并进入'}).click();
    await page.locator('nav [data-action=upload]').click();
    const pdf = execFileSync('python', ['-c',
      'from test_store import pdf_bytes; import sys; sys.stdout.buffer.write(pdf_bytes())'],
      {cwd: __dirname, windowsHide: true});
    await page.evaluate(async () => {
      const upload=await (await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:'old.txt',content:btoa('Existing event notice.')})})).json();
      const response=await fetch('/api/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({upload_id:upload.upload_id,competition_id:'c1',title:'已有通知',uploader:'测试者'})});
      if(!response.ok) throw new Error('fixture document failed');
    });
    await page.locator('#pdf-input').setInputFiles({name: '关于举办演示比赛2026的通知.pdf',
      mimeType: 'application/pdf', buffer: pdf});
    await page.locator('#confirm-form').waitFor();
    assert.equal(await page.locator('#competition-select').inputValue(), '',
      'suggested competition must not be selected automatically');
    assert.match(await page.locator('#competition-match-note').innerText(), /演示比赛/);
    assert(await page.locator('#confirm-form .actions button.primary').isDisabled(), 'must answer association question first');
    assert.equal(await page.locator('#confirm-form select[name=competition_id]').count(), 0, 'no competition dropdown');
    const before = await page.evaluate(async () => (await (await fetch(
      '/api/documents?competition_id=c1')).json()).length);
    assert.equal(before, 1, 'upload must remain temporary until explicit confirmation');
    await page.locator('#confirm-form [name=title]').fill('尚未提交的新资料');
    await page.getByRole('button',{name:'查看已有比赛及资料 ↗',exact:true}).click();
    await page.locator('.candidate-documents').getByText('已有通知',{exact:true}).waitFor();
    await page.getByRole('button',{name:'查看原文件 ↗',exact:true}).click();
    assert(await page.locator('#source-panel').isVisible());
    assert.match(await page.locator('#source-frame').getAttribute('src'), /documents/);
    assert.equal(await page.locator('#confirm-form [name=title]').inputValue(),'尚未提交的新资料');
    assert.equal(await page.locator('#competition-select').inputValue(),'','viewing sources must not confirm association');
    await page.getByRole('button', {name: '不是，这是另一场比赛', exact: true}).click();
    assert(await page.locator('#new-name').isVisible());
    assert.equal(await page.locator('[name=create_new_confirmed]').inputValue(), '1');
    assert(await page.locator('#competition-match-note').isHidden());
    assert.match(await page.locator('#association-decision').innerText(), /已选择创建新比赛/);
    await page.getByRole('button', {name: '重新选择', exact: true}).click();
    assert(await page.locator('#competition-match-note').isVisible());
    assert(await page.locator('#confirm-form .actions button.primary').isDisabled());
    assert.equal(await page.locator('#confirm-form [name=title]').inputValue(),'尚未提交的新资料');
    await page.getByRole('button', {name: '是，加入这场比赛', exact: true}).click();
    assert(await page.locator('#new-name').isHidden());
    assert(await page.locator('#competition-match-note').isHidden());
    assert.match(await page.locator('#association-decision').innerText(), /已选择已有比赛/);
    assert.match(await page.locator('#association-decision').innerText(), /资料尚未提交/);
    await page.locator('#document-update-panel [name=update_mode][value=add]').check();
    await page.screenshot({path: path.join(__dirname,'artifacts','upload-association-selected.png'),fullPage:true});
    await page.locator('#confirm-form .actions button.primary').click();
    await page.getByRole('heading', {name: '演示比赛', exact: true}).waitFor();
    const after = await page.evaluate(async () => (await (await fetch(
      '/api/documents?competition_id=c1')).json()).length);
    assert.equal(after, 2, 'confirmed document belongs to the chosen competition');

    // Keep a background task unfinished while reloading, then complete it.
    await page.waitForFunction(() => !state.busy);
    await page.locator('nav [data-action=upload]').click();
    await page.locator('#pdf-input').waitFor();
    const blankPdf = execFileSync('python', ['-c',
      'from pypdf import PdfWriter; import sys; w=PdfWriter(); w.add_blank_page(width=100,height=100); w.write(sys.stdout.buffer)'],
      {cwd:__dirname,windowsHide:true});
    const noTextUpload = await page.evaluate(async content => (await (await fetch('/api/upload', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({filename:'unrecognized.pdf',content})
    })).json()), blankPdf.toString('base64'));
    let finishJob = false;
    const testJobId = 'a'.repeat(32);
    await page.route('**/api/upload', async route => {
      await route.fulfill({status:202,contentType:'application/json',body:JSON.stringify({job_id:testJobId})});
    });
    await page.route(`**/api/upload-jobs/${testJobId}`, async route => {
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(finishJob
        ? {status:'completed',result:noTextUpload}
        : {status:'processing',stage:'recognizing',completed:8,total:23})});
    });
    await page.locator('#pdf-input').setInputFiles({name:'unrecognized.pdf',mimeType:'application/pdf',buffer:blankPdf});
    await page.getByRole('heading',{name:'正在识别：已处理 8 / 23 页',exact:true}).waitFor();
    page.once('dialog', dialog => dialog.accept());
    await page.reload();
    await page.getByRole('heading',{name:'正在识别：已处理 8 / 23 页',exact:true}).waitFor();
    finishJob = true;
    await page.locator('#confirm-form').waitFor();
    assert.match(await page.locator('#competition-match-note').innerText(), /尚未判断所属比赛/);
    assert.equal(await page.locator('#competition-select').inputValue(),'');
    assert(await page.locator('#association-decision').isHidden());
    assert(await page.locator('#confirm-form .actions button.primary').isDisabled());
    console.log('Explicit upload association: OK');
    console.log('Upload progress, reload recovery, no-text association: OK');
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
