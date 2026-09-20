"""调用已发布的 ADP 智能体回答问题。

本地网站的"问答"入口：优先让腾讯云智能体开发平台上的智能体回答，
这样网页里的答案与评委扫码问到的是同一个（同一份知识库、同一套提示词）。
配置写在 `.env`：`ADP_APP_KEY`、`ADP_CHAT_URL`（AppKey 只能问答，不能上传）。
"""
import time

import eval_adp


def configured():
    """有没有配置已发布的智能体。"""
    values = eval_adp.settings()
    return bool(values['ADP_APP_KEY'] and values['ADP_CHAT_URL'])


def ask(question, competition=None, timeout=120):
    """问一次智能体。

    带上"当前比赛"再问：知识库里可能有多场比赛的资料，不限定范围它会拿别的比赛来答。
    返回 {'answer': str, 'quotes': [{'index','name','url'}], 'elapsed': float}。
    平台或网络报错时抛 ValueError，由调用方决定怎么提示用户（绝不假装成回答）。
    """
    values = eval_adp.settings()
    prompt = str(question)
    if competition:
        prompt = (f'【当前比赛：{competition}】请只依据这场比赛的资料回答；'
                  f'如果这场比赛资料里没有，就直接说“这场比赛资料中没有找到”，'
                  f'不要引用其他比赛的规定。\n问题：{question}')
    start = time.monotonic()
    parsed, _ = eval_adp.call(values['ADP_CHAT_URL'], values['ADP_APP_KEY'], prompt, timeout=timeout)
    elapsed = time.monotonic() - start
    if parsed.get('errors'):
        raise ValueError('智能体返回错误：' + '；'.join(parsed['errors']))
    return {'answer': parsed.get('answer') or '', 'quotes': parsed.get('quotes') or [],
            'elapsed': round(elapsed, 1)}
