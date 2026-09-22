"""Offline browser-test fixture. Uses temporary storage and forbids external calls."""
import os
import tempfile
from unittest.mock import patch
from http.server import ThreadingHTTPServer


def profile_reply(query, **kwargs):
    """离线画像智能体：网站发起整理任务时出草稿，日常对话只回话。

    招募协助先判断，因为它的整理任务里也写着"网站发起的整理任务"。
    """
    if '【招募协助】' in query:
        if '严格按下面三行输出' in query:
            return {'answer': ('招募标题：校园服务智能体找搭子\n'
                               '项目想法：为校园里的同学解决信息找不到的问题，本人负责前端与演示脚本\n'
                               '希望队友参与：会做后端接口的同学'),
                    'conversation_id': 'offline-recruit'}
        return {'answer': '可以，先想一个具体的校园场景：你最想先解决哪个问题？',
                'conversation_id': 'offline-recruit'}
    if '网站发起的整理任务' in query:
        return {'answer': ('【画像草稿】\n· 专业：数字媒体\n· 年级：大二\n· 做过什么：参与短片\n'
                           '· 本人负责：剪辑\n· 希望参与：视频演示\n· 正在学习 / 希望尝试：Python\n'
                           '【本次修改】\n· 新增：做过什么、本人负责'),
                'conversation_id': 'offline-profile'}
    if '本人剪辑' in query:
        answer = '明白，你本人负责剪辑。这个短片是课程作业还是自己做的？'
    else:
        answer = '这件事里你本人主要负责什么？'
    return {'answer': answer, 'conversation_id': 'offline-profile'}


def competition_reply(query, **kwargs):
    """问队友时给一条平台引用（用来验证"有引用就把本地片段收起来"）。

    注意：发给智能体的提示词里本来就写着"不推断队友缺口、不推荐队友"，
    所以要截取【用户问题】到【本人已确认的背景】之间的那一小段，不能搜整个 query。
    """
    question = str(query).split('【用户问题】')[-1].split('【本人已确认的背景】')[0]
    if '赛道' in question:                     # 招募协助：先查这场比赛的要求
        return {'answer': '⚪ 资料未规定：赛道方向（资料里没写具体赛道）\n'
                          '✅ 提交要求：需要提交作品与说明文档 [来源：演示通知]\n'
                          '❓ 缺少信息 队伍人数：资料未写明人数上限 [来源：演示通知]',
                'conversation_id': 'offline-competition',
                'quotes': [{'index': 1, 'name': '演示通知（知识库）.md', 'url': ''}]}
    quotes = ([{'index': 1, 'name': '演示通知（知识库）.md', 'url': ''}]
              if '队友' in question else [])
    return {'answer': '✅ 满足 人数：规则允许一人 [来源：演示通知]\n❓ 缺少信息 学校：请补充学校',
            'conversation_id': 'offline-competition', 'quotes': quotes}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='huike-browser-') as root:
        os.environ['HUIKE_DATA_DIR'] = root
        import server
        server.STORE.use_ocr = False
        with server.STORE.db() as db:
            db.execute('INSERT INTO competitions VALUES (?,?,?)', ('c1', '演示比赛', '2026'))
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask', side_effect=profile_reply), \
             patch('clients.agent_client.configured', return_value=True), \
             patch('clients.agent_client.ask', side_effect=competition_reply), \
             patch('clients.knowledge_base.push', return_value={'status': 'skipped', 'message': '离线测试'}), \
             patch('eval_adp.call', side_effect=AssertionError('Browser tests must not access ADP')), \
             patch('ai.configured', return_value=False):
            ThreadingHTTPServer(('127.0.0.1', int(os.environ.get('HUIKE_PORT', '8777'))), server.Handler).serve_forever()
