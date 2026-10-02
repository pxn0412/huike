import * as pdfjs from '/vendor/build/pdf.mjs';
pdfjs.GlobalWorkerOptions.workerSrc='/vendor/build/pdf.worker.mjs';
const params=new URLSearchParams(location.search), message=document.querySelector('#message');
let pdf, pageNumber=Number(params.get('page'))||1, zoom=1, rendering=false, pending=false;
const canvas=document.querySelector('canvas');
async function render(){
  if(!pdf)return;
  if(rendering){pending=true;return;}
  rendering=true;
  try{
    pageNumber=Math.max(1,Math.min(pdf.numPages,pageNumber));
    const page=await pdf.getPage(pageNumber), base=page.getViewport({scale:1});
    const scale=Math.max(.1,(document.documentElement.clientWidth-32)/base.width)*zoom;
    const viewport=page.getViewport({scale}), ratio=Math.min(devicePixelRatio||1,2);
    canvas.width=Math.floor(viewport.width*ratio);canvas.height=Math.floor(viewport.height*ratio);
    canvas.style.width=`${viewport.width}px`;canvas.style.height=`${viewport.height}px`;
    await page.render({canvasContext:canvas.getContext('2d'),viewport,transform:[ratio,0,0,ratio,0,0]}).promise;
    document.querySelector('#page').value=pageNumber;
    document.querySelector('#prev').disabled=pageNumber<=1;
    document.querySelector('#next').disabled=pageNumber>=pdf.numPages;
    message.textContent='';
  }catch{message.textContent='当前页预览失败，可在新窗口打开原 PDF。';}
  finally{rendering=false;if(pending){pending=false;render();}}
}
document.querySelector('#prev').onclick=()=>{pageNumber--;render();};
document.querySelector('#next').onclick=()=>{pageNumber++;render();};
document.querySelector('#page').onchange=e=>{pageNumber=Number(e.target.value)||1;render();};
document.querySelector('#zoom-in').onclick=()=>{zoom=Math.min(2,zoom+.2);render();};
document.querySelector('#zoom-out').onclick=()=>{zoom=Math.max(.5,zoom-.2);render();};
let timer;window.addEventListener('resize',()=>{clearTimeout(timer);timer=setTimeout(render,150);});
try{
  const file=params.get('file');
  if(!/^\/api\/(?:uploads|documents)\/[a-f0-9]{32}\/file$/.test(file||''))throw Error('invalid');
  const response=await fetch(file.replace(/\/file$/, '/preview'));
  if(!response.ok)throw Error('preview unavailable');
  const info=await response.json();
  if(info.location_kind==='page'){
    pdf=await pdfjs.getDocument({url:file,cMapUrl:'/vendor/cmaps/',cMapPacked:true,standardFontDataUrl:'/vendor/standard_fonts/',wasmUrl:'/vendor/wasm/',isEvalSupported:false}).promise;
    document.querySelector('#total').textContent=pdf.numPages;document.querySelector('#page').max=pdf.numPages;
    await render();
  }else{
    document.querySelector('.toolbar').hidden=true;
    const paper=document.querySelector('#paper');paper.replaceChildren();
    message.textContent=info.metadata?.warning || '';
    if(info.location_kind==='image' && info.mime_type!=='image/tiff'){
      const img=document.createElement('img');img.src=file;img.alt=info.filename;img.style.maxWidth='100%';paper.append(img);
    }else{
      const label=document.createElement('p');label.textContent='提取正文（不代表原文件排版）；请通过上方入口打开或下载原件核对。';paper.append(label);
    }
    const extractionLabel=document.createElement('p');
    extractionLabel.textContent=info.metadata?.text_extraction_method?.includes('deepseek_vision')
      ? '提取文字（图片部分由 DeepSeek 识别，请对照原件核对）' : '提取文字（请对照原件核对）';
    paper.append(extractionLabel);
    const text=document.createElement('pre');text.className='extracted-text';text.textContent=info.pages.join('\n\n') || '未识别到可用文字。';paper.append(text);
  }
}catch{message.textContent='无法打开原文件，请关闭预览后重试。';}
