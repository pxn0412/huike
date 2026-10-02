// Offline end-to-end upload checks: never starts the production server or writes to ADP.
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const path=require('node:path');
const assert=require('node:assert/strict');

(async()=>{
  const base='http://127.0.0.1:8779';
  const server=spawn('python',['demo_test_server.py'],{cwd:__dirname,windowsHide:true,
    env:{...process.env,HUIKE_PORT:'8779'},stdio:'ignore'});
  let browser;
  try{
    for(let i=0;i<80;i++){
      if(server.exitCode!==null)throw Error('Fixture server exited');
      try{if((await fetch(base+'/api/status')).ok)break;}catch{}
      await new Promise(r=>setTimeout(r,150));
    }
    browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,
      'ms-playwright','chromium-1223','chrome-win64','chrome.exe')});
    const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    assert((await page.request.post(base+'/api/auth/register',{data:{account:'formats@example.com',password:'formats123',name:'Format test'}})).ok());
    await page.goto(base);
    await page.waitForFunction(()=>!document.querySelector('#login-page').offsetHeight);
    const files=JSON.parse(execFileSync('python',['-c',
      "import json,base64; from test_document_formats import word_bytes,image_bytes; from test_store import pdf_bytes; print(json.dumps({k:base64.b64encode(v).decode() for k,v in [('notice.docx',word_bytes()),('notice.png',image_bytes()),('notice.pdf',pdf_bytes()),('notice.txt',b'Competition registration information.')]}))"
    ],{cwd:__dirname,encoding:'utf8'}));
    for(const [filename,encoded] of Object.entries(files)){
      await page.evaluate(()=>uploadPage());
      await page.locator('#pdf-input').setInputFiles({name:filename,mimeType:'application/octet-stream',buffer:Buffer.from(encoded,'base64')});
      await page.locator('#confirm-form').waitFor();
      const frame=page.frameLocator('#source-frame');
      if(filename.endsWith('.docx'))await frame.locator('.extracted-text').filter({hasText:'10分钟以内'}).waitFor();
      if(filename.endsWith('.png'))await frame.locator('img').waitFor();
      if(filename.endsWith('.pdf'))await frame.locator('#total').filter({hasText:'2'}).waitFor();
      const upload=await page.evaluate(()=>state.upload);
      const raw=await page.request.get(base+upload.preview_url);
      assert.deepEqual(await raw.body(),Buffer.from(encoded,'base64'));
      assert(raw.headers()['content-disposition'].includes(filename));
      const name=page.locator('#confirm-form [name=name]');
      if(await name.isEnabled()){await name.fill('Format competition');await page.locator('#confirm-form [name=edition]').fill('2026');}
      await page.locator('#confirm-form button.primary').click();
      await page.locator('#confirm-form').waitFor({state:'detached'});
      console.log('Upload + preview + confirm PASS:',filename);
    }
    assert.equal(await page.evaluate(()=>state.documents.length),4);
    assert.deepEqual(errors,[]);
    console.log('Multi-format browser flow PASS; cloud calls mocked.');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(e=>{console.error(e);process.exitCode=1;});
