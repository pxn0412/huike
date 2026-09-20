"""Loopback-only development server. Not a public deployment server."""
import base64
import binascii
import json
import os
import re
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from store import Store
import agent
import ai
from parsing import ocr_available

ROOT = Path(__file__).resolve().parent
STORE = Store(os.environ.get('HUIKE_DATA_DIR', str(ROOT / 'data')))


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; worker-src 'self' blob:; frame-src 'self'; object-src 'self'; base-uri 'none'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == '/api/status':
            return self.send(200, {'ai_configured': ai.configured(), 'mode': 'local',
                                   'ocr_available': ocr_available(),
                                   'answer_mode': 'agent' if agent.configured()
                                   else ('local' if ai.configured() else 'keyword')})
        if path.path == '/api/competitions':
            return self.send(200, STORE.competitions())
        if path.path == '/api/documents':
            return self.send(200, STORE.documents(parse_qs(path.query).get('competition_id', [''])[0]))
        match = re.fullmatch(r'/api/documents/([a-f0-9]{32})/chunks', path.path)
        if match:
            return self.send(200, {'chunks': STORE.chunks(match.group(1))})
        match = re.fullmatch(r'/api/(uploads|documents)/([a-f0-9]{32})/file', path.path)
        if match:
            content = STORE.file(*match.groups())
            return self.send(200, content, 'application/pdf') if content else self.send(404, {'error': '资料不存在。'})
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/viewer.html': ('viewer.html', 'text/html; charset=utf-8'),
                 '/viewer.js': ('viewer.js', 'text/javascript; charset=utf-8'),
                 '/viewer.css': ('viewer.css', 'text/css; charset=utf-8'),
                 '/polish.css': ('polish.css', 'text/css; charset=utf-8'),
                 '/final.css': ('final.css', 'text/css; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path.path in files:
            name, mime = files[path.path]
            return self.send(200, (ROOT / 'static' / name).read_bytes(), mime)
        if path.path.startswith('/vendor/'):
            target = (ROOT / 'node_modules' / 'pdfjs-dist' / path.path.removeprefix('/vendor/')).resolve()
            vendor = (ROOT / 'node_modules' / 'pdfjs-dist').resolve()
            if target.is_relative_to(vendor) and target.is_file() and target.suffix in ('.mjs','.bcmap','.wasm','.pfb','.ttf'):
                mime = 'text/javascript' if target.suffix == '.mjs' else 'application/wasm' if target.suffix == '.wasm' else 'application/octet-stream'
                return self.send(200, target.read_bytes(), mime)
        self.send(404, {'error': '页面不存在。'})

    def do_POST(self):
        # Block cross-origin writes to the local service.
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{self.headers.get("Host")}':
            return self.send(403, {'error': '不允许跨站提交。'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': '仅接受 JSON 请求。'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 29 * 1024 * 1024:
                return self.send(413, {'error': '请求为空或文件过大。'})
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('请求格式不正确。')
            if self.path == '/api/upload':
                content = base64.b64decode(data.get('content', ''), validate=True)
                return self.send(200, STORE.upload(str(data.get('filename', '')), content))
            if self.path == '/api/example':
                samples = list((Path.home() / 'Desktop').glob('2026年6月5日*慧科*通知*.pdf'))
                if not samples:
                    raise ValueError('未找到桌面的慧科杯通知，请手动选择 PDF 上传。')
                return self.send(200, STORE.upload(samples[0].name, samples[0].read_bytes()))
            if self.path == '/api/confirm':
                return self.send(200, STORE.confirm(data))
            match = re.fullmatch(r'/api/documents/([a-f0-9]{32})/sync', self.path)
            if match:
                return self.send(200, {'knowledge_base': STORE.sync_knowledge_base(match.group(1))})
            if self.path == '/api/cancel':
                STORE.cancel(data.get('upload_id'))
                return self.send(200, {'ok': True})
            if self.path == '/api/search':
                return self.send(200, {'mode': 'keyword_evidence', 'hits': STORE.search(data.get('competition_id'), data.get('query', ''))})
            if self.path == '/api/ask':
                return self.send(200, STORE.answer(data.get('competition_id'), data.get('query', '')))
            self.send(404, {'error': '接口不存在。'})
        except (ValueError, binascii.Error) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            traceback.print_exc()
            self.send(500, {'error': '处理失败，请重试；原有资料不会被覆盖。'})


def backfill_knowledge_base():
    """启动时在后台把本地还没进知识库的资料补传，不需要人工点。"""
    try:
        pending = STORE.sync_pending_documents()
        if pending:
            print(f'已自动补传 {len(pending)} 份资料到知识库', flush=True)
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    port = int(os.environ.get('HUIKE_PORT', '8765'))
    threading.Thread(target=backfill_knowledge_base, daemon=True).start()
    print(f'慧科本地网站：http://127.0.0.1:{port}', flush=True)
    print('仅本机访问。关闭窗口或按 Ctrl+C 停止服务。', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
