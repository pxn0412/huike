"""把通知类资料切成可上传知识库的块。规则优先，不生成事实。

对应 docs/adp/2026-09-19-知识库切片与元数据规格.md：
R1 不跨一级编号切；R2 目标 200–800 字，语义完整的小节允许短于下限；
R3 清单整块不切散；R4 块首一行标题（块号 + 内容范围 + 页码 + 赛道）；
R5 跨页续写处补一句重叠；R6 图像/二维码噪声行整行丢弃；R7 标注赛道但不改原文。

这里的判断全部是规则，模型不参与，同一份文件每次切出来结果一致。
"""
import re

HEADING_1 = re.compile(r'^([一二三四五六七八九十]+)\s*[、.．]\s*(\S.*)$')
HEADING_2 = re.compile(r'^[（(]\s*([一二三四五六七八九十]+)\s*[)）]\s*(\S.*)$')
HEADING_3 = re.compile(r'^(\d{1,2})\s*[.．、]\s*(\S.*)$')
TERMINAL = '。！？；：!?）)】》」”’'
AI_HINTS = ('智能体', '混元', '腾讯云', 'ADP', '大模型')
PBL_HINTS = ('PBL', 'PB')  # “PB” 是 PBL 被 OCR 认断后的形态（“PB 凵页目开发赛”）
AI_TRACK = 'AI 智能体应用赛'
PBL_TRACK = 'PBL 项目开发赛'
AI_TRACK_OCR = re.compile(r'A\s*[|丨]\s*智能体')  # OCR 把 “AI” 认成 “A 丨” 时的兜底
TRACK_LABEL = {'AI': '仅 AI 智能体应用赛', 'PBL': '仅 PBL 项目开发赛', None: '两赛道通用'}
MIN_CHARS = 200
MAX_CHARS = 800


def is_cjk(char):
    """汉字，或中文标点（顿号、句号、全角括号与引号等）。"""
    return ('\u4e00' <= char <= '\u9fff' or '\u3000' <= char <= '\u303f'
            or '\uff01' <= char <= '\uff5e')


def normalize(text):
    """去掉 OCR 在汉字、中文标点之间留下的空格；英文与数字两侧保持原样。"""
    text = text.replace('\u3000', ' ')
    kept = []
    for index, char in enumerate(text):
        if char in ' \t' and 0 < index < len(text) - 1 \
                and is_cjk(text[index - 1]) and is_cjk(text[index + 1]):
            continue
        kept.append(char)
    return ''.join(kept)


def is_noise(line):
    """二维码、水印被 OCR 成 "0 到 0 0 到" 的行，整行丢弃（R6）。"""
    body = re.sub(r'\s', '', line)
    if len(body) < 2:
        return True  # 被 OCR 切碎的单个字（“丿”“回”）不是正文
    junk = sum(1 for char in body if char in '0到苤的过的回也')
    return junk / len(body) >= 0.6


def is_heading(line, short_titles=True, previous=''):
    """一级（一、）和二级（（一））编号永远算标题；短行要“上一行已结束”才算标题。

    短行标题是给"OCR 丢了编号"的通知用的（正文里孤零零的“大赛宗旨”）。
    要求上一行以句号、冒号等收尾，是为了排除被 OCR 折行的碎句（“……公示复赛晋级／名单”）。
    排版规范的文件可以关掉 short_titles，避免误判。
    """
    if not line or len(line) > 40:
        return False
    if HEADING_1.match(line) or HEADING_2.match(line):
        return True
    if not short_titles or HEADING_3.match(line) or line[0].isdigit():
        return False  # 1. 2. 这类清单条目整块保留（R3），数字开头的行不当标题
    if line[0] in '（(' or line[-1] in '）)':
        return False  # 括号行是正文或枚举，不是小节标题
    if previous and previous[-1] not in TERMINAL:
        return False  # 上一行还没说完 → 这是被折断的句子，不是标题
    if len(line) <= 16 and line[-1] not in TERMINAL and '。' not in line:
        return '、' not in line.lstrip('、.．')
    return False


def track_of(heading, text):
    """赛道标注：先看标题里的关键词，再看正文有没有出现赛道全名。

    标注只是给检索和提示词的提示；两条赛道都出现、或都判断不出时返回 None（通用）。
    宁可标成“通用”，也不要错标成某一条赛道。
    """
    has_ai = any(word in heading for word in AI_HINTS) \
        or AI_TRACK in text or bool(AI_TRACK_OCR.search(text))
    has_pbl = any(word in heading for word in PBL_HINTS) or PBL_TRACK in text
    if has_ai and not has_pbl:
        return 'AI'
    if has_pbl and not has_ai:
        return 'PBL'
    return None


def read_markdown_pages(text):
    """读回 export_text.py 导出的 Markdown（## 第 N 页），得到 [(页码, 正文)]。"""
    pages, current = [], None
    for line in normalize(text).splitlines():
        match = re.match(r'^#{1,4}\s*第\s*(\d+)\s*页', line.strip())
        if match:
            current = []
            pages.append((int(match.group(1)), current))
            continue
        if current is None:
            if line.strip() and not line.lstrip().startswith(('#', '-')):
                current = []
                pages.append((1, current))
        if current is not None:
            current.append(line)
    return [(number, '\n'.join(lines)) for number, lines in pages]


def sections_of(pages, short_titles=True):
    """把每页按标题行切成小节，返回 [{page, top, heading, lines}]。

    top 记录该小节所属的一级编号，用来判断父子关系；没有一级编号时为 ''。
    """
    sections, previous = [], ''
    for number, text in pages:
        current, top = None, ''  # top 只在同一页内用于判断父子小节
        for raw in normalize(text).splitlines():
            line = raw.strip()
            if not line or is_noise(line):
                continue
            if HEADING_1.match(line):
                top = line
            heading = is_heading(line, short_titles, previous)
            previous = line
            if current is None:
                current = {'page': number, 'top': top, 'heading': line if heading else '',
                           'lines': [] if heading else [line]}
                continue
            if heading:
                sections.append(current)
                current = {'page': number, 'top': top, 'heading': line, 'lines': []}
                continue
            current['lines'].append(line)
        if current:
            sections.append(current)
    return [section for section in sections if section['lines'] or section['heading']]


def section_text(section):
    body = '\n'.join(section['lines']).strip()
    if section['heading'] and body:
        return f"{section['heading']}\n{body}"
    return section['heading'] or body


def overlap_tail(block, limit=60):
    """跨页续写时用上一块的最后一句做重叠，避免句子被切断（R5）。"""
    text = block['text'].replace('\n', '')
    if block['heading'] and text.startswith(block['heading']):
        text = text[len(block['heading']):]  # 重叠只取正文，不重复标题
    if not text or text[-1] in TERMINAL:
        return ''
    cut = max(text.rfind(char) for char in ('。', '；', '！', '？'))
    tail = text[cut + 1:]
    return tail if 0 < len(tail) <= limit else ''


def build_blocks(pages, prefix='N', min_chars=MIN_CHARS, max_chars=MAX_CHARS, short_titles=True):
    """pages: [(页码, 正文)]。返回块列表，块内保留原文措辞。"""
    sections = sections_of(pages, short_titles)
    prepared, index = [], 0
    while index < len(sections):
        section = sections[index]
        following = sections[index + 1] if index + 1 < len(sections) else None
        # 只有标题、几乎没有正文的一级小节，并进它的子小节，保留层级（R1、R3）。
        if following and section['heading'] and following['heading'] \
                and len(section_text(section)) <= 60 and following['top'] == section['heading']:
            following['heading'] = f"{section['heading']} · {following['heading']}"
            following['lines'] = section['lines'] + following['lines']
            index += 1
            continue
        prepared.append(section)
        index += 1
    blocks = []
    for section in prepared:
        text = section_text(section)
        if not text:
            continue
        previous = blocks[-1] if blocks else None
        # 跨页续写：没有标题的小节并进上一块，必要时补一句重叠（R5）。
        if previous and not section['heading'] and section['page'] != previous['page_end'] \
                and len(previous['text']) + len(text) + 1 <= max_chars:
            tail = overlap_tail(previous)
            if tail:
                text = f'（接上页）{tail}{text}'
            previous['text'] += '\n' + text
            previous['page_end'] = section['page']
            continue
        blocks.append({'page': section['page'], 'page_end': section['page'],
                       'heading': section['heading'], 'text': text})
    for index, block in enumerate(blocks, 1):
        block['chunk_id'] = f'{prefix}{index:02d}'
        block['track'] = track_of(block['heading'], block['text'])
        block['chars'] = len(block['text'])
    return blocks


def render_markdown(blocks, title, header):
    """输出可上传知识库的 Markdown：块首一行标题，块间 --- 分隔。"""
    lines = [f'# {title}', '']
    lines += [f'- {key}：{value}' for key, value in header]
    lines += ['', '---', '']
    for block in blocks:
        page = str(block['page']) if block['page'] == block['page_end'] \
            else f"{block['page']}—{block['page_end']}"
        lines.append(f"## 块 {block['chunk_id']} ｜{block['heading'] or '正文'}"
                     f"（第 {page} 页，{TRACK_LABEL[block['track']]}）")
        lines += ['', block['text'], '', '---', '']
    return '\n'.join(lines).rstrip() + '\n'


def render_yaml(blocks, document_id):
    """输出机器可读的块清单；字段名沿用产品规格，不自造状态。"""
    lines = [f'# 由 chunking.py 生成；源文件：{document_id}', 'chunks:']
    for block in blocks:
        page = str(block['page']) if block['page'] == block['page_end'] \
            else f"{block['page']}—{block['page_end']}"
        lines += [f"  - chunk_id: {block['chunk_id']}",
                  f'    document_id: {document_id}',
                  f"    track: {block['track'] or 'null'}",
                  f'    page: {page}',
                  f"    section: {block['heading'] or '正文'}",
                  f"    chars: {block['chars']}",
                  '    text: |']
        lines += ['      ' + line for line in block['text'].splitlines()]
    return '\n'.join(lines) + '\n'

