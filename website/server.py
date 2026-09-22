"""Loopback-only development server. Not a public deployment server."""
import base64
import binascii
import json
import os
import re
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from store import Store
import ai
import clients
import identity
import demo
from parsing import ocr_available

ROOT = Path(__file__).resolve().parent
STORE = Store(os.environ.get('HUIKE_DATA_DIR', str(ROOT / 'data')))

# 静态文件（static/*）是每次请求现读的，改完刷新就生效；但 .py 只在启动时 import 一次，
# 改完必须重启服务。否则会出现"前端是新的、后端还是旧的"这种最难查的假象：
# 招募详情明明有画像却显示"还没有画像"——不是数据丢了，是还在跑改动前的 store.py。
# 所以由服务端自己报出"我比磁盘上的代码旧"，页面直接把这件事写出来（见 /api/status）。
SERVER_SOURCES = ('server.py', 'store.py', 'demo.py', 'ai.py', 'clients.py', 'agent.py',
                  'kb.py', 'parsing.py', 'chunking.py', 'identity.py')
PROCESS_STARTED_AT = time.time()


def stale_sources():
    """比本进程启动时间新的源码文件；空列表表示跑的就是当前磁盘上的代码。"""
    stale = []
    for name in SERVER_SOURCES:
        try:
            if (ROOT / name).stat().st_mtime > PROCESS_STARTED_AT:
                stale.append(name)
        except OSError:
            continue
    return stale

# 招募协助里"这场比赛的要求"只问比赛助手一次，同一身份 + 同一场比赛缓存半小时：
# 用户每聊一轮都重新去检索一次既慢又浪费，而资料本身很少在半小时内变。
BRIEF_TTL_SECONDS = 1800
BRIEF_CACHE = {}
BRIEF_LOCK = threading.Lock()


def cached_brief(owner_id, competition, refresh=False):
    key = (owner_id, competition['id'])
    with BRIEF_LOCK:
        item = BRIEF_CACHE.get(key)
    if item and not refresh and time.time() - item['at'] < BRIEF_TTL_SECONDS:
        return item['brief']
    brief = demo.recruit_brief(STORE, owner_id, competition)
    with BRIEF_LOCK:
        BRIEF_CACHE[key] = {'brief': brief, 'at': time.time()}
    return brief


class Handler(BaseHTTPRequestHandler):
    def prepare_identity(self):
        """读取/签发匿名会话身份。

        只放在服务端 Cookie 里（HttpOnly，前端读不到也改不了）：
        撤销自己发起的版本替换时，判断依据就是这个 ID，不相信前端自报的任何字段。
        """
        self.owner_id, needs_cookie = identity.ensure(self.headers.get('Cookie'))
        self.set_cookie = identity.cookie_header(self.owner_id) if needs_cookie else None
        # 这个浏览器身份有没有绑到账号上（登录过才不是 None）。
        self.user = STORE.user_by_owner(self.owner_id)

    def require_login(self, path):
        """除状态查询和登录本身，其余 /api 接口都要求已登录。未登录返回 401。"""
        if not path.startswith('/api/') or self.user:
            return False
        if path == '/api/status' or path.startswith('/api/auth/'):
            return False
        self.send(401, {'error': '请先登录。'})
        return True

    def authenticate(self, action, data):
        """注册或登录；成功后把身份 Cookie 换成账号绑定的身份。

        注册时把当前浏览器身份绑到账号上（owner_id），所以先匿名用出来的画像、招募不会丢。
        """
        if action == 'register':
            user = STORE.register(data.get('account'), data.get('password'), data.get('name'),
                                  owner_id=self.owner_id)
        else:
            user = STORE.login(data.get('account'), data.get('password'))
        self.set_cookie = identity.cookie_header(user['owner_id'])
        return {key: user[key] for key in ('account', 'kind', 'name')}

    @staticmethod
    def with_owner(data, owner_id):
        """把客户端自报的会话字段丢掉，换成服务端 Cookie 里的身份。"""
        clean = {key: value for key, value in data.items()
                 if key not in ('session_owner_id', 'uploader_session_id')}
        clean['session_owner_id'] = owner_id
        return clean

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        if getattr(self, 'set_cookie', None):
            self.send_header('Set-Cookie', self.set_cookie)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; worker-src 'self' blob:; frame-src 'self'; object-src 'self'; base-uri 'none'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.prepare_identity()
        path = urlparse(self.path)
        if path.path == '/api/auth/session':
            return self.send(200, {'user': self.user})
        if self.require_login(path.path):
            return
        if path.path == '/api/status':
            return self.send(200, {'ai_configured': ai.configured(), 'mode': 'local',
                                   'ocr_available': ocr_available(),
                                   'profile_configured': clients.profile_agent.configured(),
                                   'code_stale': stale_sources(),
                                   'answer_mode': 'agent' if clients.agent_client.configured()
                                   else ('local' if ai.configured() else 'keyword')})
        if path.path == '/api/competitions':
            return self.send(200, STORE.competitions())
        if path.path == '/api/profile':
            return self.send(200, STORE.profile(self.owner_id) or
                             {'profile': None, 'updated_at': None})
        if path.path == '/api/profile/chat':
            return self.send(200, {'messages': STORE.profile_chat(self.owner_id)})
        if path.path == '/api/recruitments':
            params = parse_qs(path.query)
            mine = params.get('mine', [''])[0] == '1'
            return self.send(200, {'recruitments': STORE.recruitments(
                competition_id=params.get('competition_id', [''])[0] or None,
                owner_id=self.owner_id if mine else None, viewer_id=self.owner_id,
                include_closed=mine)})
        match = re.fullmatch(r'/api/recruitments/([a-f0-9]{32})', path.path)
        if match:
            try:
                return self.send(200, {'recruitment': STORE.recruitment(match.group(1), self.owner_id)})
            except ValueError as exc:
                return self.send(404, {'error': str(exc)})
        match = re.fullmatch(r'/api/competitions/([^/]+)/summary', path.path)
        if match:
            cid = match.group(1)
            competition = next((c for c in STORE.competitions() if c['id'] == cid), None)
            if not competition:
                return self.send(404, {'error': '比赛不存在。'})
            return self.send(200, {'competition': competition, 'documents': STORE.documents(cid),
                                  'recruitment_count': STORE.competition_recruitment_count(cid)})
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
                 '/demo.js': ('demo.js', 'text/javascript; charset=utf-8'),
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
        self.prepare_identity()
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
            if self.path in ('/api/auth/login', '/api/auth/register'):
                return self.send(200, {'user': self.authenticate(self.path.rsplit('/', 1)[-1], data)})
            if self.path == '/api/auth/logout':
                self.set_cookie = identity.clear_cookie_header()
                return self.send(200, {'user': None})
            if self.require_login(self.path):
                return
            if self.path == '/api/upload':
                content = base64.b64decode(data.get('content', ''), validate=True)
                return self.send(200, STORE.upload(str(data.get('filename', '')), content))
            if self.path == '/api/example':
                samples = list((Path.home() / 'Desktop').glob('2026年6月5日*慧科*通知*.pdf'))
                if not samples:
                    raise ValueError('未找到桌面的慧科杯通知，请手动选择 PDF 上传。')
                return self.send(200, STORE.upload(samples[0].name, samples[0].read_bytes()))
            if self.path == '/api/confirm':
                # 上传者归属由服务端 Cookie 决定，前端自报的一律丢掉。
                return self.send(200, STORE.confirm(self.with_owner(data, self.owner_id)))
            match = re.fullmatch(r'/api/documents/([a-f0-9]{32})/sync', self.path)
            if match:
                return self.send(200, {'knowledge_base': STORE.sync_knowledge_base(match.group(1))})
            if self.path == '/api/cancel':
                STORE.cancel(data.get('upload_id'))
                return self.send(200, {'ok': True})
            if self.path == '/api/search':
                return self.send(200, {'mode': 'keyword_evidence', 'hits': STORE.search(data.get('competition_id'), data.get('query', ''))})
            if self.path == '/api/ask':
                # 连续追问：同一个浏览器 + 同一场比赛共用一个会话；身份只认服务端 Cookie。
                return self.send(200, demo.ask(STORE, self.owner_id, data))
            if self.path == '/api/profile/chat':
                return self.send(200, demo.chat(STORE, self.owner_id, data))
            if self.path == '/api/profile/draft':
                return self.send(200, demo.draft(STORE, self.owner_id, data))
            if self.path == '/api/profile/chat/reset':
                STORE.clear_profile_messages(self.owner_id)
                return self.send(200, STORE.reset_conversation(self.owner_id, 'profile'))
            if self.path == '/api/profile/save':
                return self.send(200, STORE.save_profile(self.owner_id, demo.profile_payload(data.get('payload'))))
            if self.path == '/api/recruitments/create':
                return self.send(200, {'recruitment': STORE.create_recruitment(self.owner_id, data)})
            if self.path == '/api/recruitments/update':
                return self.send(200, {'recruitment': STORE.update_recruitment(self.owner_id, data.get('id'), data)})
            if self.path == '/api/recruitments/delete':
                return self.send(200, STORE.delete_recruitment(self.owner_id, data.get('id')))
            if self.path == '/api/session/reset':
                return self.send(200, STORE.reset_conversation(self.owner_id,
                                                               data.get('competition_id')))
            if self.path == '/api/profile/ask':
                # 画像智能体（第二个应用）：不带比赛范围，回答不引用比赛资料。
                return self.send(200, STORE.profile_answer(data.get('query', ''),
                                                           owner_id=self.owner_id))
            if self.path in ('/api/recruit/brief', '/api/recruit/chat', '/api/recruit/draft',
                             '/api/recruit/reset'):
                # 招募协助：比赛范围由服务端按 competition_id 锁定，不信前端传的比赛名。
                cid = str(data.get('competition_id') or '').strip()
                competition = next((c for c in STORE.competitions() if c['id'] == cid), None)
                if not competition:
                    raise ValueError('请先选择对应的比赛。')
                if self.path == '/api/recruit/brief':
                    return self.send(200, {'brief': cached_brief(self.owner_id, competition,
                                                                 bool(data.get('refresh')))})
                if self.path == '/api/recruit/chat':
                    brief = cached_brief(self.owner_id, competition)
                    return self.send(200, demo.recruit_chat(STORE, self.owner_id, data,
                                                            competition, brief))
                if self.path == '/api/recruit/draft':
                    brief = cached_brief(self.owner_id, competition)
                    return self.send(200, demo.recruit_draft(STORE, self.owner_id, data,
                                                             competition, brief))
                return self.send(200, STORE.reset_conversation(self.owner_id,
                                                              f'recruit:{competition["id"]}'))
            self.send(404, {'error': '接口不存在。'})
        except (ValueError, binascii.Error) as exc:
            self.send(400, {'error': str(exc)})
        except Exception:
            traceback.print_exc()
            self.send(500, {'error': '处理失败，请重试；原有资料不会被覆盖。'})


def backfill_knowledge_base():
    """启动时先认领知识库里已有的文档，再把本地还没进库的补传。

    顺序不能反：认领会把 kb_doc_id/kb_file_name 补上，这些资料就不会再被当成
    “还没进库”而重复上传（老资料尤其如此，它们是用命令行传进去的）。
    """
    try:
        linked = STORE.link_knowledge_documents()
        if linked['linked']:
            print(f'已认领 {len(linked["linked"])} 份知识库已有文档', flush=True)
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
