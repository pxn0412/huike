"""把已入库的通知正文导出成 Markdown，供 ADP 知识库上传。

扫描件 PDF（例如慧科杯通知）没有文字层，平台侧文档解析读不到内容，
而本机 OCR 的结果已经存在 data/huike.sqlite3 里；这份导出件就是可被
检索的那一份。
"""
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_documents(db_path):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        return db.execute('''SELECT d.id, d.title, d.filename, d.publisher, d.published_at,
            d.track, d.pages, d.metadata, c.name competition, c.edition edition
            FROM documents d JOIN competitions c ON c.id = d.competition_id
            ORDER BY c.name, d.published_at''').fetchall()
    finally:
        db.close()


def export(row, out_dir):
    pages = json.loads(row['pages'])
    metadata = json.loads(row['metadata'])
    ocr_pages = set(metadata.get('ocr_pages') or [])
    title = row['title'] or Path(row['filename']).stem
    lines = [f'# {title}', '',
             f'- 比赛：{row["competition"]}（{row["edition"]}）',
             f'- 发布单位：{row["publisher"] or "未提取"}',
             f'- 发布时间：{row["published_at"] or "未提取"}',
             f'- 来源文件：{row["filename"]}',
             f'- 页数：{len(pages)}', '']
    for number, text in enumerate(pages, 1):
        lines.append(f'## 第 {number} 页{"（OCR 识别）" if number in ocr_pages else ""}')
        lines.append((text or '').strip() or '（本页未识别出文字）')
        lines.append('')
    target = out_dir / f'{title}.md'.replace('/', '_').replace('\\', '_')
    target.write_text('\n'.join(lines), encoding='utf-8')
    chars = sum(len((page or '').strip()) for page in pages)
    return target, len(pages), chars


def main():
    parser = argparse.ArgumentParser(description='导出已确认通知的正文，供外部平台建知识库。')
    parser.add_argument('--db', default=str(ROOT / 'data' / 'huike.sqlite3'))
    parser.add_argument('--out', default=str(ROOT.parent / 'docs' / 'adp' / 'data'))
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_documents(args.db)
    if not rows:
        print('库里还没有已确认的资料：先在网站上传一份通知并在确认页保存。')
        return
    for row in rows:
        target, pages, chars = export(row, out_dir)
        verdict = '内容完整，可直接上传知识库' if chars >= 200 else '正文几乎为空，需先补 OCR'
        print(f'{target.name}：{pages} 页 / {chars} 字 —— {verdict}')
    print('输出目录：', out_dir)


if __name__ == '__main__':
    main()
