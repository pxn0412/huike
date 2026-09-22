"""Local persistence and page-based evidence. No model-generated facts.

表结构随代码一起演进，所以这里带一个最小迁移器：v1（只有 hash、把知识库状态塞在
metadata 里）→ v2（双哈希 + document_status + kb_* 列）。迁移前先备份 sqlite 文件。
"""
import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from parsing import extract, suggest
import ai
import chunking
import clients

# v1 = 只有 hash，知识库状态塞在 metadata.knowledge_base 里；
# v2 = source/indexed 双哈希 + document_status + kb_* 列（单一来源，不再写 metadata.knowledge_base）。
# SQLite 不能改 UNIQUE 约束，所以 v1 → v2 需要重建一次 documents 表；之后只加列，不再重建。
SCHEMA_VERSION = 2
DOCUMENT_STATUSES = ('candidate', 'current', 'superseded', 'withdrawn')

# 登录账号：邮箱或手机号 + 密码。一个账号对应一个本机身份（owner_id），
# 画像、招募、上传的资料都记在这个身份下，所以换台设备登录还能看到自己的东西。
EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE_PATTERN = re.compile(r'^1[3-9]\d{9}$')
PASSWORD_MIN = 6
PASSWORD_ROUNDS = 200_000


def normalize_account(raw):
    """把用户输入的账号规范成 (类型, 账号)；不合法就报错。

    手机号只留数字（容忍空格、括号、短横线）；邮箱统一小写。
    """
    value = str(raw or '').strip()
    digits = re.sub(r'[\s\-()]', '', value)
    if PHONE_PATTERN.fullmatch(digits):
        return 'phone', digits
    if EMAIL_PATTERN.fullmatch(value.lower()):
        return 'email', value.lower()
    raise ValueError('请输入正确的邮箱或手机号。')


def password_digest(password, salt):
    """密码只存加盐摘要，不存明文；标准库实现，不引新依赖。"""
    return hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'),
                               bytes.fromhex(salt), PASSWORD_ROUNDS).hex()


def per_document(hits, limit=3):
    """回答页要的是"依据在哪"，不是把同一份资料的相关段落全铺开。

    同一份资料只留最相关的那一块（hits 已按分数排好序），最多 `limit` 份。
    """
    seen, picked = set(), []
    for hit in hits:
        key = hit.get('document_id') or hit.get('title') or ''
        if key in seen:
            continue
        seen.add(key)
        picked.append(hit)
        if len(picked) >= limit:
            break
    return picked


def default_name(kind, account):
    """第一次登录没填显示名称时的兜底：给个能认出来的名字，不要真名也能用。"""
    if kind == 'email':
        return (account.split('@')[0] or '同学')[:40]
    return '同学' + account[-4:]

# documents 表的列定义只写一次：建表与重建共用，避免迁移和正常建表漂移。
DOCUMENTS_DDL = '''
    id TEXT PRIMARY KEY, competition_id TEXT NOT NULL,
    filename TEXT, title TEXT, publisher TEXT, published_at TEXT,
    uploaded_at TEXT, uploader_name TEXT, uploader_role TEXT, uploader_session_id TEXT,
    track TEXT, content BLOB,
    source_content_hash TEXT, indexed_content_hash TEXT, pages TEXT,
    document_status TEXT NOT NULL DEFAULT 'candidate',
    version_group_id TEXT, superseded_by_document_id TEXT, kb_switch_before TEXT,
    kb_doc_id TEXT, kb_file_name TEXT, kb_status TEXT, kb_status_desc TEXT, kb_checked_at TEXT,
    metadata TEXT DEFAULT '{}',
    UNIQUE(competition_id, source_content_hash),
    FOREIGN KEY(competition_id) REFERENCES competitions(id)'''


class Store:
    def __init__(self, root, use_ocr=True):
        self.use_ocr = use_ocr
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_database()
        with self.db() as db:
            db.executescript(f'''
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, filename TEXT, content BLOB, hash TEXT,
                    pages TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS competitions (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, edition TEXT NOT NULL,
                    UNIQUE(name, edition));
                CREATE TABLE IF NOT EXISTS documents ({DOCUMENTS_DDL});
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, competition_id TEXT NOT NULL,
                    seq INTEGER NOT NULL, label TEXT, page INTEGER, page_end INTEGER,
                    heading TEXT, track TEXT, ocr INTEGER DEFAULT 0, chars INTEGER,
                    text TEXT NOT NULL, source TEXT,
                    FOREIGN KEY(document_id) REFERENCES documents(id));
                -- 连续追问：同一个人（服务端签发的匿名 ID）在同一场比赛里共用一个会话。
                -- 只加表、不动 documents，所以不需要重建表。
                CREATE TABLE IF NOT EXISTS agent_sessions (
                    session_owner_id TEXT NOT NULL, competition_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, updated_at TEXT,
                    PRIMARY KEY (session_owner_id, competition_id));
                -- 学生画像（初赛版：一份画像 + 展示开关，不做版本历史）
                CREATE TABLE IF NOT EXISTS profiles (
                    owner_id TEXT PRIMARY KEY, payload TEXT,
                    visibility TEXT DEFAULT 'private', updated_at TEXT);
                -- 画像助手的对话记录：消息落库，刷新/换台电脑都不丢对话
                CREATE TABLE IF NOT EXISTS profile_messages (
                    owner_id TEXT NOT NULL, seq INTEGER NOT NULL, role TEXT NOT NULL,
                    text TEXT NOT NULL, created_at TEXT,
                    PRIMARY KEY (owner_id, seq));
                -- 我关注的比赛（必须用户主动点，不自动加入）
                CREATE TABLE IF NOT EXISTS follows (
                    owner_id TEXT NOT NULL, competition_id TEXT NOT NULL, created_at TEXT,
                    PRIMARY KEY (owner_id, competition_id));
                -- 队伍招募（普通产品功能，不经 AI；只有 open/closed 两个状态）
                CREATE TABLE IF NOT EXISTS recruitments (
                    id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, competition_id TEXT,
                    title TEXT, idea TEXT, want_role TEXT, need_count INTEGER DEFAULT 1,
                    contact TEXT, show_profile INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'open', created_at TEXT);
                -- 登录账号（邮箱或手机号）→ 本机身份。账号第一次登录时自动创建。
                CREATE TABLE IF NOT EXISTS users (
                    account TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    password_hash TEXT NOT NULL, salt TEXT NOT NULL,
                    name TEXT NOT NULL, owner_id TEXT NOT NULL, created_at TEXT);
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_competition ON chunks(competition_id);
            ''')
            for table in ('uploads', 'documents'):
                fields = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if 'metadata' not in fields:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN metadata TEXT DEFAULT '{{}}'")
            if 'publisher_name' not in {row['name'] for row in db.execute('PRAGMA table_info(recruitments)')}:
                db.execute("ALTER TABLE recruitments ADD COLUMN publisher_name TEXT NOT NULL DEFAULT '参赛者'")
            # 已入库但还没切块的资料要补上，历史资料也必须能按块检索。
            pending = db.execute('''SELECT d.id, d.competition_id, d.pages, d.track, d.metadata
                FROM documents d WHERE NOT EXISTS
                (SELECT 1 FROM chunks c WHERE c.document_id = d.id)''').fetchall()
            for row in pending:
                self._index_document(db, row['id'], row['competition_id'],
                                     json.loads(row['pages']), row['track'] or '',
                                     json.loads(row['metadata'] or '{}'))
            # 表结构版本号：迁移器只认它，新库在这里初始化。
            if not db.execute('SELECT 1 FROM schema_version').fetchone():
                db.execute('INSERT INTO schema_version (version) VALUES (?)', (SCHEMA_VERSION,))
            # Abandoned uploads are temporary, never public records.
            db.execute("DELETE FROM uploads WHERE julianday(created_at) < julianday('now', '-1 day')")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / 'huike.sqlite3')
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def schema_version(self):
        with self.db() as db:
            row = db.execute('SELECT MAX(version) v FROM schema_version').fetchone()
            return row['v'] or 0

    def _migrate_legacy_database(self):
        """v1 → v2：重建一次 documents 表。

        老库里知识库状态塞在 metadata.knowledge_base 里，且 UNIQUE 约束建在 hash 上；
        SQLite 改不了约束，只能新建表、搬数据、换名。迁移前先备份整个 sqlite 文件，
        出问题可以整份退回去。整个过程只跑一次（靠 source_content_hash 列是否存在判断）。
        """
        path = self.root / 'huike.sqlite3'
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        try:
            # 重建父表要先关外键校验，否则 DROP 会被 chunks 的外键挡住（必须在事务外设置）。
            db.execute('PRAGMA foreign_keys = OFF')
            fields = {row['name'] for row in db.execute('PRAGMA table_info(documents)')}
            if not fields or 'source_content_hash' in fields:
                return  # 空库或已迁移：交给正常 DDL
            rows = [dict(row) for row in db.execute('SELECT * FROM documents')]
            self._backup_database(path)
            db.execute(f'CREATE TABLE documents_new ({DOCUMENTS_DDL})')
            for row in rows:
                metadata = json.loads(row.get('metadata') or '{}')
                kb = metadata.pop('knowledge_base', None) or {}
                db.execute('''INSERT INTO documents_new (id,competition_id,filename,title,publisher,
                    published_at,uploaded_at,uploader_name,uploader_role,track,content,
                    source_content_hash,pages,document_status,version_group_id,
                    kb_doc_id,kb_file_name,kb_status,kb_status_desc,metadata)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'current',?,?,?,?,?,?)''', (
                    row.get('id'), row.get('competition_id'), row.get('filename'), row.get('title'),
                    row.get('publisher'), row.get('published_at'), row.get('uploaded_at'),
                    row.get('uploader'), row.get('role'), row.get('track'), row.get('content'),
                    row.get('hash'), row.get('pages'), row.get('id'),
                    str(kb.get('doc_id') or ''), str(kb.get('filename') or ''),
                    str(kb.get('status') or ''), str(kb.get('message') or ''),
                    json.dumps(metadata, ensure_ascii=False)))
            db.execute('DROP TABLE documents')
            db.execute('ALTER TABLE documents_new RENAME TO documents')
            db.execute('CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)')
            db.execute('INSERT INTO schema_version (version) VALUES (?)', (SCHEMA_VERSION,))
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _backup_database(path):
        """迁移前备份：<库文件>.bak-<时间戳>。迁移失败可以整份换回来。"""
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        shutil.copy2(path, path.with_name(f'{path.name}.bak-{stamp}'))

    def _promote_unclaimed(self, db, document_id):
        """把还没被声明为新版本候选的资料提升为 current。

        candidate 的语义是“已上传、但还没成为当前版本”：第一批还没有资料关系，
        所以确认后直接提升；等版本切换那一步落地时，被声明为替换的新版会停在 candidate，
        直到平台侧切换成功才提升（见 P3/P4）。
        """
        has_relations = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                   "AND name='document_relations'").fetchone()
        if has_relations:
            claimed = db.execute('SELECT 1 FROM document_relations WHERE source_document_id=?',
                                 (document_id,)).fetchone()
            if claimed:
                return
        db.execute("UPDATE documents SET document_status='current' "
                   "WHERE id=? AND document_status='candidate'", (document_id,))

    def upload(self, filename, content):
        if not filename.lower().endswith('.pdf') or not content.startswith(b'%PDF-'):
            raise ValueError('请选择有效的 PDF 文件。')
        if len(content) > 20 * 1024 * 1024:
            raise ValueError('PDF 不能超过 20 MB。')
        pages, metadata = extract(content, self.use_ocr)
        suggestions = suggest(filename, pages)
        method = 'filename_only'
        if ai.configured() and any(p.strip() for p in pages):
            try:
                extracted = ai.extract_identity(filename, pages)
                # 模型回答“未知”时不能把文件名里已经确定的线索（例如届次年份）擦掉。
                suggestions.update({key: value for key, value in extracted.items() if value})
                method = 'ai'
            except ValueError as exc:
                metadata['ai_warning'] = str(exc)
        metadata.update(extraction_method=method, ai_extracted_result=suggestions.copy(),
                        ai_extracted_at=datetime.now(timezone.utc).isoformat() if method == 'ai' else None)
        uid = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.db() as db:
            db.execute('INSERT INTO uploads (id,filename,content,hash,pages,created_at,metadata) VALUES (?,?,?,?,?,?,?)', (
                uid, Path(filename).name, content, hashlib.sha256(content).hexdigest(),
                json.dumps(pages, ensure_ascii=False), now, json.dumps(metadata, ensure_ascii=False)))
        return {'upload_id': uid, 'filename': filename, 'page_count': len(pages),
                'has_text': any(p.strip() for p in pages),
                'suggested': suggestions, 'metadata': metadata,
                'extraction_method': method, 'preview_url': f'/api/uploads/{uid}/file'}

    def cancel(self, uid):
        with self.db() as db:
            db.execute('DELETE FROM uploads WHERE id = ?', (uid,))

    def confirm(self, data):
        title = str(data.get('title', '')).strip()
        name = str(data.get('name', '')).strip()
        edition = str(data.get('edition', '')).strip()
        uploader = str(data.get('uploader', '')).strip()
        role = data.get('role') or 'user'
        date = str(data.get('published_at', '')).strip()
        if not title or not uploader or role not in ('student', 'teacher', 'user'):
            raise ValueError('请填写资料标题和上传者名称。')
        if date:
            try:
                datetime.strptime(date, '%Y-%m-%d')
            except ValueError as exc:
                raise ValueError('发布时间格式应为年-月-日，未知可以留空。') from exc
        cid = data.get('competition_id')
        if not cid and (not name or not edition):
            raise ValueError('请填写比赛名称和届次，或选择已有比赛。')
        with self.db() as db:
            upload = db.execute('SELECT * FROM uploads WHERE id = ?', (data.get('upload_id'),)).fetchone()
            if not upload:
                raise ValueError('临时上传已失效，请重新上传。')
            if cid:
                if not db.execute('SELECT id FROM competitions WHERE id=?', (cid,)).fetchone():
                    raise ValueError('所选比赛不存在，请重新选择。')
            else:
                existing = db.execute('SELECT id FROM competitions WHERE name=? AND edition=?', (name, edition)).fetchone()
                cid = existing['id'] if existing else uuid.uuid4().hex
                if not existing:
                    db.execute('INSERT INTO competitions VALUES (?,?,?)', (cid, name, edition))
            duplicate = db.execute('SELECT id FROM documents WHERE competition_id=? AND source_content_hash=?',
                                   (cid, upload['hash'])).fetchone()
            if duplicate:
                db.execute('DELETE FROM uploads WHERE id=?', (upload['id'],))
                return {'competition_id': cid, 'document_id': duplicate['id'], 'duplicate': True}
            did = uuid.uuid4().hex
            # 归属判断只认服务端签发的匿名会话 ID（server.py 注入），不接受前端自报的值。
            owner = str(data.get('session_owner_id') or '').strip() or None
            db.execute('''INSERT INTO documents (id,competition_id,filename,title,publisher,
                published_at,uploaded_at,uploader_name,uploader_role,uploader_session_id,track,
                content,source_content_hash,pages,document_status,version_group_id,metadata)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'candidate',?,?)''', (
                did, cid, upload['filename'], title, str(data.get('publisher', '')).strip(), date,
                datetime.now(timezone.utc).isoformat(), uploader, role, owner,
                str(data.get('track', '')).strip(), upload['content'], upload['hash'],
                upload['pages'], did, upload['metadata']))
            # 第一批还没有资料关系，所以新资料确认后立刻成为 current；
            # 声明为“新版本”的资料会停在 candidate，等平台侧切换成功才提升。
            self._promote_unclaimed(db, did)
            db.execute('DELETE FROM uploads WHERE id=?', (upload['id'],))
            chunk_count = self._index_document(db, did, cid, json.loads(upload['pages']),
                                               str(data.get('track', '')).strip(),
                                               json.loads(upload['metadata'] or '{}'))
        # 本地入库成功后，把这份资料自动送进 ADP 知识库（失败只记状态，不影响本地入库）。
        knowledge_base = self.sync_knowledge_base(did)
        return {'competition_id': cid, 'document_id': did, 'duplicate': False,
                'chunk_count': chunk_count, 'knowledge_base': knowledge_base}

    def competitions(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('''SELECT c.*, COUNT(d.id) document_count
                FROM competitions c LEFT JOIN documents d ON d.competition_id=c.id
                GROUP BY c.id ORDER BY c.name, c.edition DESC''')]

    def documents(self, cid):
        with self.db() as db:
            rows = db.execute('''SELECT d.id, d.competition_id, d.filename, d.title, d.publisher,
                d.published_at, d.uploaded_at, d.uploader_name, d.uploader_role,
                d.uploader_name AS uploader, d.uploader_role AS role,
                d.document_status, d.kb_status, d.kb_doc_id, d.kb_file_name, d.kb_status_desc,
                d.source_content_hash, d.indexed_content_hash,
                d.track, d.pages, d.metadata,
                (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) chunk_count FROM documents d
                WHERE d.competition_id=? ORDER BY COALESCE(NULLIF(d.published_at,''),d.uploaded_at) DESC''', (cid,))
            result = []
            for row in rows:
                item = dict(row)
                pages = json.loads(item.pop('pages'))
                item['metadata'] = json.loads(item['metadata'])
                item.update(page_count=len(pages), has_text=any(p.strip() for p in pages))
                result.append(item)
            return result

    def file(self, kind, uid):
        table = 'uploads' if kind == 'uploads' else 'documents'
        with self.db() as db:
            row = db.execute(f'SELECT content FROM {table} WHERE id=?', (uid,)).fetchone()
            return bytes(row['content']) if row else None

    def sync_knowledge_base(self, document_id):
        """把一份资料按同一套规则切成块，送进 ADP 知识库。

        自动上传失败不能影响本地已保存的资料：这里只记录状态，不抛异常。
        """
        with self.db() as db:
            row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
            if not row:
                raise ValueError('资料不存在。')
            competition = db.execute('SELECT name, edition FROM competitions WHERE id=?',
                                     (row['competition_id'],)).fetchone()
            pages = json.loads(row['pages'])
            metadata = json.loads(row['metadata'] or '{}')
            blocks = chunking.build_blocks([(number, text) for number, text in enumerate(pages, 1)])
            if not blocks:
                outcome = {'status': 'skipped', 'doc_id': '',
                           'message': '这份资料没有可切分的正文（可能是扫描页）'}
                db.execute('UPDATE documents SET kb_status=?, kb_status_desc=?, kb_checked_at=? WHERE id=?',
                           (outcome['status'], outcome['message'],
                            datetime.now(timezone.utc).isoformat(), document_id))
                return outcome
            header = [('比赛', f"{competition['name']}（{competition['edition']}）" if competition
                       else '未确认'),
                      ('资料类型', '比赛资料'),
                      ('来源文件', row['filename'] or ''),
                      ('发布时间', row['published_at'] or '未标注')]
            if row['track']:
                header.append(('适用赛道', row['track']))
            content = chunking.render_markdown(blocks, row['title'], header).encode('utf-8')
            indexed_hash = hashlib.sha256(content).hexdigest()
            filename = self._knowledge_base_filename(row['competition_id'], document_id, indexed_hash)
            outcome = clients.knowledge_base.push(content, filename)
            checked_at = datetime.now(timezone.utc).isoformat()
            db.execute('''UPDATE documents SET indexed_content_hash=?, kb_doc_id=?, kb_file_name=?,
                kb_status=?, kb_status_desc=?, kb_checked_at=?, metadata=? WHERE id=?''', (
                indexed_hash, str(outcome.get('doc_id') or ''), filename,
                str(outcome.get('status') or ''), str(outcome.get('message') or '')[:300],
                checked_at, json.dumps(metadata, ensure_ascii=False), document_id))
            result = {**outcome, 'filename': filename, 'chunk_count': len(blocks),
                      'synced_at': checked_at}
        return result

    def sync_pending_documents(self, limit=5):
        """把本地还没进知识库的资料补传（老资料，或上次上传失败的）。

        返回需要补传的资料 ID 列表；服务启动时会在后台跑一次，不需要人工点。
        """
        with self.db() as db:
            pending = [row['id'] for row in db.execute('SELECT id, kb_status FROM documents')
                       if row['kb_status'] not in ('uploaded', 'duplicate')]
        for document_id in pending[:limit]:
            try:
                self.sync_knowledge_base(document_id)
            except ValueError:
                continue
        return pending

    @staticmethod
    def _knowledge_base_filename(competition_id, document_id, indexed_hash):
        """知识库里的技术文件名：{比赛ID}-{资料ID}-{上传内容哈希前12位}.md

        以前用“标题-日期”当文件名，平台又只按文件名判重 —— 同名通知、同名不同届
        都会被当成重复拒收，真正的新版本进不了库。改成带比赛 ID 和内容哈希的名字后：
            内容没变 → 名字没变 → 真的重复，跳过；
            内容变了 → 名字变了 → 新版本正常入库，旧版继续留着。
        前缀带比赛 ID，也让“回答引用的文件属于哪场比赛”可以确定性核对（比赛隔离用得上）。
        至于文件名不好看：展示时用本地资料表把这段名字翻回标题，不指望知识库文件名好看。
        """
        return f'{competition_id}-{document_id}-{indexed_hash[:12]}.md'

    @staticmethod
    def _terms(query):
        """把问题拆成检索词：英文数字词 + 中文二字片段 + 少量同义词。"""
        terms = set(re.findall(r'[a-zA-Z0-9]{2,}', query.lower()))
        for part in re.findall(r'[\u4e00-\u9fff]+', query):
            terms.update(part[i:i+2] for i in range(len(part)-1))
        for key, synonyms in {'一个人': ['人数', '组队', '单人'], '截止': ['截止', '提交'],
                              '老师': ['指导', '教师'], '报名': ['报名', '参赛对象'],
                              '材料': ['提交', '作品'], '培训': ['培训', '课程']}.items():
            if key in query:
                terms.update(synonyms)
        return terms

    def _index_document(self, db, document_id, competition_id, pages, track='', metadata=None):
        """把入库正文按规则切成块写进 chunks；切块异常时退回整页块，不影响入库。"""
        pairs = [(number, str(text)) for number, text in enumerate(pages, 1)]
        source = 'chunking'
        try:
            blocks = chunking.build_blocks(pairs)
        except Exception:  # 切块只是索引，不能因为格式异常让资料入不了库
            blocks = []
        if not blocks:
            source = 'page_fallback'
            blocks = [{'page': number, 'page_end': number, 'heading': '', 'chunk_id': f'N{number:02d}',
                       'track': None, 'text': text.strip()} for number, text in pairs if text.strip()]
        ocr_pages = {int(page) for page in (metadata or {}).get('ocr_pages', []) if str(page).isdigit()}
        rows = []
        for seq, block in enumerate(blocks, 1):
            label = str(block.get('chunk_id') or f'N{seq:02d}')
            page = int(block.get('page') or 1)
            page_end = int(block.get('page_end') or page)
            rows.append((f'{document_id}:{label}', document_id, competition_id, seq, label, page, page_end,
                         str(block.get('heading') or ''), track or str(block.get('track') or ''),
                         1 if any(number in ocr_pages for number in range(page, page_end + 1)) else 0,
                         len(str(block.get('text') or '')), str(block.get('text') or ''), source))
        db.execute('DELETE FROM chunks WHERE document_id=?', (document_id,))
        db.executemany('''INSERT INTO chunks
            (id,document_id,competition_id,seq,label,page,page_end,heading,track,ocr,chars,text,source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''', rows)
        return len(rows)

    def chunks(self, document_id):
        """返回一份资料的切块结果，用来核对切分是否合理。"""
        with self.db() as db:
            if not db.execute('SELECT id FROM documents WHERE id=?', (document_id,)).fetchone():
                raise ValueError('资料不存在。')
            rows = db.execute('''SELECT seq, label, page, page_end, heading, track, ocr, chars, source, text
                FROM chunks WHERE document_id=? ORDER BY seq''', (document_id,))
            return [dict(row) for row in rows]

    def search(self, cid, query):
        if not cid:
            raise ValueError('请先选择比赛。')
        query = str(query).strip()
        if not query or len(query) > 1000:
            raise ValueError('请输入 1 至 1000 字的检索内容。')
        # Transparent keyword evidence lookup; never presented as an AI answer.
        terms = self._terms(query)
        hits = []
        with self.db() as db:
            rows = db.execute('''SELECT c.seq, c.label, c.page, c.page_end, c.heading, c.track,
                    c.ocr, c.text, d.id document_id, d.title
                FROM chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.competition_id=?''', (cid,))
            for row in rows:
                text = row['text'].lower()
                heading = (row['heading'] or '').lower()
                matched = {term for term in terms if term in text}
                if not matched:
                    continue
                # 命中词的种类比单词重复次数更能说明这段是答案所在。
                score = len(matched) * 3 + sum(text.count(term) for term in terms) \
                    + 2 * sum(1 for term in terms if term in heading)
                hits.append({'document_id': row['document_id'], 'title': row['title'],
                             'page': row['page'], 'page_end': row['page_end'], 'chunk': row['label'],
                             'heading': row['heading'], 'text': row['text'], 'score': score,
                             'track': row['track'], 'ocr': bool(row['ocr'])})
        return sorted(hits, key=lambda h: h['score'], reverse=True)[:5]

    def answer(self, cid, query, owner_id=None, context=None):
        """回答。owner_id 是服务端签发的匿名身份：同一身份在同一场比赛里的追问接成一段会话。"""
        hits = self.search(cid, query)
        # 页面上只展示"每份资料最相关的一块"；本地模型仍看全部命中，不削弱兜底回答。
        shown = per_document(hits)
        with self.db() as db:
            competition = db.execute('SELECT id,name,edition FROM competitions WHERE id=?',
                                     (cid,)).fetchone()
        if not competition:
            raise ValueError('请先选择存在的比赛。')
        # 优先走已发布的智能体：网页里的回答与评委扫码问到的是同一个。
        if clients.agent_client.configured():
            return self._agent_answer(shown, dict(competition), context or query, owner_id)
        if not ai.configured():
            return {'mode': 'keyword_evidence', 'hits': shown, 'answer': None}
        evidence = [{**hit, 'evidence_id': f'E{i+1}'} for i,hit in enumerate(hits)]
        result = ai.grounded_answer(context or query, dict(competition), evidence)
        return {'mode': 'ai', 'hits': shown, **result}

    def _agent_answer(self, hits, competition, query, owner_id):
        """比赛资料问答：带比赛范围提问，回答后把引用映射回本地资料。"""
        return self._ask_agent(clients.agent_client, hits, query, owner_id,
                               scope=competition['id'], mode='agent',
                               label=f"{competition['name']}（{competition['edition']}）",
                               competition_id=competition['id'])

    def profile_answer(self, query, owner_id=None):
        """学生画像问答：不带比赛范围、不映射引用；会话按"画像"这个虚拟范围记。"""
        query = str(query or '').strip()
        if not query or len(query) > 2000:
            raise ValueError('请输入 1 至 2000 字的自我介绍或问题。')
        if not clients.profile_agent.configured():
            return {'mode': 'profile_failed', 'hits': [], 'answer': None, 'quotes': [],
                    'agent_error': '画像智能体还没有配置：.env 里缺 ADP_PROFILE_APP_KEY。'}
        return self._ask_agent(clients.profile_agent, [], query, owner_id,
                               scope='profile', mode='profile')

    def _ask_agent(self, client, hits, query, owner_id, scope, mode,
                   label=None, competition_id=None):
        """问一个已发布智能体（资料问答 / 画像共用）。

        会话：同一身份 + 同一范围复用同一个会话 ID；平台说会话失效就清掉旧记录、
        换新会话**只重试一次**，仍然失败就如实报失败（不假装回答）。
        `label` 只在资料问答时传（画像智能体不读比赛知识库，不该带比赛范围）。
        `competition_id` 只在资料问答时传（用于把引用映射回本地资料）。
        """
        existing = self.conversation_id(owner_id, scope) if owner_id else None
        error = ''
        for conversation_id in ([existing, None] if existing else [None]):
            kwargs = {'conversation_id': conversation_id}
            if label:
                kwargs['competition'] = label
            try:
                reply = client.ask(query, **kwargs)
            except clients.SessionExpired as exc:
                error = str(exc)
                self.reset_conversation(owner_id, scope)   # 失效会话留着没用
                continue                                   # 换新会话，只重试一次
            except ValueError as exc:
                return {'mode': f'{mode}_failed', 'hits': hits, 'answer': None, 'quotes': [],
                        'used_reference_indices': [], 'agent_error': str(exc)}
            if owner_id:
                self.remember_conversation(owner_id, scope, reply['conversation_id'])
            used = reply.get('used_reference_indices') or []
            quotes = reply.get('quotes') or []
            if competition_id:
                quotes = self.map_citations(competition_id, quotes, used)
            return {'mode': mode, 'hits': hits, 'continued': bool(conversation_id),
                    **{**reply, 'quotes': quotes}}
        return {'mode': f'{mode}_failed', 'hits': hits, 'answer': None, 'quotes': [],
                'used_reference_indices': [], 'agent_error': '会话已失效，重建后仍然失败，请重试。'
                                                            + (f'（{error}）' if error else '')}

    # ---- 会话：用户 + 比赛（P5）--------------------------------------------
    def conversation_id(self, owner_id, competition_id):
        """这个身份在这场比赛里正在用的会话 ID；没有就返回 None。"""
        if not owner_id:
            return None
        with self.db() as db:
            row = db.execute('SELECT conversation_id FROM agent_sessions '
                             'WHERE session_owner_id=? AND competition_id=?',
                             (owner_id, competition_id)).fetchone()
            return row['conversation_id'] if row else None

    def remember_conversation(self, owner_id, competition_id, conversation_id):
        """记下当前会话，让下一次追问接着上文问。"""
        if not owner_id:
            return
        with self.db() as db:
            db.execute('''INSERT INTO agent_sessions
                (session_owner_id, competition_id, conversation_id, updated_at) VALUES (?,?,?,?)
                ON CONFLICT(session_owner_id, competition_id) DO UPDATE SET
                conversation_id=excluded.conversation_id, updated_at=excluded.updated_at''',
                (owner_id, competition_id, conversation_id,
                 datetime.now(timezone.utc).isoformat()))

    def reset_conversation(self, owner_id, competition_id):
        """新会话：只重置这个身份在这一场比赛里的上下文，别的比赛不受影响。"""
        if not owner_id:
            return {'competition_id': competition_id, 'reset': False}
        with self.db() as db:
            db.execute('DELETE FROM agent_sessions WHERE session_owner_id=? AND competition_id=?',
                       (owner_id, competition_id))
        return {'competition_id': competition_id, 'reset': True}

    # ---- 引用映射：平台引用 → 本地资料（P9）--------------------------------
    def map_citations(self, competition_id, quotes, used=None):
        """把平台引用映射到本地资料。

        只做映射，不判断回答可不可信（可信度是比赛隔离 P6 的事）。
        顺序：平台文档 ID → 知识库文件名 → 旧命名兼容；都找不到就不猜来源。
        页码只在“引用原文唯一命中一个块”时给，否则只到文件级。
        """
        used = set(used or [])
        if not quotes:
            return []
        with self.db() as db:
            documents = [dict(row) for row in db.execute(
                '''SELECT id, title, published_at, kb_doc_id, kb_file_name FROM documents
                   WHERE competition_id=?''', (competition_id,))]
            mapped = []
            for quote in quotes:
                item = {**quote, 'used': quote.get('index') in used, 'document_id': '',
                        'title': '', 'page': None, 'match': ''}
                document = self._match_document(documents, quote)
                if document:
                    item.update(document_id=document['id'], title=document['title'],
                                match=document['match'])
                    page = self._unique_quote_page(db, document['id'], quote.get('quote_text') or '')
                    if page:
                        item['page'] = page
                mapped.append(item)
            return mapped

    @staticmethod
    def legacy_knowledge_base_filename(title, published_at):
        """第一批之前用的文件名规则：只为兼容老资料，新资料一律用技术名。"""
        safe = re.sub(r'[\\/:*?"<>|]', '-', str(title)).strip() or '未命名资料'
        return f'{safe[:60]}-{published_at or "未标日期"}.md'

    def link_knowledge_documents(self):
        """认领：把知识库里**已经存在**的文档对应回本地资料（写 kb_doc_id / kb_file_name）。

        为什么需要：早期资料是用命令行传进知识库的，本地没记文档 ID ——
        既让智能体的引用翻不回本地原文，又会被当成“还没进库”重复上传。
        只处理 kb_doc_id 为空的资料；候选不唯一就留着不动（不猜）。
        """
        try:
            existing = clients.knowledge_base.existing_documents()
        except ValueError as exc:
            return {'linked': [], 'ambiguous': [], 'message': str(exc)}
        with self.db() as db:
            claimed = {row['kb_doc_id'] for row in
                       db.execute("SELECT kb_doc_id FROM documents WHERE COALESCE(kb_doc_id,'') <> ''")}
            pending = [dict(row) for row in db.execute(
                "SELECT id, title, published_at FROM documents WHERE COALESCE(kb_doc_id,'') = ''")]
            linked, ambiguous = [], []
            for document in pending:
                match = self._match_existing_document(existing, claimed, document)
                if match == 'ambiguous':
                    ambiguous.append(document['title'])
                    continue
                if not match:
                    continue
                claimed.add(match['doc_id'])
                db.execute("UPDATE documents SET kb_doc_id=?, kb_file_name=?, kb_status='uploaded', "
                           "kb_status_desc=?, kb_checked_at=? WHERE id=?",
                           (match['doc_id'], match['name'],
                            f"认领知识库已有文档（{match['how']}），没有重复上传",
                            datetime.now(timezone.utc).isoformat(), document['id']))
                linked.append({'title': document['title'], 'kb_doc_id': match['doc_id'],
                               'kb_file_name': match['name'], 'how': match['how']})
        return {'linked': linked, 'ambiguous': ambiguous, 'message': ''}

    @classmethod
    def _match_existing_document(cls, existing, claimed, document):
        """在知识库现有文档里找这份资料对应的那一份；不唯一就返回 'ambiguous'。"""
        legacy = cls.legacy_knowledge_base_filename(document['title'], document['published_at'])
        title = cls._normalize_title(document['title'])
        candidates = []
        for item in existing:
            if item['doc_id'] in claimed or not item['name'].endswith('.md'):
                continue
            if item['name'] == legacy:
                candidates.append({**item, 'how': 'legacy_name'})
                continue
            key = cls._normalize_title(re.sub(r'-\d{4}(-\d{2}){0,2}$', '', item['name'][:-3]))
            if len(key) >= 8 and title and (key in title or title in key):
                candidates.append({**item, 'how': 'legacy_title'})
        if len(candidates) > 1:
            exact = [item for item in candidates if item['how'] == 'legacy_name']
            return exact[0] if len(exact) == 1 else 'ambiguous'
        return candidates[0] if candidates else None

    @staticmethod
    def _normalize_title(text):
        """标题比较用：去掉空白和标点，只留中日韩文字与字母数字。"""
        return re.sub(r'[^\u4e00-\u9fffa-zA-Z0-9]', '', str(text))

    @classmethod
    def _match_document(cls, documents, quote):
        name = str(quote.get('name') or '').strip()
        platform_id = str(quote.get('doc_id') or '').strip()
        if platform_id:
            for row in documents:
                if row['kb_doc_id'] and row['kb_doc_id'] == platform_id:
                    return {**row, 'match': 'kb_doc_id'}
        if name:
            for row in documents:
                if row['kb_file_name'] and row['kb_file_name'] == name:
                    return {**row, 'match': 'kb_file_name'}
            for row in documents:
                if cls.legacy_knowledge_base_filename(row['title'], row['published_at']) == name:
                    return {**row, 'match': 'legacy_name'}
        return None

    @staticmethod
    def _normalize(text):
        return re.sub(r'\s+', '', str(text))

    def _unique_quote_page(self, db, document_id, quote_text):
        """引用原文唯一命中一个块才给页码；多处命中或找不到都不猜。"""
        needle = self._normalize(quote_text)
        if len(needle) < 12:
            return None
        rows = db.execute('SELECT page, text FROM chunks WHERE document_id=? ORDER BY seq',
                          (document_id,)).fetchall()
        for candidate in (needle, needle[:24] if len(needle) >= 24 else ''):
            if not candidate:
                continue
            hits = [row for row in rows if candidate in self._normalize(row['text'])]
            if len(hits) == 1:
                return hits[0]['page']
            if hits:
                return None      # 多处命中：不唯一，宁可不给页码
        return None

    # ---- 学生画像（初赛版：一份画像 + 展示开关）-----------------------------
    def profile(self, owner_id):
        """读自己的画像；没存过返回 None。画像只给自己看，没有对外展示开关。"""
        if not owner_id:
            return None
        with self.db() as db:
            row = db.execute('SELECT payload, updated_at FROM profiles WHERE owner_id=?',
                             (owner_id,)).fetchone()
        if not row:
            return None
        return {'profile': json.loads(row['payload'] or '{}'), 'updated_at': row['updated_at']}

    def save_profile(self, owner_id, payload):
        """保存画像（初赛版直接覆盖；版本历史后移）。"""
        if not owner_id:
            raise ValueError('请先登录再保存画像。')
        data = payload if isinstance(payload, dict) else {}
        if not any(str(value).strip() for value in data.values() if not isinstance(value, list)):
            raise ValueError('画像里还没有内容，先写点东西再保存。')
        with self.db() as db:
            db.execute('''INSERT INTO profiles (owner_id, payload, updated_at)
                VALUES (?,?,?) ON CONFLICT(owner_id) DO UPDATE SET payload=excluded.payload,
                updated_at=excluded.updated_at''',
                (owner_id, json.dumps(data, ensure_ascii=False),
                 datetime.now(timezone.utc).isoformat()))
        return self.profile(owner_id)

    # ---- 画像助手的对话记录 -------------------------------------------------
    def append_profile_message(self, owner_id, role, text):
        """记一条对话消息（user / assistant），落库是为了刷新和换设备都不丢对话。"""
        if not owner_id:
            raise ValueError('请先登录。')
        with self.db() as db:
            row = db.execute('SELECT COALESCE(MAX(seq), 0) AS last FROM profile_messages '
                             'WHERE owner_id=?', (owner_id,)).fetchone()
            seq = (row['last'] or 0) + 1
            db.execute('INSERT INTO profile_messages (owner_id, seq, role, text, created_at) '
                       'VALUES (?,?,?,?,?)',
                       (owner_id, seq, role, text, datetime.now(timezone.utc).isoformat()))
        return {'role': role, 'text': text, 'at': None}

    def profile_chat(self, owner_id, limit=200):
        """读对话记录，老的在前。"""
        if not owner_id:
            return []
        with self.db() as db:
            rows = db.execute('SELECT role, text, created_at FROM profile_messages '
                              'WHERE owner_id=? ORDER BY seq DESC LIMIT ?',
                              (owner_id, limit)).fetchall()
        return [{'role': row['role'], 'text': row['text'], 'at': row['created_at']}
                for row in reversed(rows)]

    def clear_profile_messages(self, owner_id):
        """清空对话（换话题用）。"""
        if not owner_id:
            raise ValueError('请先登录。')
        with self.db() as db:
            db.execute('DELETE FROM profile_messages WHERE owner_id=?', (owner_id,))
        return {'messages': []}

    # ---- 登录账号（邮箱 / 手机号）----

    def _account_row(self, db, account):
        return db.execute('SELECT account, kind, password_hash, salt, name, owner_id '
                          'FROM users WHERE account=?', (account,)).fetchone()

    def register(self, account, password, name, owner_id=None):
        """注册：创建账号，并把当前浏览器身份绑到账号上。

        绑定是为了不丢东西：同一个人先匿名用了一会儿（存了画像、发了招募），
        再注册时这些内容仍属于他 —— 还是同一个 owner_id。
        注册与登录分开：账号已存在就明确报错，不替用户决定"直接登录"。
        """
        kind, account = normalize_account(account)
        password = str(password or '')
        if len(password) < PASSWORD_MIN:
            raise ValueError(f'密码至少 {PASSWORD_MIN} 位。')
        name = str(name or '').strip()[:40]
        with self.db() as db:
            if self._account_row(db, account):
                raise ValueError('这个邮箱或手机号已经注册过了，直接登录吧。')
            owner = owner_id or secrets.token_hex(16)
            salt = secrets.token_hex(16)
            display = name or default_name(kind, account)
            db.execute('INSERT INTO users '
                       '(account, kind, password_hash, salt, name, owner_id, created_at)'
                       ' VALUES (?,?,?,?,?,?,?)',
                       (account, kind, password_digest(password, salt), salt, display, owner,
                        datetime.now(timezone.utc).isoformat()))
            return {'account': account, 'kind': kind, 'name': display,
                    'owner_id': owner, 'created': True}

    def login(self, account, password):
        """登录：只校验已注册的账号，不创建新账号。

        返回 {'account', 'kind', 'name', 'owner_id', 'created': False}；失败抛 ValueError。
        """
        kind, account = normalize_account(account)
        password = str(password or '')
        with self.db() as db:
            row = self._account_row(db, account)
            if not row:
                raise ValueError('这个账号还没注册，先去注册一个吧。')
            if not secrets.compare_digest(password_digest(password, row['salt']),
                                          row['password_hash']):
                raise ValueError('账号或密码不正确。')
            return {'account': account, 'kind': row['kind'], 'name': row['name'],
                    'owner_id': row['owner_id'], 'created': False}

    def user_by_owner(self, owner_id):
        """当前浏览器身份对应的账号；没登录过返回 None。"""
        if not owner_id:
            return None
        with self.db() as db:
            row = db.execute('SELECT account, kind, name FROM users WHERE owner_id=?',
                             (owner_id,)).fetchone()
        return dict(row) if row else None

    # ---- 我关注的比赛（必须用户主动点）--------------------------------------
    def followed_competitions(self, owner_id, limit=None):
        if not owner_id:
            return []
        sql = '''SELECT c.id, c.name, c.edition, f.created_at,
                    (SELECT COUNT(*) FROM documents d WHERE d.competition_id=c.id) document_count
                 FROM follows f JOIN competitions c ON c.id = f.competition_id
                 WHERE f.owner_id=? ORDER BY f.created_at DESC'''
        with self.db() as db:
            rows = [dict(row) for row in db.execute(sql, (owner_id,))]
        return rows[:limit] if limit else rows

    def is_followed(self, owner_id, competition_id):
        if not owner_id or not competition_id:
            return False
        with self.db() as db:
            return bool(db.execute('SELECT 1 FROM follows WHERE owner_id=? AND competition_id=?',
                                   (owner_id, competition_id)).fetchone())

    def set_follow(self, owner_id, competition_id, follow=True):
        """关注 / 取消关注。不因为问过、填过画像、发过招募就自动加入。"""
        if not owner_id:
            raise ValueError('请先登录再关注比赛。')
        with self.db() as db:
            if not db.execute('SELECT id FROM competitions WHERE id=?', (competition_id,)).fetchone():
                raise ValueError('比赛不存在。')
            if follow:
                db.execute('INSERT OR IGNORE INTO follows (owner_id, competition_id, created_at) '
                           'VALUES (?,?,?)',
                           (owner_id, competition_id, datetime.now(timezone.utc).isoformat()))
            else:
                db.execute('DELETE FROM follows WHERE owner_id=? AND competition_id=?',
                           (owner_id, competition_id))
        return {'competition_id': competition_id, 'followed': bool(follow)}

    # ---- 队伍招募（普通产品功能，不经 AI）-----------------------------------
    RECRUITMENT_SQL = '''SELECT r.*, c.name competition_name, c.edition competition_edition,
        p.payload publisher_profile, p.updated_at publisher_profile_updated_at
        FROM recruitments r
        LEFT JOIN competitions c ON c.id = r.competition_id
        LEFT JOIN profiles p ON p.owner_id = r.owner_id'''

    def recruitments(self, competition_id=None, owner_id=None, viewer_id=None,
                     include_closed=False):
        """招募列表。"""
        sql, args = [self.RECRUITMENT_SQL, 'WHERE 1=1'], []
        if competition_id:
            sql.append('AND r.competition_id=?')
            args.append(competition_id)
        if owner_id:
            sql.append('AND r.owner_id=?')
            args.append(owner_id)
        if not include_closed:
            sql.append("AND r.status='open'")
        sql.append('ORDER BY r.created_at DESC')
        with self.db() as db:
            rows = [dict(row) for row in db.execute(' '.join(sql), args)]
        return [self._recruitment_view(row, viewer_id) for row in rows]

    def recruitment(self, recruitment_id, viewer_id=None):
        with self.db() as db:
            row = db.execute(f'{self.RECRUITMENT_SQL} WHERE r.id=?', (recruitment_id,)).fetchone()
        if not row:
            raise ValueError('招募不存在。')
        return self._recruitment_view(dict(row), viewer_id)

    @staticmethod
    def _recruitment_view(row, viewer_id):
        row.pop('show_profile', None)     # 旧列留着不读：没有"是否公开"开关，露不露由"发不发招募"决定
        # 发布者的已确认画像随招募一起给出去（组队要先看得见人）；没填过就是空字典。
        payload = row.pop('publisher_profile', None)
        try:
            row['publisher_profile'] = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            row['publisher_profile'] = {}
        row['mine'] = bool(viewer_id and row['owner_id'] == viewer_id)
        row.pop('owner_id', None)  # Never expose the anonymous session credential in public JSON.
        return row

    def competition_recruitment_count(self, competition_id):
        """比赛页的"这场比赛招募（N 条）"——普通数据库查询，不是智能体算的。"""
        with self.db() as db:
            return db.execute("SELECT COUNT(*) n FROM recruitments "
                              "WHERE competition_id=? AND status='open'",
                              (competition_id,)).fetchone()['n']

    def create_recruitment(self, owner_id, data):
        """发布一条队伍招募。"""
        if not owner_id:
            raise ValueError('请先登录再发布招募。')
        title = str(data.get('title', '')).strip()
        competition_id = str(data.get('competition_id', '')).strip()
        contact = str(data.get('contact', '')).strip()
        if not title or not competition_id or not contact:
            raise ValueError('请填写招募标题、对应比赛和联系方式。')
        try:
            need = max(1, min(10, int(data.get('need_count') or 1)))
        except (TypeError, ValueError) as exc:
            raise ValueError('需要人数请填 1-10 之间的数字。') from exc
        uid = uuid.uuid4().hex
        with self.db() as db:
            if not db.execute('SELECT id FROM competitions WHERE id=?', (competition_id,)).fetchone():
                raise ValueError('比赛不存在，请重新选择。')
            db.execute('''INSERT INTO recruitments (id, owner_id, competition_id, title, idea,
                want_role, need_count, contact, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,'open',?)''',
                (uid, owner_id, competition_id, title[:80],
                 str(data.get('idea', '')).strip()[:300],
                 str(data.get('want_role', '')).strip()[:80], need, contact[:120],
                 datetime.now(timezone.utc).isoformat()))
            db.execute('UPDATE recruitments SET publisher_name=? WHERE id=?',
                       (str(data.get('publisher_name') or '参赛者').strip()[:40] or '参赛者', uid))
        return self.recruitment(uid, viewer_id=owner_id)

    def update_recruitment(self, owner_id, recruitment_id, data):
        """改自己的招募：只有发布者本人能改。"""
        with self.db() as db:
            row = db.execute('SELECT owner_id FROM recruitments WHERE id=?',
                             (recruitment_id,)).fetchone()
            if not row:
                raise ValueError('招募不存在。')
            if row['owner_id'] != owner_id:
                raise ValueError('只能修改自己发布的招募。')
            fields, args = [], []
            for column, key, limit in (('title', 'title', 80), ('idea', 'idea', 300),
                                       ('want_role', 'want_role', 80), ('contact', 'contact', 120)):
                if data.get(key) is not None:
                    if key in ('title', 'contact') and not str(data[key]).strip():
                        raise ValueError('招募标题和联系方式不能为空。')
                    fields.append(f'{column}=?')
                    args.append(str(data.get(key)).strip()[:limit])
            if data.get('need_count') is not None:
                try:
                    need = max(1, min(10, int(data.get('need_count'))))
                except (TypeError, ValueError) as exc:
                    raise ValueError('需要人数请填 1-10 之间的数字。') from exc
                fields.append('need_count=?')
                args.append(need)
            if data.get('status') in ('open', 'closed'):
                fields.append('status=?')
                args.append(data['status'])
            if not fields:
                raise ValueError('没有要修改的内容。')
            db.execute(f'UPDATE recruitments SET {", ".join(fields)} WHERE id=?',
                       [*args, recruitment_id])
        return self.recruitment(recruitment_id, viewer_id=owner_id)

    def set_recruitment_status(self, owner_id, recruitment_id, status):
        """结束 / 重新开启招募（只有发布者本人）。"""
        if status not in ('open', 'closed'):
            raise ValueError('状态只能是 open 或 closed。')
        return self.update_recruitment(owner_id, recruitment_id, {'status': status})

    def delete_recruitment(self, owner_id, recruitment_id):
        with self.db() as db:
            row = db.execute('SELECT owner_id FROM recruitments WHERE id=?',
                             (recruitment_id,)).fetchone()
            if not row:
                raise ValueError('招募不存在。')
            if row['owner_id'] != owner_id:
                raise ValueError('只能删除自己发布的招募。')
            db.execute('DELETE FROM recruitments WHERE id=?', (recruitment_id,))
        return {'deleted': recruitment_id}
