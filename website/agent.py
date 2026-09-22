"""调用已发布的 ADP 智能体回答问题。

本地网站的"问答"入口：优先让腾讯云智能体开发平台上的智能体回答，
这样网页里的答案与评委扫码问到的是同一个（同一份知识库、同一套提示词）。
配置写在 `.env`：`ADP_APP_KEY`、`ADP_CHAT_URL`（AppKey 只能问答，不能上传）。
"""
import time
import uuid

import eval_adp


class SessionExpired(ValueError):
    """平台说这个会话不能用了（过期 / 不存在）：调用方换一个新会话重试一次即可。"""


SESSION_WORDS = ('conversationid', 'conversation', '会话')
FAILURE_WORDS = ('invalid', 'expired', 'not exist', '不存在', '过期', '失效', '无效')


def is_session_expired(message):
    """判断平台错误是不是“会话失效”。

    平台文案与字段名都可能变，所以只做保守的“会话词 + 失败词”同时命中：
    宁可把它当普通失败，也不要误判 —— 误判会白重试一次，还可能把好好的会话重置掉。
    """
    text = str(message).lower()
    return any(word in text for word in SESSION_WORDS) and \
        any(word in text for word in FAILURE_WORDS)


def configured():
    """有没有配置已发布的智能体。"""
    values = eval_adp.settings()
    return bool(values['ADP_APP_KEY'] and values['ADP_CHAT_URL'])


def profile_configured():
    """有没有配置画像智能体（第二个应用）。"""
    values = eval_adp.settings()
    return bool(values['ADP_PROFILE_APP_KEY']
                and (values['ADP_PROFILE_CHAT_URL'] or values['ADP_CHAT_URL']))


def ask(question, competition=None, conversation_id=None, timeout=120):
    """问比赛资料智能体。

    带上"当前比赛"再问：知识库里可能有多场比赛的资料，不限定范围它会拿别的比赛来答。
    `conversation_id` 是会话 ID：传进去就是同一场对话的追问，不传就开新会话
    （会话 ID 由我们生成并返回，调用方负责记住它）。
    返回 {'answer', 'quotes', 'used_reference_indices', 'conversation_id', 'elapsed'}。
    平台或网络报错时抛 ValueError，由调用方决定怎么提示用户（绝不假装成回答）。
    """
    prompt = str(question)
    if competition:
        prompt = (f'【当前比赛：{competition}】请只依据这场比赛的资料回答；'
                  f'如果这场比赛资料里没有，就直接说“这场比赛资料中没有找到”，'
                  f'不要引用其他比赛的规定。\n问题：{question}')
    values = eval_adp.settings()
    return _ask_app(prompt, values['ADP_APP_KEY'], values['ADP_CHAT_URL'],
                    conversation_id, timeout)


def ask_profile(question, conversation_id=None, timeout=120):
    """问画像智能体。

    它不读比赛知识库，所以**不注入"当前比赛"**，也不做比赛范围约束
    （比赛范围是资料问答那条线的规则）。
    """
    values = eval_adp.settings()
    endpoint = values['ADP_PROFILE_CHAT_URL'] or values['ADP_CHAT_URL']
    return _ask_app(str(question), values['ADP_PROFILE_APP_KEY'], endpoint,
                    conversation_id, timeout)


def _ask_app(prompt, app_key, endpoint, conversation_id=None, timeout=120):
    """发一次 SSE 并做统一校验：错误分类、空正文判定、会话 ID 回传。"""
    conversation = str(conversation_id or uuid.uuid4().hex)
    start = time.monotonic()
    parsed, _ = eval_adp.call(endpoint, app_key, prompt,
                              conversation_id=conversation, timeout=timeout)
    elapsed = time.monotonic() - start
    if parsed.get('errors'):
        message = '；'.join(parsed['errors'])
        if is_session_expired(message):
            raise SessionExpired(message)
        raise ValueError('智能体返回错误：' + message)
    answer = (parsed.get('answer') or '').strip()
    if not answer:
        # 空响应不是回答：宁可走 agent_failed 让用户重试，也不能把空白当结论展示。
        raise ValueError('智能体没有返回正文（可能超时或被平台限流），请重试。')
    return {'answer': answer, 'quotes': parsed.get('quotes') or [],
            'used_reference_indices': parsed.get('used_reference_indices') or [],
            'conversation_id': conversation, 'elapsed': round(elapsed, 1)}
