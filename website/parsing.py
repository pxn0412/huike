"""Document extraction entry point; PDF keeps its original page numbering."""
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader
import document_vision

ROOT = Path(__file__).resolve().parent


def poppler_binary():
    found = shutil.which('pdftoppm')
    if found:
        return found
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/Library/bin/pdftoppm.exe'
    return str(bundled) if bundled.is_file() else None


def ocr_available():
    return os.name == 'nt' and bool(poppler_binary())


def extract_document(filename, content, use_ocr=True, progress=None):
    # Lazy import keeps the existing PDF-only export script backwards compatible.
    from document_formats import extract_document as dispatch
    return dispatch(filename, content, use_ocr, progress=progress)


def extract(content, use_ocr=True, progress=None):
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError('请先解除 PDF 密码保护，再上传。')
        if not 0 < len(reader.pages) <= 200:
            raise ValueError('请上传 1 至 200 页的 PDF。')
        pages = [p.extract_text() or '' for p in reader.pages]
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('无法解析这份 PDF，请检查文件是否损坏。') from exc
    empty = [i for i, text in enumerate(pages) if not text.strip()]
    metadata = {'ocr_pages': [], 'unreadable_pages': [], 'warning': ''}
    if use_ocr and document_vision.configured():
        metadata['text_extraction_method'] = 'native_text+deepseek_vision'
        def needs_vision(index):
            text = pages[index]
            # Font decoding artifacts and images both require looking at the actual page.
            damaged = '\ufffd' in text or '\x00' in text or '(cid:' in text
            try:
                has_images = bool(reader.pages[index].images)
            except Exception:
                has_images = True
            return not text.strip() or damaged or has_images
        targets = [i for i in range(len(pages)) if needs_vision(i)]
        for i in targets:
            if '\ufffd' in pages[i] or '\x00' in pages[i] or '(cid:' in pages[i]:
                pages[i] = ''
        if targets:
            if not poppler_binary():
                metadata['warning'] = '缺少 PDF 页面渲染工具，扫描及图片内容未识别。'
                metadata['incomplete_pages'] = [i + 1 for i in targets]
            else:
                errors = []
                completed = 0
                def report(number):
                    nonlocal completed
                    completed += 1
                    if progress:
                        progress({'stage': 'recognizing', 'completed': completed,
                                  'total': len(targets), 'page': number})
                if progress:
                    progress({'stage': 'recognizing', 'completed': 0, 'total': len(targets)})
                with tempfile.TemporaryDirectory(prefix='huike-vision-pdf-') as folder:
                    pdf = Path(folder) / 'source.pdf'
                    pdf.write_bytes(content)
                    for start in range(0, len(targets), 6):
                        with tempfile.TemporaryDirectory(dir=folder) as batch:
                            for i in targets[start:start + 6]:
                                try:
                                    subprocess.run([poppler_binary(), '-f', str(i + 1), '-l', str(i + 1),
                                                    '-singlefile', '-scale-to', '3200', '-png', str(pdf),
                                                    str(Path(batch) / f'page-{i+1:04d}')],
                                                   check=True, capture_output=True, timeout=35,
                                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                                except (subprocess.SubprocessError, OSError):
                                    errors.append(f'第 {i+1} 页无法渲染，图片内容未识别。')
                                    metadata.setdefault('incomplete_pages', []).append(i + 1)
                                    report(i + 1)
                            def on_result(item):
                                report(int(re.search(r'page-(\d+)', item['file'])[1]))
                            for item in document_vision.read_folder(batch, on_result=on_result):
                                number = int(re.search(r'page-(\d+)', item['file'])[1])
                                if item['text'].strip():
                                    pages[number - 1] = item['text']
                                    metadata['ocr_pages'].append(number)
                                if item['error']:
                                    errors.append(f'第 {number} 页：{item["error"]}')
                                    metadata.setdefault('incomplete_pages', []).append(number)
                                elif not item['text'].strip() and pages[number - 1].strip():
                                    errors.append(f'第 {number} 页图片未提取到文字，保留原文字层，请核对。')
                    metadata['warning'] = '；'.join(errors)
        metadata['unreadable_pages'] = [i+1 for i,p in enumerate(pages) if not p.strip()]
        return pages, metadata
    if empty and use_ocr and ocr_available():
        if len(empty) > 20:
            metadata['warning'] = '本地版一次最多识别 20 个扫描页，请拆分文件后再添加。'
        else:
            try:
                with tempfile.TemporaryDirectory(prefix='huike-ocr-') as folder:
                    pdf = Path(folder) / 'source.pdf'
                    pdf.write_bytes(content)
                    for i in empty:
                        subprocess.run([poppler_binary(), '-f', str(i+1), '-l', str(i+1),
                                        '-singlefile', '-scale-to', '2400', '-png', str(pdf),
                                        str(Path(folder) / f'page-{i+1:04d}')],
                                       check=True, capture_output=True, timeout=35,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                                             '-File', str(ROOT / 'ocr.ps1'), '-ImageDirectory', folder],
                                            check=True, capture_output=True, timeout=150,
                                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    for item in json.loads(result.stdout.decode('utf-8-sig')):
                        number = int(re.search(r'page-(\d+)', item['file']).group(1))
                        text = re.sub(r'(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])', '', item['text'])
                        pages[number-1] = text
                        if text.strip():
                            metadata['ocr_pages'].append(number)
            except (subprocess.SubprocessError, OSError, ValueError, KeyError):
                metadata['warning'] = '扫描文字识别未完成，原文件已保留，可以重新上传重试。'
    metadata['unreadable_pages'] = [i+1 for i,p in enumerate(pages) if not p.strip()]
    return pages, metadata


def suggest(filename, pages):
    """Conservative filename hints, explicitly not AI extraction."""
    title = Path(filename).stem.strip()
    year = re.search(r'20\d{2}', title)
    result = {'title': title, 'edition': year.group() if year else '', 'name': '',
              'publisher': '', 'published_at': '', 'track': ''}
    match = re.search(r'(?:关于举办|关于开展)(.+?)(?:的通知|通知)$', title)
    if match:
        result['name'] = match.group(1).strip()
    return result
