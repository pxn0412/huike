"""Local persistence and page-based evidence. No model-generated facts."""
import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from parsing import extract, suggest
import adp
import agent
import ai
import chunking


class Store:
    def __init__(self, root, use_ocr=True):
        self.use_ocr = use_ocr
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, filename TEXT, content BLOB, hash TEXT,
                    pages TEXT, created_at TEXT);
                CREATE TABLE IF NOT EXISTS competitions (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, edition TEXT NOT NULL,
                    UNIQUE(name, edition));
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, competition_id TEXT NOT NULL,
                    filename TEXT, title TEXT, publisher TEXT, published_at TEXT,
                    uploaded_at TEXT, uploader TEXT, role TEXT, track TEXT,
                    content BLOB, hash TEXT, pages TEXT,
                    UNIQUE(competition_id, hash),
                    FOREIGN KEY(competition_id) REFERENCES competitions(id));
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, competition_id TEXT NOT NULL,
                    seq INTEGER NOT NULL, label TEXT, page INTEGER, page_end INTEGER,
                    heading TEXT, track TEXT, ocr INTEGER DEFAULT 0, chars INTEGER,
                    text TEXT NOT NULL, source TEXT,
                    FOREIGN KEY(document_id) REFERENCES documents(id));
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_competition ON chunks(competition_id);
            ''')
            for table in ('uploads', 'documents'):
                fields = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if 'metadata' not in fields:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN metadata TEXT DEFAULT '{{}}'")
            # 已入库但还没切块的资料要补上，历史资料也必须能按块检索。
            pending = db.execute('''SELECT d.id, d.competition_id, d.pages, d.track, d.metadata
                FROM documents d WHERE NOT EXISTS
                (SELECT 1 FROM chunks c WHERE c.document_id = d.id)''').fetchall()
            for row in pending:
                self._index_document(db, row['id'], row['competition_id'],
                                     json.loads(row['pages']), row['track'] or '',
                                     json.loads(row['metadata'] or '{}'))
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
        role = data.get('role')
        date = str(data.get('published_at', '')).strip()
        if not title or not uploader or role not in ('student', 'teacher'):
            raise ValueError('请填写资料标题、上传者姓名和身份。')
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
            duplicate = db.execute('SELECT id FROM documents WHERE competition_id=? AND hash=?', (cid, upload['hash'])).fetchone()
            if duplicate:
                db.execute('DELETE FROM uploads WHERE id=?', (upload['id'],))
                return {'competition_id': cid, 'document_id': duplicate['id'], 'duplicate': True}
            did = uuid.uuid4().hex
            db.execute('INSERT INTO documents (id,competition_id,filename,title,publisher,published_at,uploaded_at,uploader,role,track,content,hash,pages,metadata) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                did, cid, upload['filename'], title, str(data.get('publisher', '')).strip(), date,
                datetime.now(timezone.utc).isoformat(), uploader, role,
                str(data.get('track', '')).strip(), upload['content'], upload['hash'], upload['pages'], upload['metadata']))
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
                d.published_at, d.uploaded_at, d.uploader, d.role, d.track, d.pages, d.metadata,
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
                metadata['knowledge_base'] = {'status': 'skipped', 'doc_id': '',
                                              'message': '这份资料没有可切分的正文（可能是扫描页）'}
                db.execute('UPDATE documents SET metadata=? WHERE id=?',
                           (json.dumps(metadata, ensure_ascii=False), document_id))
                return metadata['knowledge_base']
            header = [('比赛', f"{competition['name']}（{competition['edition']}）" if competition
                       else '未确认'),
                      ('资料类型', '比赛资料'),
                      ('来源文件', row['filename'] or ''),
                      ('发布时间', row['published_at'] or '未标注')]
            if row['track']:
                header.append(('适用赛道', row['track']))
            filename = self._knowledge_base_filename(row['title'], row['published_at'])
            content = chunking.render_markdown(blocks, row['title'], header).encode('utf-8')
            outcome = adp.push_document(content, filename)
            metadata['knowledge_base'] = {**outcome, 'filename': filename,
                                          'chunk_count': len(blocks),
                                          'synced_at': datetime.now(timezone.utc).isoformat()}
            db.execute('UPDATE documents SET metadata=? WHERE id=?',
                       (json.dumps(metadata, ensure_ascii=False), document_id))
        return metadata['knowledge_base']

    def sync_pending_documents(self, limit=5):
        """把本地还没进知识库的资料补传（老资料，或上次上传失败的）。

        返回需要补传的资料 ID 列表；服务启动时会在后台跑一次，不需要人工点。
        """
        with self.db() as db:
            pending = [row['id'] for row in db.execute('SELECT id, metadata FROM documents')
                       if (json.loads(row['metadata'] or '{}').get('knowledge_base') or {}
                           ).get('status') not in ('uploaded', 'duplicate')]
        for document_id in pending[:limit]:
            try:
                self.sync_knowledge_base(document_id)
            except ValueError:
                continue
        return pending

    @staticmethod
    def _knowledge_base_filename(title, published_at):
        """知识库里的文件名。平台按文件名去重，所以发布日期必须带上：同名会被判为重复。"""
        safe = re.sub(r'[\\/:*?"<>|]', '-', str(title)).strip() or '未命名资料'
        return f'{safe[:60]}-{published_at or "未标日期"}.md'

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

    def answer(self, cid, query):
        hits = self.search(cid, query)
        with self.db() as db:
            competition = db.execute('SELECT name,edition FROM competitions WHERE id=?', (cid,)).fetchone()
        if not competition:
            raise ValueError('请先选择存在的比赛。')
        # 优先走已发布的智能体：网页里的回答与评委扫码问到的是同一个。
        if agent.configured():
            try:
                return {'mode': 'agent', 'hits': hits,
                        **agent.ask(query, competition=f"{competition['name']}（{competition['edition']}）")}
            except ValueError as exc:
                return {'mode': 'agent_failed', 'hits': hits, 'answer': None,
                        'quotes': [], 'agent_error': str(exc)}
        if not ai.configured():
            return {'mode': 'keyword_evidence', 'hits': hits, 'answer': None}
        evidence = [{**hit, 'evidence_id': f'E{i+1}'} for i,hit in enumerate(hits)]
        result = ai.grounded_answer(query, dict(competition), evidence)
        return {'mode': 'ai', 'hits': hits, **result}
