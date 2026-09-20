const { chromium } = require('playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async()=>{
  const data=fs.mkdtempSync(path.join(require('node:os').tmpdir(),'huike-ui-'));
  const server=spawn('python',['server.py'],{cwd:__dirname,env:{...process.env,HUIKE_PORT:'8766',HUIKE_DATA_DIR:data,PYTHONIOENCODING:'utf-8'},windowsHide:true,stdio:'pipe'});
  let browser;
  try{
    for(let i=0;i<50;i++){try{if((await fetch('http://127.0.0.1:8766/api/status')).ok)break;}catch{}await new Promise(r=>setTimeout(r,200));}
    browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,'ms-playwright','chromium-1223','chrome-win64','chrome.exe')});
    const page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    fs.mkdirSync(path.join(__dirname,'artifacts'),{recursive:true});
    await page.goto('http://127.0.0.1:8766');
    await page.getByRole('heading',{name:/下一场比赛/}).waitFor();
    await page.screenshot({path:path.join(__dirname,'artifacts','01-首页.png'),fullPage:true});
    await page.locator('.hero [data-action="upload"]').click();
    await page.locator('#identity-form input[name=name]').fill('演示同学');
    await page.getByRole('button',{name:'保存身份'}).click();
    await page.locator('#pdf-input').waitFor();
    await page.getByRole('button',{name:'使用桌面上的慧科杯通知 →'}).click();
    await page.locator('#confirm-form').waitFor({timeout:120000});
    await page.locator('#confirm-form input[name=name]').fill('慧科杯 AI 创新大赛');
    await page.locator('#confirm-form input[name=edition]').fill('2026');
    await page.locator('#confirm-form input[name=publisher]').fill('浙江广厦建设职业技术大学');
    await page.locator('#confirm-form input[name=published_at]').fill('2026-06-05');
    const frame=page.frameLocator('#source-frame');
    await frame.locator('#total').filter({hasText:'6'}).waitFor();
    await page.waitForFunction(()=>document.querySelector('#source-frame').contentDocument.querySelector('canvas').width>100);
    await page.screenshot({path:path.join(__dirname,'artifacts','02-上传确认.png'),fullPage:true});
    await page.getByRole('button',{name:'确认添加',exact:true}).click();
    await page.getByRole('heading',{name:'慧科杯 AI 创新大赛',exact:true}).waitFor();
    assert.equal(await page.locator('.document').count(),1);
    await page.screenshot({path:path.join(__dirname,'artifacts','03-比赛资料.png'),fullPage:true});
    await page.getByRole('button',{name:'问答与原文',exact:true}).click();
    await page.locator('#ask-form input').fill('人数');
    // 按钮文案随是否配置 AI 变化（有 AI 时是"提问"，没有时是"查原文"）。
    await page.getByRole('button',{name:/查原文|提问/}).first().click();
    await page.locator('.evidence').first().waitFor();
    assert((await page.locator('.evidence').first().innerText()).includes('PDF 第'));
    await page.locator('.evidence [data-action=source]').first().click();
    await frame.locator('#total').filter({hasText:'6'}).waitFor();
    await page.waitForTimeout(1000);
    await page.screenshot({path:path.join(__dirname,'artifacts','04-原文检索.png'),fullPage:true});
    await page.getByRole('button',{name:'关闭原文'}).click();
    await page.getByRole('button',{name:'全部通知',exact:true}).click();
    await page.getByRole('button',{name:'问答与原文',exact:true}).click();
    assert(await page.locator('.evidence').count()>0,'Evidence should survive tab switch');
    await page.reload();
    await page.getByRole('heading',{name:/下一场比赛/}).waitFor();
    assert.equal(await page.locator('.competition').count(),1,'Saved competition should survive reload');
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:path.join(__dirname,'artifacts','05-手机首页.png'),fullPage:true});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile layout overflows');
    assert.deepEqual(errors,[]);
    console.log('PASS: identity, scanned PDF OCR, confirmation, PDF.js rendering, persistence, search, references, tab state and mobile layout.');
    console.log('Screenshots: website/artifacts');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
