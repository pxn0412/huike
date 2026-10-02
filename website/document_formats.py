"""Validated original files → text units + honest source locations (no generated facts)."""
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import warnings
import zipfile
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn
from PIL import Image, ImageOps

import parsing
import document_vision

MIME_TYPES = {
    '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.doc': 'application/msword', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.bmp': 'image/bmp', '.tif': 'image/tiff', '.tiff': 'image/tiff',
    '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8',
}
IMAGE_FORMATS = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP',
                 '.bmp': 'BMP', '.tif': 'TIFF', '.tiff': 'TIFF'}
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 2_000_000
WORD_LOCK = threading.Lock()


def mime_type(filename):
    return MIME_TYPES.get(Path(filename).suffix.lower(), 'application/octet-stream')


def location_kind(filename):
    suffix = Path(filename).suffix.lower()
    return 'page' if suffix == '.pdf' else 'image' if suffix in IMAGE_FORMATS else 'section'


def extract_document(filename, content, use_ocr=True, progress=None):
    ext = Path(filename).suffix.lower()
    if ext not in MIME_TYPES:
        raise ValueError('支持 PDF、Word（.docx/.doc）、PNG/JPG/WebP/BMP/TIFF 图片、TXT 和 Markdown。')
    if not content:
        raise ValueError('文件为空，请重新选择。')
    if len(content) > MAX_BYTES:
        raise ValueError('文件不能超过 20 MB。')
    meta = {'source_type': ext[1:], 'mime_type': mime_type(filename),
            'location_kind': location_kind(filename), 'ocr_pages': [], 'unreadable_pages': [], 'warning': '',
            'text_extraction_method': 'native_text'}
    if ext == '.pdf':
        if not content.startswith(b'%PDF-'):
            raise ValueError('PDF 内容与格式不符，请检查文件。')
        pages, pdf_meta = parsing.extract(content, use_ocr, progress=progress)
        meta.update(pdf_meta)
    elif ext in IMAGE_FORMATS:
        pages, image_meta = extract_image(content, IMAGE_FORMATS[ext], use_ocr)
        meta.update(image_meta)
    elif ext in ('.docx', '.doc'):
        if ext == '.doc':
            content = convert_doc(content)
        pages, word_warning = extract_docx(content, use_ocr)
        meta['warning'] = word_warning
        if use_ocr and document_vision.configured():
            meta['text_extraction_method'] = 'native_text+deepseek_vision'
    else:
        pages = [decode_text(content)]
    if sum(len(p) for p in pages) > MAX_TEXT:
        raise ValueError('提取出的文字过多，请拆分资料后上传。')
    meta['unreadable_pages'] = [i + 1 for i, text in enumerate(pages) if not text.strip()]
    if meta['unreadable_pages'] and not meta['warning']:
        meta['warning'] = '部分内容未识别到文字，原文件仍可查看；未识别内容不参与问答。'
    return pages, meta


def decode_text(content):
    encodings = ('utf-16',) if content.startswith((b'\xff\xfe', b'\xfe\xff')) else ('utf-8-sig', 'gb18030')
    for encoding in encodings:
        try:
            text = content.decode(encoding)
        except UnicodeError:
            continue
        if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
            break
        return text.replace('\r\n', '\n').replace('\r', '\n')
    raise ValueError('文本编码无法识别，或内容不是文本；请另存为 UTF-8 文本。')


def run_image_ocr(folder):
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(Path(__file__).with_name('ocr.ps1')), '-ImageDirectory', str(folder)],
        check=True, capture_output=True, timeout=150,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return json.loads(result.stdout.decode('utf-8-sig'))


def extract_image(content, expected_format, use_ocr):
    meta = {'ocr_pages': [], 'warning': ''}
    with tempfile.TemporaryDirectory(prefix='huike-image-') as folder:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as source:
                    if source.format != expected_format:
                        raise ValueError('图片实际格式与文件扩展名不一致。')
                    count = getattr(source, 'n_frames', 1)
                    if not 1 <= count <= 20:
                        raise ValueError('一次最多识别 20 帧图片，请拆分后上传。')
                    pixels = 0
                    for i in range(count):
                        source.seek(i)
                        pixels += source.width * source.height
                        if pixels > 80_000_000:
                            raise ValueError('图片总像素过大，请缩小后上传。')
                        frame = ImageOps.exif_transpose(source).convert('RGB')
                        frame.thumbnail((3600, 3600))
                        frame.save(Path(folder) / f'page-{i+1:04d}.png')
        except ValueError:
            raise
        except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError('无法解析图片，请检查是否损坏或尺寸过大。') from exc
        pages = [''] * count
        if use_ocr and document_vision.configured():
            meta['text_extraction_method'] = 'deepseek_vision'
            errors = []
            for item in document_vision.read_folder(folder):
                number = int(re.search(r'page-(\d+)', item['file'])[1])
                pages[number - 1] = item['text']
                if item['text'].strip():
                    meta['ocr_pages'].append(number)
                if item['error']:
                    errors.append(f'图片 {number}：{item["error"]}')
            meta['warning'] = '；'.join(errors)
        elif use_ocr and os.name == 'nt':
            meta['text_extraction_method'] = 'windows_ocr'
            try:
                for item in run_image_ocr(folder):
                    match = re.fullmatch(r'page-(\d+)\.png', item['file'])
                    if match and 1 <= int(match[1]) <= count:
                        number = int(match[1])
                        pages[number - 1] = re.sub(r'(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])', '', item['text'])
                        if pages[number - 1].strip():
                            meta['ocr_pages'].append(number)
            except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
                meta['warning'] = '图片文字识别未完成；原图已保留，请重试或上传含文字的 Word/PDF。'
        else:
            meta['warning'] = '当前环境未启用图片 OCR，已保留原图，但暂时无法检索图中文字。'
    return pages, meta


def extract_docx(content, use_ocr=True):
    try:
        # Bound decompression before python-docx loads XML and embedded files.
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > 5000 or sum(x.file_size for x in infos) > 80 * 1024 * 1024:
                raise ValueError('Word 解压后内容过大，请拆分资料。')
            if 'word/document.xml' not in archive.namelist():
                raise ValueError('这不是有效的 DOCX 文档。')
        doc = Document(io.BytesIO(content))
        pieces = []
        image_cache = {}
        image_issues = []
        vision_enabled = use_ocr and document_vision.configured()

        def ordered_items(element, parent):
            # python-docx's public iterator skips content controls in form templates.
            for child in element:
                if child.tag == qn('w:p'):
                    yield Paragraph(child, parent)
                elif child.tag == qn('w:tbl'):
                    yield Table(child, parent)
                elif child.tag in (qn('w:sdt'), qn('w:sdtContent'), qn('w:customXml'), qn('w:ins')):
                    yield from ordered_items(child, parent)

        def paragraph_text(item):
            nodes = item._p.xpath('.//w:t[not(ancestor::w:del)] | .//w:tab | .//w:br | .//a:blip | .//*[local-name()="imagedata"]')
            text = ''
            for node in nodes:
                if node.tag in (qn('w:t'), qn('w:tab'), qn('w:br')):
                    text += '\t' if node.tag == qn('w:tab') else '\n' if node.tag == qn('w:br') else node.text or ''
                    continue
                rid = node.get(qn('r:embed')) or node.get(qn('r:id'))
                if not rid:
                    image_issues.append('外链图片未读取')
                    continue
                if rid not in image_cache:
                    if not vision_enabled:
                        image_cache[rid] = ''
                        image_issues.append('未启用图片识别')
                    elif len(image_cache) >= 20:
                        image_cache[rid] = ''
                        image_issues.append('超过单份 Word 20 张图片的识别上限')
                    else:
                        try:
                            image_cache[rid] = document_vision.read_image(doc.part.related_parts[rid].blob)
                        except (ValueError, KeyError) as exc:
                            image_cache[rid] = getattr(exc, 'partial_text', '')
                            image_issues.append(f'嵌入图片 {len(image_cache)} 未完成识别：{exc}')
                if image_cache[rid].strip():
                    text += '\n\n【本段嵌入图片文字】\n' + image_cache[rid] + '\n\n'
            return text

        def table_text(table):
            rows = []
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    parts = []
                    for item in ordered_items(cell._tc, cell):
                        parts.append(table_text(item) if isinstance(item, Table) else paragraph_text(item))
                    cells.append(' / '.join(p.strip() for p in parts if p.strip()).replace('|', '\\|').replace('\n', ' / '))
                rows.append('| ' + ' | '.join(cells) + ' |')
            if rows:
                rows.insert(1, '| ' + ' | '.join('---' for _ in table.rows[0].cells) + ' |')
            return '\n'.join(rows)

        for item in ordered_items(doc.element.body, doc):
            if isinstance(item, Table):
                pieces.append(table_text(item))
            else:
                text = paragraph_text(item).strip()
                if not text:
                    continue
                style = item.style.name if item.style else ''
                heading = re.fullmatch(r'(?:Heading|标题)\s*(\d)', style, re.I)
                pieces.append(('#' * min(int(heading[1]), 6) + ' ' if heading else '') + text)
        warning = 'Word 显示提取正文，排版页码未确认；可下载原文件核对。'
        if image_issues:
            warning += '；'.join(dict.fromkeys(image_issues)) + ' 未识别或不确定的段落不参与问答，可单独上传清晰图片。'
        return ['\n\n'.join(pieces)], warning
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('无法解析 Word 文件，请检查是否损坏、加密，或另存为 .docx。') from exc


def convert_doc(content):
    if not content.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
        raise ValueError('这不是有效的旧版 Word（.doc）文件，请另存为 .docx。')
    if os.name != 'nt':
        raise ValueError('旧版 .doc 转换需要本机 Microsoft Word，请另存为 .docx 后上传。')
    with WORD_LOCK, tempfile.TemporaryDirectory(prefix='huike-word-') as folder:
        source = Path(folder) / 'source.doc'
        target = Path(folder) / 'converted.docx'
        owner_file = Path(folder) / 'word-process.json'
        source.write_bytes(content)
        command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(Path(__file__).with_name('convert_word.ps1')),
                   '-SourcePath', str(source), '-TargetPath', str(target), '-OwnerFile', str(owner_file)]
        try:
            subprocess.run(command,
                           check=True, capture_output=True, timeout=90,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return target.read_bytes()
        except subprocess.TimeoutExpired as exc:
            try:
                subprocess.run(command + ['-CleanupOnly'], check=True, capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.SubprocessError):
                pass
            raise ValueError('旧版 Word 转换超时，请另存为 .docx 后上传。') from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError('旧版 Word 转换失败或超时，请使用 Word 另存为 .docx 后上传。') from exc
