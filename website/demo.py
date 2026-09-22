"""Initial-round presentation contracts; platform transport remains in clients."""
import re
from datetime import datetime, timezone, timedelta

import clients

PROFILE_FIELDS = {
    'major': '专业', 'grade': '年级',
    'experiences': '做过什么 / 本人负责什么',
    'participation': '希望参与什么', 'learning': '正在学习 / 希望尝试什么',
}


def profile_payload(value):
    if not isinstance(value, dict):
        raise ValueError('画像格式不正确。')
    result = {}
    for key in PROFILE_FIELDS:
        item = value.get(key, '')
        if not isinstance(item, str) or len(item) > 4000:
            raise ValueError('画像每项请填写不超过 4000 字的文本。')
        if item.strip():
            result[key] = item.strip()
    return result


def profile_text(payload):
    return '\n'.join(f'{label}：{payload[key]}' for key, label in PROFILE_FIELDS.items()
                     if payload.get(key))


def profile_template():
    """给模型的固定栏目模板。

    **必须每次都发**：只给「整理成画像草稿」这句话、不给栏目名时（尤其没有任何底稿），
    模型会自己发明格式，把草稿写成一整段话；而 parse_profile 只认"栏目名：内容"的行，
    整段无法归类时只能拒收（见下面 unmatched 分支）。实测踩过一次。
    """
    return ('草稿段落里请严格按下面五行写，每行都是"栏目名：内容"，栏目名照抄、不要改写、'
            '不要合并成一段话；没有内容的行写"未提及"，不要编造：\n'
            + '\n'.join(f'{label}：' for label in PROFILE_FIELDS.values()))


# 模型表示"这里没内容"的说法。这些不是画像内容，一律当空处理：
# 实测里它会把没提到的类别写成"（未提及）"，直接存进去就成了假内容。
PLACEHOLDERS = ('待补充', '待补充信息', '待定', '未提及', '未填写', '未说明', '未提供', '未回答',
                '暂无', '暂无信息', '未知', '无', '略', 'n/a', 'none', 'null')


def is_placeholder(value):
    """整段是不是占位文字（去掉括号、破折号、空白后再比，按整值匹配，不做子串判断）。"""
    text = re.sub(r'^[\s·•\-—－()（）【】\[\]]+|[\s·•\-—－()（）【】\[\]]+$', '',
                  str(value or '')).strip()
    return not text or text.lower() in PLACEHOLDERS


def clean_reply(text):
    """草稿之外的文字就是"这一轮给用户看的话"。"""
    return str(text or '').strip()


def parse_profile(answer, base):
    """把模型回答拆成两路：`reply`（给用户看的话，永远有）与 `draft`（有草稿才有）。

    只认带【画像草稿】标记的草稿；标记之外的文字一律当对话回复，不当画像内容。
    """
    answer = str(answer or '')
    raw = clean_reply(answer)
    match = re.search(r'【画像草稿】(.*?)(?:【本次修改】(.*)|$)', answer, re.S)
    if not match:
        return {'draft': None, 'changes': [], 'reply': raw, 'raw': raw, 'reason': 'no_marker'}
    body, changes = match.group(1).strip(), (match.group(2) or '').strip()
    if not body:
        return {'draft': None, 'changes': [], 'reply': raw, 'raw': raw, 'reason': 'empty_body'}
    reply = clean_reply(answer[:match.start()])
    draft = dict(base)
    aliases = {
        '专业': 'major', '年级': 'grade', '背景': 'major',
        '做过什么 / 本人负责什么': 'experiences', '做过什么｜本人负责什么': 'experiences',
        '做过什么': 'experiences', '本人负责什么': 'experiences', '本人负责': 'experiences',
        '希望参与什么': 'participation', '希望参与': 'participation',
        '正在学习 / 希望尝试什么': 'learning', '正在学习 / 希望尝试': 'learning',
        '正在学习': 'learning', '希望尝试': 'learning',
    }
    # Ask for plain labeled text, but tolerate markdown headings and bullets.
    collected, current, unmatched = {}, None, []
    for line in body.splitlines():
        line = re.sub(r'^[\s#·•\-*]+', '', line.replace('**', '')).strip()
        if not line:
            continue
        parts = re.split(r'[：:]', line, maxsplit=1)
        label = parts[0].strip('【】 ')
        key = aliases.get(label)
        if key:
            current = key
            value = parts[1].strip() if len(parts) > 1 else ''
            # 模型有时会把没提到的类别写成"待补充""（未提及）"：那是空，不是内容。
            if value and not is_placeholder(value):
                # 「做过什么」与「本人负责」是两个标签、同一个栏目：分开记，别互相覆盖。
                if label in ('本人负责', '本人负责什么'):
                    value = '本人负责：' + value
                collected.setdefault(key, []).append(value)
        elif current and not is_placeholder(line):
            collected.setdefault(current, []).append(line)
        elif not current:
            unmatched.append(line)
    if unmatched:
        # 模型的文字不静默丢掉，但也不替它猜栏目：如实说清"这版没按栏目写"，把原文附在后面。
        return {'draft': None, 'changes': [], 'reason': 'unlabeled', 'raw': raw, 'reply':
                '智能体这版写成了一整段、没有按栏目分（' + ' / '.join(PROFILE_FIELDS.values()) +
                '），我没有替它猜栏目。再点一次“整理成画像草稿”请它按栏目重写，或点“手动编辑”自己填。\n'
                '它写的内容如下：\n' + answer}
    for key, lines in collected.items():
        draft[key] = '\n'.join(lines)
    if not collected:
        return {'draft': None, 'changes': [], 'reply': reply or raw, 'raw': raw,
                'reason': 'empty_body'}
    return {'draft': profile_payload(draft),
            'changes': [line.strip(' ·•-*') for line in changes.splitlines() if line.strip()],
            'reply': reply, 'raw': raw, 'reason': 'ok'}


def draft_changes(draft, base):
    """自己算"本次修改"，不依赖模型怎么写。"""
    changes = [f'{"修改" if base.get(key) else "新增"}“{label}”'
               for key, label in PROFILE_FIELDS.items()
               if draft.get(key, '') != base.get(key, '')]
    return changes or ['与当前草稿一致，未修改内容。']


CHAT_FALLBACK_REPLY = '我这轮没接上话，你再说一次？也可以点“手动编辑”自己填。'
DRAFT_READY_REPLY = '我先照你说的整理了一版草稿，你看哪里不对；确认后点“确认并保存”。'
# 模型没按栏目写时，第二次请求追加的这句（模板已经在上面的提示里，这里只指出它上一版错在哪）。
UNLABELED_RETRY = ('\n注意：你上一版把草稿写成了一整段，没有按栏目分。请只输出【画像草稿】与【本次修改】两段，'
                   '【画像草稿】里严格按“栏目名：内容”逐行写，栏目名照抄上面那五行，不要合并成一段话。')
DRAFT_OVERWRITE_NOTICE = '（AI 又整理了一版，但你这版正在改，我没有覆盖；需要的话点“整理成画像草稿”。）'


def transcript_text(messages):
    """把对话记录拍成纯文本，喂给智能体。"""
    return '\n'.join(f'{"用户" if m["role"] == "user" else "助手"}：{m["text"]}'
                     for m in messages)


def ask_profile_agent(store, owner, prompt):
    """问画像助手一次；没答上来就抛错（错误信息保留平台原话）。"""
    reply = store._ask_agent(clients.profile_agent, [], prompt, owner,
                             scope='profile', mode='profile')
    if not reply.get('answer'):
        raise ValueError(reply.get('agent_error') or '画像智能体暂时没有回答，请重试。')
    return reply['answer']


def base_draft(store, owner, data):
    """整理底稿：优先用页面上正在改的草稿，其次用已保存的画像。"""
    saved = store.profile(owner)
    if data.get('draft') is not None:
        return profile_payload(data['draft']), saved
    return (profile_payload(saved['profile']) if saved else {}), saved


def chat(store, owner, data):
    """日常对话：只回话，不生成草稿（草稿由 draft() 单独触发）。"""
    text = str(data.get('text') or '').strip()
    if not text or len(text) > 2000:
        raise ValueError('请输入 1 至 2000 字的内容。')
    if not clients.profile_agent.configured():
        raise ValueError('画像智能体暂未配置，可以先手动编辑画像。')
    base, saved = base_draft(store, owner, data)
    store.append_profile_message(owner, 'user', text)
    prompt = (
        '这是网站的画像助手的日常对话，不是整理任务：请像人一样回应用户这一轮说的话，'
        '可以追问、可以解释、可以纠正；这一轮不要生成画像草稿，也不要输出任何标记格式。'
        '不清楚就一次只问一个最关键的问题，回答一到三句话。\n'
        f'【当前已保存画像】\n{profile_text(saved["profile"]) if saved else "尚未保存"}\n'
        f'【当前编辑草稿】\n{profile_text(base) or "无"}\n'
        f'【本轮用户消息】\n{text}')
    answer_text = ask_profile_agent(store, owner, prompt)
    result = parse_profile(answer_text, base)
    draft, changes, answer = None, [], result['reply']
    if result['draft'] is None and result.get('reason') == 'unlabeled':
        # 聊天里不贴"这版没按栏目分"的说明（那是对整理任务的解释），按它原话显示。
        answer = result['raw'] or answer
    if result['draft'] is not None:
        # 平台提示词还没改成"只在被要求时出草稿"时要兜得住：
        # 草稿区空着就收下（内容别丢），正在改草稿就不覆盖。
        if data.get('draft') is None:
            draft, changes = result['draft'], draft_changes(result['draft'], base)
            answer = answer or DRAFT_READY_REPLY
        else:
            answer = f'{answer}\n{DRAFT_OVERWRITE_NOTICE}'.strip()
    answer = answer or CHAT_FALLBACK_REPLY
    store.append_profile_message(owner, 'assistant', answer)
    return {'messages': store.profile_chat(owner), 'reply': answer,
            'draft': draft, 'changes': changes}


def draft(store, owner, data):
    """网站发起的整理任务：把对话记录整理成画像草稿（只出草稿，不聊天）。"""
    if not clients.profile_agent.configured():
        raise ValueError('画像智能体暂未配置，可以先手动编辑画像。')
    base, saved = base_draft(store, owner, data)
    messages = store.profile_chat(owner)
    if not messages and not base:
        raise ValueError('先聊两句或者自己写点内容，再点“整理成画像草稿”。')
    prompt = (
        '这是网站发起的整理任务：请把下面的对话记录整理成画像草稿。'
        '只输出【画像草稿】与【本次修改】两段，不要聊天、不要解释格式。'
        '以“当前编辑草稿”为底稿（没有就用“当前已保存画像”），'
        '保留与本次修改无关的内容，缺失的内容不要编造。\n'
        f'{profile_template()}\n'
        f'【当前已保存画像】\n{profile_text(saved["profile"]) if saved else "尚未保存"}\n'
        f'【当前编辑草稿】\n{profile_text(base) or "无"}\n'
        f'【对话记录】\n{transcript_text(messages) or "（还没有对话）"}\n'
        '【本轮用户消息】请按上面的对话记录整理成画像草稿。')
    answer_text = ask_profile_agent(store, owner, prompt)
    result = parse_profile(answer_text, base)
    if result['draft'] is None and result.get('reason') == 'unlabeled':
        # 模型自己发明了格式（多见于"一点底稿都没有"时）：带上栏目模板再要一次，
        # 不把"让用户再点一遍"当成第一选择。
        answer_text = ask_profile_agent(store, owner, prompt + UNLABELED_RETRY)
        result = parse_profile(answer_text, base)
    if result['draft'] is None:
        detail = result['reply'] or '请再试一次。'
        if result.get('reason') != 'unlabeled':
            detail = '智能体这次没有给出可用草稿：' + detail
        raise ValueError(detail)
    return {'draft': result['draft'], 'changes': draft_changes(result['draft'], base),
            'reply': result['reply']}


def eligibility(answer):
    states = {'✅': 'satisfied', '❌': 'unsatisfied', '⚪': 'not_specified', '❓': 'missing'}
    rows, heading = [], ''
    for line in str(answer or '').splitlines():
        line = line.replace('**', '').strip()
        if line.startswith('#'):
            heading = line.lstrip('# ').strip()
            continue
        match = re.match(r'^([✅❌⚪❓])\s*(?:满足|不满足|资料未规定|缺少信息)?\s*(.*)$', line)
        if not match:
            continue
        symbol, content = match.groups()
        source = re.search(r'[\[【]来源[：:]\s*(.*?)[\]】]', content)
        if source:
            content = content[:source.start()].strip()
        if content.startswith(('：', ':')):
            item, detail = heading, content[1:].strip()
        else:
            parts = re.split(r'[：:]', content, maxsplit=1)
            if len(parts) != 2:
                continue
            item, detail = parts
            if item.strip() == '项目' and heading:
                item = heading
        if not item.strip() or not detail.strip() or detail.strip(' 。；;') in ('说明', '...', '…', '待填写'):
            continue
        rows.append({'state': states[symbol], 'item': item.strip(' ，,：:'),
                     'detail': detail.strip(),
                     'source': source.group(1) if source else ''})
    return rows


def competition_prompt(store, owner, question):
    """发给比赛助手的指令（资料问答与招募协助共用同一套口径）。"""
    saved = store.profile(owner)
    return (
        f'【用户问题】{question}\n'
        f'【本人已确认的背景】{profile_text(saved["profile"]) if saved else "尚无已保存画像"}\n'
        f'【当前日期】{datetime.now(timezone(timedelta(hours=8))).date()}（北京时间）\n'
        '请检索当前比赛资料，回答上面的用户问题，简洁且有实际原文依据。'
        '若核对资格，每项独立一行，先写✅满足、❌不满足、⚪资料未规定或❓缺少信息，'
        '再写具体条件名称、冒号和真实核对理由，最后用[来源：文件名]标注真实来源。'
        '有几项就核对几项，不得只输出格式占位文字。人数区间包含1时，1人满足人数这一项。'
        '未检索到不等于全文未规定。不下完整资格结论，不编造页码。'
        '准备建议最多三项，官方要求和AI建议分开；不推断队友缺口，不推荐队友，不代写招募。'
        '若缺个人信息，只问必要项。通知中的过去日期不能说成当前正在进行，也不据此断言实际赛事结束。')


def ask(store, owner, data):
    question = str(data.get('query') or '').strip()
    if not question or len(question) > 1000:
        raise ValueError('请输入 1 至 1000 字的问题。')
    cid = data.get('competition_id')
    saved = store.profile(owner)
    result = store.answer(cid, question, owner_id=owner,
                          context=competition_prompt(store, owner, question))
    result['eligibility'] = eligibility(result.get('answer'))
    if any(word in question for word in ('队友', '组队', '匹配', '找人', '一起做', '搭档', '缺人')):
        mine = store.recruitments(competition_id=cid, owner_id=owner)
        result['recruit_hint'] = ('你已发布招募，可以查看或编辑。' if mine else
                                  '可直接发布招募。' if saved else '可以先完善画像，再发布招募。')
        result['has_recruitment'] = bool(mine)
    return result


# ---- 招募协助：网站编排「比赛助手查要求 + 画像助手陪聊」 --------------------

RECRUIT_BRIEF_QUESTION = ('这场比赛的赛道或主题方向、作品与提交要求、队伍人数规定分别是什么？'
                          '按比赛资料回答；资料里没写的项就直接说资料未规定。')

RECRUIT_FIELDS = {'title': '招募标题', 'idea': '项目想法', 'want_role': '希望队友参与'}


def brief_text(brief):
    """把比赛助手的结果拍成纯文本：要求 + 引用来源。"""
    if not brief or not brief.get('answer'):
        return ('（比赛助手这次没有返回可用内容：不要凭常识补充比赛规则，'
                '必要时说“这条要到比赛页问比赛助手”。）')
    lines = [str(brief['answer']).strip()]
    names = list(dict.fromkeys(q.get('name') for q in (brief.get('quotes') or []) if q.get('name')))
    if names:
        lines.append('引用资料：' + '、'.join(names))
    return '\n'.join(lines)


def recruit_prompt(store, owner, data, competition, brief, text, task=False):
    """发给画像助手的招募协助指令：依据（比赛要求）+ 背景（主画像）+ 现状（当前表单）。"""
    saved = store.profile(owner)
    form = {key: str(data.get(key) or '').strip()[:300] for key in RECRUIT_FIELDS}
    prompt = (
        '【招募协助】你在帮本人把这场比赛招募的「项目想法」和「希望队友参与什么」想清楚，'
        '不是整理主画像。\n'
        '比赛事实只依据【比赛要求（来自比赛助手）】回答，并保留其中的来源；依据不足或没提供时，'
        '就说“这条需要到比赛页问比赛助手”，不要凭常识补充规则。\n'
        '比赛要求是依据，项目方向和分工是建议：不要把建议说成官方要求，不要替本人确定分工。\n'
        '一次只问一个最关键的问题，通常一到三句话；不替本人编造经历、不替他承诺时间。\n'
        '主画像只用来理解本人：可以建议他承担什么，但不得把“参与过某件事”升级成“能独立负责某件事”。\n'
        '不要修改主画像，也不要声称已保存画像或已发布招募。\n'
        f'【比赛】{competition["name"]} · {competition["edition"]}\n'
        f'【比赛要求（来自比赛助手，含来源）】\n{brief_text(brief)}\n'
        f'【本人主画像（仅作背景，不要修改）】\n{profile_text(saved["profile"]) if saved else "尚未保存"}\n'
        f'【当前招募表单】\n招募标题：{form["title"] or "（空）"}\n'
        f'项目想法：{form["idea"] or "（空）"}\n希望队友参与：{form["want_role"] or "（空）"}\n'
        f'【本轮用户消息】\n{text}')
    if task:
        prompt += ('\n现在请把上面的讨论整理成招募文案。注意：**这不是画像草稿**，不要用【画像草稿】标记，'
                   '也不要在前面加问候或解释。\n'
                   '严格按下面三行输出，每行一个标签，不要多写别的字：\n'
                   '招募标题：…（12 字以内，说清项目 + 招募方向）\n'
                   '项目想法：…（一到两句：为谁解决什么问题、计划做什么、本人希望参与什么）\n'
                   '希望队友参与：…（一到两个具体工作或岗位）')
    return prompt


def recruit_brief(store, owner, competition):
    """第一步：向比赛助手问一次这场比赛的要求（带来源）。失败也如实返回，不影响手写。"""
    if not clients.agent_client.configured():
        return {'answer': None, 'quotes': [],
                'agent_error': '比赛助手暂未配置：可以先自己写项目想法，规则到比赛页核对。'}
    result = store.answer(competition['id'], RECRUIT_BRIEF_QUESTION, owner_id=owner,
                          context=competition_prompt(store, owner, RECRUIT_BRIEF_QUESTION))
    return {'answer': result.get('answer'), 'quotes': result.get('quotes') or [],
            'agent_error': result.get('agent_error')}


def recruit_chat(store, owner, data, competition, brief):
    """第二步：画像助手陪聊（比赛要求由网站喂进去，用户只看到一个对话框）。"""
    text = str(data.get('text') or '').strip()
    if not text or len(text) > 1000:
        raise ValueError('请输入 1 至 1000 字的内容。')
    if not clients.profile_agent.configured():
        raise ValueError('画像智能体暂未配置，可以直接自己写项目想法。')
    prompt = recruit_prompt(store, owner, data, competition, brief, text)
    reply = store._ask_agent(clients.profile_agent, [], prompt, owner,
                             scope=f'recruit:{competition["id"]}', mode='profile')
    if not reply.get('answer'):
        raise ValueError(reply.get('agent_error') or '画像智能体暂时没有回答，请重试。')
    return {'reply': clean_reply(reply['answer'])}


def parse_recruit_draft(answer):
    """从回答里抠出三栏；抠不到（少于两栏）返回 None（不猜、不硬塞）。

    平台提示词里只有【画像草稿】一个标记，所以不要求模型用标记；
    有标记就只看标记里面，没有就整段找标签行。
    """
    text = str(answer or '')
    match = re.search(r'【招募草稿】(.*?)(?:【|$)', text, re.S)
    body = match.group(1) if match else text
    aliases = {'招募标题': 'title', '标题': 'title', '项目想法': 'idea', '想法': 'idea',
               '希望队友参与什么': 'want_role', '希望队友参与': 'want_role',
               '想找什么样的队友': 'want_role', '需要的队友': 'want_role'}
    collected = {}
    for line in body.splitlines():
        line = re.sub(r'^[\s#·•\-*]+', '', line.replace('**', '')).strip()
        if not line:
            continue
        parts = re.split(r'[：:]', line, maxsplit=1)
        key = aliases.get(parts[0].strip('【】 '))
        if key and len(parts) > 1 and parts[1].strip() and not is_placeholder(parts[1]):
            collected[key] = parts[1].strip()
    return collected if len(collected) >= 2 else None


def profile_hits(draft, profile):
    """草稿里用到了主画像的哪几句：本机比对，不采信模型自述。

    判定：整句照搬，或与这句的 2 字组合重合 ≥2 个且达到该句组合数的四分之一。
    这样"结合参与过剧本创作的经历"这类改写能认出来（参与/剧本/创作三个组合重合），
    而只是碰巧提到"校园"这种单个词重合不会误报成"引用了你的画像"。
    命中时整句列出来，用户看到的是自己原话。
    """
    text = ' '.join(str(value) for value in (draft or {}).values() if isinstance(value, str))
    hits = []
    for key in PROFILE_FIELDS:
        for piece in re.split(r'[\n。；;，,]+', str((profile or {}).get(key) or '')):
            piece = piece.strip()
            if len(piece) < 4:
                continue
            if piece in text:
                hits.append(piece)
                continue
            grams = {piece[i:i + 2] for i in range(len(piece) - 1)}
            overlap = sum(1 for gram in grams if gram in text)
            if overlap >= max(2, len(grams) // 4):
                hits.append(piece)
    return hits[:5]


def recruit_draft(store, owner, data, competition, brief):
    """第三步：让画像助手只输出三栏，本机再标出用到的画像句子。仍然不落库。"""
    if not clients.profile_agent.configured():
        raise ValueError('画像智能体暂未配置，可以直接自己写项目想法。')
    text = str(data.get('text') or '').strip()
    prompt = recruit_prompt(store, owner, data, competition, brief, text, task=True)
    reply = store._ask_agent(clients.profile_agent, [], prompt, owner,
                             scope=f'recruit:{competition["id"]}', mode='profile')
    if not reply.get('answer'):
        raise ValueError(reply.get('agent_error') or '画像智能体暂时没有回答，请重试。')
    draft = parse_recruit_draft(reply['answer'])
    if not draft:
        raise ValueError('智能体这次没有给出可用的招募草稿：'
                         + (clean_reply(reply['answer'])[:200] or '可以再聊两句，或自己写。'))
    saved = store.profile(owner)
    return {'draft': draft, 'reply': clean_reply(reply['answer']),
            'profile_hits': profile_hits(draft, saved['profile'] if saved else {})}
