"""自动跑评测集：把题目逐条发给已发布的应用，记录回答。

用的是官方「对话端接口（HTTP SSE）」：POST {ADP_CHAT_URL}，body 里带 AppKey。
HTTP SSE 只需要 AppKey，不需要腾讯云 SecretId/SecretKey。

用法：
    python eval_adp.py --list      # 只解析题目，不联网
    python eval_adp.py --limit 3   # 先跑前 3 题
    python eval_adp.py             # 跑全部题目

配置（website/.env 或环境变量）：
    ADP_APP_KEY    应用管理 → 应用 → 调用 → 创建/复制 AppKey
    ADP_CHAT_URL   对话接口地址，默认 https://wss.lke.cloud.tencent.com/adp/v2/chat

每题一个独立会话（ConversationId 不同），避免上一题的上下文影响下一题。
原始返回存进 _eval/raw-NNN.txt，方便核对解析对不对。
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_QUESTIONS = ROOT.parent / 'docs' / 'adp' / '2026-09-19-ADP黄金评测集.md'
DEFAULT_ENDPOINT = 'https://wss.lke.cloud.tencent.com/adp/v2/chat'
RAW_DIR = ROOT / '_eval'
ROW = re.compile(r'^\s*\|\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|')
TEXT_EVENTS = ('text.delta', 'content.add', 'message.delta')
# 平台不同版本对同一概念的字段名不一样：按可能性从高到低取第一个非空值。
DOC_ID_KEYS = ('DocBizId', 'DocId', 'DocumentId', 'Id')
QUOTE_KEYS = ('Content', 'QuoteText', 'Text', 'Quote', 'SegmentText')


def settings():
    values = {}
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    names = ('ADP_APP_KEY', 'ADP_CHAT_URL', 'ADP_PROFILE_APP_KEY', 'ADP_PROFILE_CHAT_URL')
    return {name: os.environ.get(name, values.get(name, '')) for name in names}


def load_questions(path):
    """从评测集 Markdown 的表格里抽出题目：| 题号 | 问题 | ... |"""
    questions, seen = [], set()
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        match = ROW.match(line)
        if not match or '问题' in match.group(2):
            continue
        number, question = int(match.group(1)), match.group(2).strip()
        if not question or number in seen:
            continue  # 跳过记录表里的空行和重复题号
        seen.add(number)
        questions.append((number, question))
    return questions


def call(endpoint, app_key, question, conversation_id=None, timeout=120):
    """一次问答。返回 (解析结果, 原始事件文本)。

    `conversation_id` 决定这是新会话还是追问：传同一个 ID 就是接着上文问。
    评测脚本每题都不传，等于每题一个新会话。
    """
    payload = {'RequestId': uuid.uuid4().hex,
               'ConversationId': str(conversation_id or uuid.uuid4().hex),
               'AppKey': app_key, 'UserId': 'eval-runner', 'UserName': '评测脚本',
               'Contents': [{'Type': 'text', 'Text': question}], 'Incremental': True,
               'Stream': 'enable'}
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        raise ValueError(f'HTTP {exc.code}：{exc.read().decode("utf-8", "replace")[:300]}') from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ValueError(f'请求失败：{exc}') from exc
    return parse_events(raw), raw


def parse_events(raw):
    """按 SSE 事件名归集内容：正文、平台角标、引用列表、错误。

    角标用平台给的 `quote_info.added`（Index + Position）**原样保留**，
    既不往回答正文里插 `[n]`，也不从正文里正则找 `[n]` —— 正文里天然出现的
    “提交材料包括：[1] PPT”不是引用，猜不得。
    """
    answer, marks, refs, errors, event = [], [], [], [], ''
    for line in raw.splitlines():
        if line.startswith('event:'):
            event = line[6:].strip()
            continue
        if not line.startswith('data:'):
            continue
        body = line[5:].strip()
        if not body or body == '[DONE]':
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        payload = data.get('Payload', data) if isinstance(data, dict) else {}
        if not isinstance(payload, dict):
            continue
        if event == 'text.replace':
            # 平台在需要修正时会把「到目前为止的完整文本」重发一次，必须以它为准
            full = payload.get('Text') or payload.get('Content')
            if isinstance(full, str) and full:
                answer = [full]
        elif event in TEXT_EVENTS or 'text' in event:
            text = payload.get('Content') or payload.get('Text') or payload.get('Message')
            if isinstance(text, str) and text:
                answer.append(text)
        elif event == 'quote_info.added':
            info = payload.get('QuoteInfo') or {}
            if isinstance(info, dict) and info.get('Position') is not None:
                marks.append({'index': int(info.get('Index') or 0),
                              'position': int(info['Position'])})
        elif event.startswith('reference.added'):
            reference = payload.get('Reference') or {}
            document = reference.get('DocRefer') or {}
            name = document.get('DocName') or reference.get('Name') or ''
            if name:
                refs.append({'index': reference.get('Index') or len(refs) + 1, 'name': name,
                             'url': document.get('Url') or '',
                             'doc_id': _reference_field(reference, document, DOC_ID_KEYS),
                             'quote_text': _reference_field(reference, document, QUOTE_KEYS)})
        elif event == 'error' or payload.get('Code'):
            message = payload.get('Message') or payload.get('Code')
            if message:
                errors.append(str(message))
    return {'answer': ''.join(answer).strip(), 'quotes': refs, 'marks': marks,
            'used_reference_indices': sorted({mark['index'] for mark in marks if mark['index']}),
            'errors': errors}


def _first(mapping, keys):
    """按顺序取第一个非空字符串：不同接口版本字段名不一样，按可能性依次试。"""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


def _reference_field(reference, document, keys):
    return _first(document, keys) or _first(reference, keys)


def display_answer(answer):
    """评测报告里怎么显示回答：空的就写“未解析到正文”，不编一段像回答的话。"""
    return answer.strip() if answer and answer.strip() else '未解析到正文'


def quote_summary(result):
    """引用列：标出哪些角标是回答真正用到的（✓），哪些只是被检索到。"""
    used = set(result.get('used_reference_indices') or [])
    return '；'.join(f"[{item['index']}] {item['name']}"
                     f"{'✓' if item['index'] in used else '（检索到未使用）'}"
                     for item in (result.get('quotes') or [])[:4])


def write_result(out_dir, rows, endpoint):
    """把结果写成 Markdown 表格 + 引用明细。

    rows 每项是 (题号, 问题, 解析结果, 耗时)；解析结果就是 parse_events 的返回。
    """
    stamp = datetime.now().strftime('%Y-%m-%d-%H%M')
    target = Path(out_dir) / f'2026-09-19-评测结果-{stamp}.md'
    lines = [f'# 评测结果（{stamp}）', '',
             f'- 接口：{endpoint}', f'- 题数：{len(rows)}', '',
             '> 原始返回在 `website/_eval/raw-NNN.txt`。角标是平台给的（`quote_info.added`），'
             '不再往正文里插 `[n]`；✓ 表示回答实际用到了这条引用。', '',
             '| 题号 | 问题 | 回答 | 引用（✓=回答实际使用） | 耗时(s) |', '| --- | --- | --- | --- | --- |']
    for number, question, result, elapsed in rows:
        clean = display_answer(result.get('answer')).replace('\n', ' ').replace('|', '｜')
        lines.append(f'| {number} | {question} | {clean} | {quote_summary(result)} | {elapsed:.1f} |')
    lines += ['', '## 引用明细', '']
    for number, _, result, _ in rows:
        quotes = result.get('quotes') or []
        if not quotes:
            lines.append(f'- 第 {number} 题：无引用')
            continue
        used = set(result.get('used_reference_indices') or [])
        used_text = '、'.join(str(index) for index in sorted(used)) or '无（回答未标注可核对引用）'
        lines.append(f'- 第 {number} 题：回答使用角标 {used_text}')
        for item in quotes:
            mark = '（回答依据）' if item['index'] in used else '（检索到但回答未使用）'
            lines.append(f"  - [{item['index']}] {item['name']}{mark}（{item['url']}）")
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return target


def main():
    parser = argparse.ArgumentParser(description='自动跑评测集并记录回答。')
    parser.add_argument('--questions', default=str(DEFAULT_QUESTIONS), help='评测集 Markdown 路径')
    parser.add_argument('--app-key', help='AppKey；不填则读 .env 的 ADP_APP_KEY')
    parser.add_argument('--endpoint', help='对话接口地址；不填则读 .env 的 ADP_CHAT_URL')
    parser.add_argument('--out', default=str(ROOT.parent / 'docs' / 'adp'), help='结果输出目录')
    parser.add_argument('--limit', type=int, default=0, help='只跑前 N 题')
    parser.add_argument('--only', default='', help='只跑指定题号，用逗号分隔，例如 7,8,19')
    parser.add_argument('--list', action='store_true', help='只解析题目，不联网')
    parser.add_argument('--reparse', action='store_true',
                        help='不联网：用 _eval 里的原始返回重新生成结果（改了解析器就能重算）')
    args = parser.parse_args()

    questions = load_questions(args.questions)
    if not questions:
        print(f'没从 {args.questions} 解析到题目。')
        return 1
    print(f'共解析到 {len(questions)} 道题：')
    for number, question in questions:
        print(f'  {number:>2}. {question}')
    if args.list:
        return 0

    if args.reparse:
        rows = []
        for number, question in questions:
            raw_path = RAW_DIR / f'raw-{number:03d}.txt'
            if not raw_path.exists():
                continue
            result = parse_events(raw_path.read_text(encoding='utf-8'))
            rows.append((number, question, result, 0.0))
        target = write_result(args.out, rows, '离线重解析（用 _eval 里的原始返回）')
        print(f'重新解析 {len(rows)} 题：{target}')
        return 0

    config = settings()
    app_key = args.app_key or config['ADP_APP_KEY']
    endpoint = args.endpoint or config['ADP_CHAT_URL'] or DEFAULT_ENDPOINT
    if not app_key:
        print('缺少 AppKey：应用管理 → 应用 → 调用 → 复制 AppKey，写进 website/.env 的 ADP_APP_KEY=')
        return 1
    print(f'接口：{endpoint}')

    RAW_DIR.mkdir(exist_ok=True)
    if args.only:
        wanted = {int(part) for part in args.only.replace('，', ',').split(',') if part.strip().isdigit()}
        selected = [item for item in questions if item[0] in wanted]
    else:
        selected = questions[:args.limit] if args.limit else questions
    rows = []
    for number, question in selected:
        started = time.time()
        try:
            result, raw = call(endpoint, app_key, question)
        except ValueError as exc:
            print(f'{number:>2}. 失败：{exc}')
            rows.append((number, question, {'answer': f'调用失败：{exc}', 'quotes': [],
                                            'used_reference_indices': [], 'errors': []}, 0.0))
            continue
        (RAW_DIR / f'raw-{number:03d}.txt').write_text(raw, encoding='utf-8')
        elapsed = time.time() - started
        note = f"，引用 {len(result['quotes'])} 条" if result['quotes'] else ''
        if result['used_reference_indices']:
            note += f"，回答用到角标 {result['used_reference_indices']}"
        if not result['answer'].strip():
            note += '，未解析到正文（不要当成“没有问题”）'
        if result['errors']:
            note += '，错误：' + '；'.join(result['errors'])
        print(f'{number:>2}. 完成（{elapsed:.1f}s，{len(result["answer"])} 字）{note}')
        rows.append((number, question, result, elapsed))

    target = write_result(args.out, rows, endpoint)
    print(f'结果：{target}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
