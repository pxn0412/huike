"""上传文件 → 知识库文本：解析、切块、导出。不依赖腾讯云，可离线跑。

用法：
    python kb.py --source 通知.pdf --prefix N --kind 比赛通知 --published 2026-06-05 --review

产出（默认写到 ../docs/adp/data/）：
    <标题>.md            可上传知识库的分块文本（块首一行标题，块间 --- 分隔）
    <标题>.yaml          机器可读块清单
    <标题>-纠错建议.md     --review 时生成，人工确认后才改原文
"""
import argparse
from pathlib import Path

import ai
import chunking
from parsing import extract

ROOT = Path(__file__).resolve().parent
WARN_SHORT = 120
WARN_LONG = 800


def read_pages(source, use_ocr=True):
    """PDF 走 OCR 提取；导出的 Markdown 按页读回；纯文本按一页处理。"""
    if not source.exists():
        raise ValueError(f'找不到文件：{source}')
    if source.suffix.lower() == '.pdf':
        pages, metadata = extract(source.read_bytes(), use_ocr)
        return list(enumerate(pages, 1)), metadata
    text = source.read_text(encoding='utf-8')
    if source.suffix.lower() in ('.md', '.markdown'):
        return chunking.read_markdown_pages(text), {}
    return [(1, text)], {}


def write_review(path, issues):
    lines = ['# OCR 纠错建议（模型提出，人工确认后才改原文）', '',
             '> 本文件只用于人工校对；确认后的改字要回到 `.md` 源文件里改，不要引模型输出当事实。', '']
    if not issues:
        lines.append('模型没有提出明显问题。')
    for index, issue in enumerate(issues, 1):
        lines += [f"## {index}. 第 {issue['page']} 页 · 置信度 {issue['confidence'] or '未标注'}",
                  f"- 原文：{issue['original']}",
                  f"- 建议：{issue['suggested']}",
                  f"- 理由：{issue['reason']}", '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='把通知类资料切成可上传知识库的分块文本。')
    parser.add_argument('--source', required=True, help='PDF、Markdown 或纯文本路径')
    parser.add_argument('--title', help='资料标题，默认取文件名')
    parser.add_argument('--prefix', default='N', help='块号前缀：通知用 N，海报用 C')
    parser.add_argument('--kind', default='比赛通知', help='资料类型，写进文件头')
    parser.add_argument('--competition', default='浙江广厦建设职业技术大学 2026 年“慧科杯”AI 创新大赛')
    parser.add_argument('--published', default='', help='发布时间，例如 2026-06-05')
    parser.add_argument('--out', default=str(ROOT.parent / 'docs' / 'adp' / 'data'))
    parser.add_argument('--review', action='store_true', help='调用模型列出可疑 OCR 错字')
    parser.add_argument('--force', action='store_true', help='允许覆盖同名的输出文件')
    parser.add_argument('--no-short-titles', action='store_true', help='关掉“短行也算标题”')
    args = parser.parse_args()

    source = Path(args.source)
    title = args.title or source.stem
    try:
        pages, metadata = read_pages(source)
    except ValueError as exc:
        print(f'读取失败：{exc}')
        return 1
    unreadable = [number for number, text in pages if not text.strip()]
    blocks = chunking.build_blocks(pages, prefix=args.prefix, short_titles=not args.no_short_titles)
    if not blocks:
        print('没有切出任何块：这份文件的正文可能是空的（扫描件请先 OCR）。')
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    header = [('比赛', args.competition), ('资料类型', args.kind), ('来源文件', source.name)]
    if args.published:
        header.append(('发布时间', args.published))
    if metadata.get('ocr_pages'):
        header.append(('说明', f"第 {metadata['ocr_pages']} 页来自本地 OCR，已人工校对"))
    markdown_path = out_dir / f'{title}.md'
    yaml_path = out_dir / f'{title}.yaml'
    if markdown_path.exists() and not args.force:
        print(f'{markdown_path} 已存在。人工校对过的文件不能被覆盖，'
              f'如确实要重切请改 --title 或加 --force。')
        return 1
    markdown_path.write_text(chunking.render_markdown(blocks, title, header), encoding='utf-8')
    yaml_path.write_text(chunking.render_yaml(blocks, source.stem), encoding='utf-8')

    total = sum(block['chars'] for block in blocks)
    print(f'页数 {len(pages)}｜块数 {len(blocks)}｜正文字数 {total}')
    for block in blocks:
        flag = '  ← 偏短' if block['chars'] < WARN_SHORT else '  ← 偏长' if block['chars'] > WARN_LONG else ''
        page = block['page'] if block['page'] == block['page_end'] else f"{block['page']}—{block['page_end']}"
        print(f"  {block['chunk_id']} 第{page}页 {block['chars']:>4}字 "
              f"[{chunking.TRACK_LABEL[block['track']]}] {block['heading'][:34]}{flag}")
    if unreadable:
        print(f'注意：第 {unreadable} 页没有文字，需要人工补录。')
    print(f'输出：{markdown_path}')
    print(f'输出：{yaml_path}')

    if args.review:
        if not ai.configured():
            print('未配置模型，跳过纠错建议（.env 里设置 HUIKE_AI_*）。')
            return 0
        try:
            issues = ai.review_blocks(source.name, blocks)
        except ValueError as exc:
            print(f'纠错建议未生成：{exc}')
            return 0
        review_path = out_dir / f'{title}-纠错建议.md'
        write_review(review_path, issues)
        print(f'输出：{review_path}（{len(issues)} 条建议，需人工确认）')
    print('上传提示：切分选“通用标识符切分”，标识符填 ---，最大长度 800，重叠 80。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
