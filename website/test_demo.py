"""Offline integration tests: no platform requests, no production database."""
import http.client
import base64
import json
import os
import tempfile
import threading
import unittest
from unittest.mock import patch


class EligibilityContractTests(unittest.TestCase):
    def test_heading_style_does_not_parse_source_or_time_as_item(self):
        from demo import eligibility
        rows = eligibility('### 队伍人数\n⚪ 资料未规定：暂未找到规定。[来源：通知]\n'
                           '### 报名时间\n❌ 不满足：截止时间 18:00。[来源：补充通知]\n'
                           '### 学校身份\n✅ 满足 项目：本校在校生。[来源：通知]')
        self.assertEqual([r['item'] for r in rows], ['队伍人数', '报名时间', '学校身份'])
        self.assertEqual(rows[0]['source'], '通知')
        self.assertEqual(rows[1]['detail'], '截止时间 18:00。')

    def test_unstructured_line_without_item_is_not_a_card(self):
        from demo import eligibility
        self.assertEqual(eligibility('⚪ 资料未规定：没有找到。[来源：通知]'), [])

    def test_four_states_and_mixed_punctuation(self):
        from demo import eligibility
        rows = eligibility('✅ 满足，人数：1人 [来源：通知]\n❌ 不满足 年级: 不符\n'
                           '⚪ 资料未规定 专业：未找到\n❓ 缺少信息 学校: 未提供')
        self.assertEqual([r['state'] for r in rows], ['satisfied','unsatisfied','not_specified','missing'])
        self.assertEqual(rows[0]['item'], '人数')

    def test_literal_template_is_not_a_qualification_card(self):
        from demo import eligibility
        self.assertEqual(eligibility('✅ 满足 人数条件：说明 [来源：真实资料名]'), [])


class DemoApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'HUIKE_DATA_DIR': cls.tmp.name}):
            import server
        cls.server_module = server
        from store import Store
        cls.original = server.STORE
        server.STORE = Store(cls.tmp.name, use_ocr=False)
        from http.server import ThreadingHTTPServer
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()
        cls.server_module.STORE = cls.original
        cls.tmp.cleanup()

    def setUp(self):
        self.store = self.server_module.STORE
        with self.store.db() as db:
            for table in ('users', 'recruitments', 'profiles', 'agent_sessions', 'qa_turns', 'chunks',
                          'documents', 'uploads', 'competitions'):
                db.execute(f'DELETE FROM {table}')
            db.execute('INSERT INTO competitions VALUES (?,?,?)', ('c1', '测试比赛', '2026'))
        self.cookie = None
        self.request('GET', '/api/status')
        self.register()

    def register(self, account='zhang@example.com', password='huike123', name='小张'):
        """默认每个用例先注册并进入；账号表在 setUp 里已清空。"""
        status, body = self.request('POST', '/api/auth/register',
                                    {'account': account, 'password': password, 'name': name})
        self.assertEqual(status, 200)
        return body['user']

    def sign_in(self, account='zhang@example.com', password='huike123'):
        """登录（账号必须已经注册过）。"""
        status, body = self.request('POST', '/api/auth/login',
                                    {'account': account, 'password': password})
        self.assertEqual(status, 200)
        return body['user']

    def anonymous(self):
        """换一台设备：清掉 Cookie，重新拿一个匿名身份。"""
        self.cookie = None
        self.request('GET', '/api/status')

    def request(self, method, path, data=None, cookie=None):
        con = http.client.HTTPConnection(*self.http.server_address)
        headers = {'Content-Type': 'application/json'}
        if cookie or self.cookie:
            headers['Cookie'] = cookie or self.cookie
        con.request(method, path, json.dumps(data) if data is not None else None, headers)
        res = con.getresponse()
        if res.getheader('Set-Cookie'):
            self.cookie = res.getheader('Set-Cookie').split(';')[0]
        status, body = res.status, json.loads(res.read())
        con.close()
        return status, body

    def test_anonymous_is_blocked_until_login(self):
        """没登录：状态可查，其余接口一律 401。"""
        self.anonymous()
        self.assertEqual(self.request('GET', '/api/status')[0], 200)
        self.assertEqual(self.request('GET', '/api/profile')[0], 401)
        self.assertEqual(self.request('GET', '/api/competitions')[0], 401)
        self.assertEqual(self.request('POST', '/api/profile/save', {'payload': {'major': 'x'}})[0], 401)

    def test_login_binds_the_work_done_before_logging_in(self):
        """升级前匿名存下的画像：第一次登录后仍属于同一个人（不搬走也不丢）。"""
        self.anonymous()
        owner = self.cookie.split('=', 1)[1]          # 这个浏览器的匿名身份
        self.store.save_profile(owner, {'major': '匿名时期'})
        self.register(account='13800000000', name='小李')
        self.assertEqual(self.request('GET', '/api/profile')[1]['profile']['major'], '匿名时期')

    def test_same_account_on_another_device_sees_the_same_profile(self):
        """换台设备用同一账号登录，能看到自己的画像。"""
        self.request('POST', '/api/profile/save', {'payload': {'major': '数字媒体'}})
        self.anonymous()
        self.assertEqual(self.request('GET', '/api/profile')[0], 401)
        self.sign_in()
        self.assertEqual(self.request('GET', '/api/profile')[1]['profile']['major'], '数字媒体')

    def test_unregistered_account_cannot_login(self):
        """注册与登录分开：没注册过就登录不了，也不会被顺手建出账号。"""
        self.anonymous()
        status, body = self.request('POST', '/api/auth/login',
                                    {'account': 'new@example.com', 'password': 'huike123'})
        self.assertEqual(status, 400)
        self.assertIn('还没注册', body['error'])
        self.assertEqual(self.request('GET', '/api/auth/session')[1]['user'], None)

    def test_registering_the_same_account_twice_is_rejected(self):
        self.anonymous()
        status, body = self.request('POST', '/api/auth/register',
                                    {'account': 'Zhang@example.com', 'password': 'other123',
                                     'name': '重复注册'})
        self.assertEqual(status, 400)
        self.assertIn('已经注册', body['error'])
        self.assertEqual(self.request('GET', '/api/profile')[0], 401)      # 注册失败不留会话

    def test_wrong_password_and_bad_account_are_rejected(self):
        self.anonymous()
        status, body = self.request('POST', '/api/auth/login',
                                    {'account': 'zhang@example.com', 'password': 'wrong123'})
        self.assertEqual(status, 400)
        self.assertIn('密码', body['error'])
        self.assertEqual(self.request('GET', '/api/profile')[0], 401)      # 失败不留会话
        self.assertEqual(self.request('POST', '/api/auth/login',
                                      {'account': '不是邮箱', 'password': 'huike123'})[0], 400)

    def test_logout_returns_to_anonymous(self):
        self.assertEqual(self.request('POST', '/api/auth/logout', {})[0], 200)
        self.assertEqual(self.request('GET', '/api/profile')[0], 401)
        self.assertEqual(self.request('GET', '/api/auth/session')[1]['user'], None)

    def test_placeholder_words_are_not_saved_as_profile_content(self):
        """模型把没提到的类别写成"待补充""（未提及）"时，不能当成画像内容。"""
        reply = {'answer': '【画像草稿】\n· 做过什么：参与课程短片\n· 本人负责：待补充\n'
                           '· 希望参与什么：（未提及）\n· 正在学习 / 希望尝试什么：Python\n'
                           '【本次修改】\n新增做过的事情', 'conversation_id': 'placeholder'}
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask', return_value=reply):
            status, result = self.request('POST', '/api/profile/chat', {'text': '做过短片'})
        self.assertEqual(status, 200)
        self.assertEqual(set(result['draft']), {'experiences', 'learning'})
        self.assertEqual(result['draft']['experiences'], '参与课程短片')
        self.assertEqual(result['draft']['learning'], 'Python')
        self.assertEqual(result['changes'],
                         ['新增“做过什么 / 本人负责什么”', '新增“正在学习 / 希望尝试什么”'])
        self.assertIsNone(self.request('GET', '/api/profile')[1]['profile'])   # 草稿不等于保存

    def test_save_and_read_profile(self):
        payload = {'major': '数字媒体', 'experiences': '课程短片｜本人负责剪辑'}
        self.assertEqual(self.request('POST', '/api/profile/save', {'payload': payload})[0], 200)
        _, saved = self.request('GET', '/api/profile')
        self.assertEqual(saved['profile']['experiences'], payload['experiences'])
        self.assertNotIn('visibility', saved)        # 画像不再有对外展示开关
        self.assertEqual(self.request('POST', '/api/profile/visibility', {'visibility': 'public'})[0], 404)

    def test_empty_nested_profile_is_rejected(self):
        self.assertEqual(self.request('POST', '/api/profile/save', {'payload': {'background': {}}})[0], 400)

    def chat(self, text, answer, draft=None):
        """走一轮画像对话：answer 是模型这轮回的话，draft 是页面上正在改的草稿。"""
        payload = {'text': text}
        if draft is not None:
            payload['draft'] = draft
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask',
                   return_value={'answer': answer, 'conversation_id': 'chat'}) as ask:
            status, result = self.request('POST', '/api/profile/chat', payload)
        return status, result, ask

    def test_chat_just_replies_and_keeps_the_transcript(self):
        """聊天只回话：不出草稿、不写画像，双方消息都记下来。"""
        status, result, _ = self.chat('参与过短片', '这件事里你本人主要负责什么？')
        self.assertEqual(status, 200)
        self.assertEqual(result['reply'], '这件事里你本人主要负责什么？')
        self.assertIsNone(result['draft'])
        self.assertEqual([m['role'] for m in result['messages']], ['user', 'assistant'])
        self.assertEqual(result['messages'][0]['text'], '参与过短片')
        self.assertEqual(self.request('GET', '/api/profile/chat')[1]['messages'], result['messages'])
        self.assertIsNone(self.request('GET', '/api/profile')[1]['profile'])

    def test_chat_keeps_a_model_draft_when_the_draft_area_is_empty(self):
        """平台提示词还按老规矩"能整理就整理"时，内容别丢：草稿区空着就收下。"""
        status, result, _ = self.chat('我负责剪辑',
                                      '【画像草稿】\n做过什么：参与短片\n本人负责：剪辑\n【本次修改】\n新增剪辑')
        self.assertEqual(status, 200)
        self.assertEqual(result['draft']['experiences'], '参与短片\n本人负责：剪辑')
        self.assertIn('草稿', result['reply'])
        self.assertIsNone(self.request('GET', '/api/profile')[1]['profile'])

    def test_chat_does_not_overwrite_the_draft_being_edited(self):
        """正在改草稿时，模型顺手整理的草稿不覆盖，只在回复里提一句。"""
        status, result, ask = self.chat('再加一句', '【画像草稿】\n做过什么：另一件事\n【本次修改】\n无',
                                        draft={'experiences': '本人做过剪辑'})
        self.assertEqual(status, 200)
        self.assertIsNone(result['draft'])
        self.assertIn('本人做过剪辑', ask.call_args.args[0])          # 页面上的草稿带过去了
        self.assertIn('没有覆盖', result['reply'])

    def test_draft_endpoint_builds_draft_from_the_transcript(self):
        """点「整理成画像草稿」= 网站发起的整理任务：把对话记录整段带过去。"""
        self.chat('参与过短片', '这件事里你本人主要负责什么？')
        self.chat('负责剪辑', '明白，你负责剪辑。')
        answer = ('【画像草稿】\n· 专业：数字媒体\n· 做过什么：参与短片\n· 本人负责：剪辑\n'
                  '【本次修改】\n· 新增：做过什么')
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask',
                   return_value={'answer': answer, 'conversation_id': 'draft'}) as ask:
            status, result = self.request('POST', '/api/profile/draft', {})
        self.assertEqual(status, 200)
        self.assertEqual(result['draft']['experiences'], '参与短片\n本人负责：剪辑')
        self.assertEqual(result['changes'], ['新增“专业”', '新增“做过什么 / 本人负责什么”'])
        prompt = ask.call_args.args[0]
        self.assertIn('【对话记录】', prompt)
        self.assertIn('负责剪辑', prompt)                            # 不只是最后一句
        self.assertEqual(result['reply'], '')                        # 整理任务不带聊天话术
        self.assertIsNone(self.request('GET', '/api/profile')[1]['profile'])

    def test_draft_needs_something_to_work_with(self):
        self.assertEqual(self.request('POST', '/api/profile/draft', {})[0], 400)

    def test_draft_uses_the_unsaved_draft_as_base(self):
        reply = {'answer': '【画像草稿】\n希望参与什么：视频演示\n【本次修改】\n新增意愿',
                 'conversation_id': 'draft'}
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask', return_value=reply) as ask:
            _, result = self.request('POST', '/api/profile/draft',
                                     {'draft': {'experiences': '本人做过剪辑'}})
        self.assertIn('本人做过剪辑', ask.call_args.args[0])
        self.assertEqual(result['draft']['experiences'], '本人做过剪辑')
        self.assertIsNone(self.request('GET', '/api/profile')[1]['profile'])

    def test_draft_includes_saved_profile_and_does_not_save(self):
        self.request('POST', '/api/profile/save', {'payload': {'major': '数字媒体'}})
        self.chat('我负责剪辑', '好，我记下了。')
        reply = {'answer': '【画像草稿】\n做过什么 / 本人负责什么：短片剪辑\n【本次修改】\n新增剪辑经历',
                 'conversation_id': 'test-profile'}
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask', return_value=reply) as ask:
            status, result = self.request('POST', '/api/profile/draft', {})
        self.assertEqual(status, 200)
        self.assertIn('数字媒体', ask.call_args.args[0])
        self.assertIn('剪辑', result['draft']['experiences'])
        self.assertEqual(result['draft']['major'], '数字媒体')
        self.assertNotIn('experiences', self.request('GET', '/api/profile')[1]['profile'])

    def test_clarification_is_not_a_draft(self):
        status, result, _ = self.chat('做过短片', '这件事里你本人主要负责什么？')
        self.assertEqual(status, 200)
        self.assertIsNone(result['draft'])
        self.assertIn('本人', result['reply'])

    def test_clearing_the_chat_resets_the_agent_session(self):
        self.chat('参与过短片', '好的。')
        owner = self.cookie.split('=', 1)[1]
        self.assertIsNotNone(self.store.conversation_id(owner, 'profile'))
        self.assertEqual(self.request('POST', '/api/profile/chat/reset', {})[0], 200)
        self.assertEqual(self.request('GET', '/api/profile/chat')[1]['messages'], [])
        self.assertIsNone(self.store.conversation_id(owner, 'profile'))   # 换话题：上下文也重置

    def create(self):
        return self.request('POST', '/api/recruitments/create', {
            'competition_id': 'c1', 'title': '找开发队友', 'contact': 'QQ 123',
            'want_role': '网页实现', 'publisher_name': '小张', 'owner_id': 'forged'})

    def test_recruitment_never_carries_the_private_profile(self):
        """主画像只给自己看：招募详情不带画像，连"展示"字段都没有了。"""
        self.request('POST', '/api/profile/save', {'payload': {'experiences': '本人做过剪辑'}})
        status, created = self.create()
        self.assertEqual(status, 200)
        rid = created['recruitment']['id']
        self.assertNotEqual(created['recruitment'].get('owner_id'), 'forged')
        self.assertEqual(created['recruitment']['publisher_name'], '小张')
        self.assertNotIn('profile', created['recruitment'])
        detail = self.request('GET', '/api/recruitments/' + rid)[1]['recruitment']
        self.assertNotIn('profile', detail)
        self.assertNotIn('show_profile', detail)

    def brief_call(self, answer='⚪ 资料未规定：赛道方向\n✅ 提交要求：作品说明文档 [来源：通知]',
                   competition_id='c1', refresh=None):
        payload = {'competition_id': competition_id}
        if refresh is not None:
            payload['refresh'] = refresh
        with patch.object(self.store, 'answer', return_value={
                 'mode': 'grounded', 'answer': answer, 'hits': [],
                 'quotes': [{'index': 1, 'name': '通知.md', 'url': ''}]}) as ask:
            status, body = self.request('POST', '/api/recruit/brief', payload)
        return status, body, ask

    def test_recruit_brief_asks_the_competition_agent_once(self):
        status, body, ask = self.brief_call()
        self.assertEqual(status, 200)
        self.assertIn('提交要求', body['brief']['answer'])
        self.assertEqual(ask.call_count, 1)
        # 同一身份 + 同一场比赛再问：直接用缓存，不再骚扰比赛助手
        with patch.object(self.store, 'answer',
                   side_effect=AssertionError('缓存有效时不该再问一次')):
            _, again = self.request('POST', '/api/recruit/brief', {'competition_id': 'c1'})
        self.assertEqual(again['brief']['answer'], body['brief']['answer'])
        # 点「重新查一次」才真的再查
        with patch.object(self.store, 'answer', return_value={
                 'mode': 'grounded', 'answer': '✅ 第二次查：队伍 1-3 人 [来源：通知]',
                 'hits': [], 'quotes': []}) as ask2:
            _, refreshed = self.request('POST', '/api/recruit/brief',
                                        {'competition_id': 'c1', 'refresh': True})
        self.assertEqual(ask2.call_count, 1)
        self.assertIn('第二次查', refreshed['brief']['answer'])

    def test_recruit_chat_feeds_brief_profile_and_form(self):
        self.request('POST', '/api/profile/save', {'payload': {'experiences': '参与过剧本创作'}})
        self.brief_call()
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask',
                   return_value={'answer': '你想先解决校园里的哪个问题？',
                                 'conversation_id': 'recruit'}) as ask:
            status, result = self.request('POST', '/api/recruit/chat', {
                'competition_id': 'c1', 'title': '找搭子', 'idea': '想做校园服务智能体',
                'want_role': '会做后端', 'text': '我想做校园服务智能体，但不太会代码'})
        self.assertEqual(status, 200)
        self.assertEqual(result['reply'], '你想先解决校园里的哪个问题？')
        prompt = ask.call_args.args[0]
        self.assertIn('【招募协助】', prompt)
        self.assertIn('【比赛要求（来自比赛助手，含来源）】', prompt)
        self.assertIn('提交要求', prompt)                 # 比赛助手的结果带进去了
        self.assertIn('通知.md', prompt)                  # 来源也带进去了
        self.assertIn('参与过剧本创作', prompt)            # 主画像带进去了
        self.assertIn('想做校园服务智能体', prompt)        # 当前表单也带进去了
        # 只是讨论，不动画像
        self.assertEqual(self.request('GET', '/api/profile')[1]['profile'],
                         {'experiences': '参与过剧本创作'})

    def test_recruit_assist_needs_a_known_competition(self):
        for path in ('/api/recruit/brief', '/api/recruit/chat', '/api/recruit/draft'):
            self.assertEqual(self.request('POST', path, {'competition_id': 'missing'})[0], 400)
            self.assertEqual(self.request('POST', path, {})[0], 400)

    def test_recruit_draft_returns_three_fields_and_profile_hits(self):
        self.request('POST', '/api/profile/save',
                     {'payload': {'experiences': '参与过剧本创作', 'learning': '正在学 Python'}})
        self.brief_call()
        answer = ('【招募草稿】\n· 招募标题：校园服务智能体找搭子\n'
                  '· 项目想法：为校园同学解决信息找不到的问题，结合参与过剧本创作的经历，本人负责演示脚本\n'
                  '· 希望队友参与：会做后端接口的同学')
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask',
                   return_value={'answer': answer, 'conversation_id': 'recruit'}) as ask:
            status, result = self.request('POST', '/api/recruit/draft',
                                          {'competition_id': 'c1', 'text': '整理一下'})
        self.assertEqual(status, 200)
        self.assertEqual(result['draft']['title'], '校园服务智能体找搭子')
        self.assertIn('本人负责演示脚本', result['draft']['idea'])
        self.assertEqual(result['draft']['want_role'], '会做后端接口的同学')
        self.assertEqual(result['profile_hits'], ['参与过剧本创作'])   # 本机比对出的原话
        self.assertIn('严格按下面三行输出', ask.call_args.args[0])
        # 讨论过程不会写进画像，也不会写进招募
        self.assertEqual(self.request('GET', '/api/profile')[1]['profile'],
                         {'experiences': '参与过剧本创作', 'learning': '正在学 Python'})
        self.assertEqual(self.request('GET', '/api/recruitments?mine=1')[1]['recruitments'], [])

    def test_recruit_draft_rejects_an_unusable_answer(self):
        self.brief_call()
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask',
                   return_value={'answer': '我们再聊两句吧。', 'conversation_id': 'recruit'}):
            status, body = self.request('POST', '/api/recruit/draft',
                                        {'competition_id': 'c1', 'text': '整理一下'})
        self.assertEqual(status, 400)
        self.assertIn('没有给出可用', body['error'])

    def test_profile_hits_flags_quotes_and_paraphrases(self):
        """整句照搬、或改写但要点重合才算"用到了画像"；只是碰巧撞一个词不算。"""
        from demo import profile_hits
        profile = {'experiences': '参与过校园短片的剧本创作', 'learning': '正在学 Python'}
        self.assertEqual(profile_hits({'idea': '结合参与过剧本创作的经历，负责脚本'}, profile),
                         ['参与过校园短片的剧本创作'])
        self.assertEqual(profile_hits({'idea': '参与过校园短片的剧本创作'}, profile),
                         ['参与过校园短片的剧本创作'])
        self.assertEqual(profile_hits({'idea': '本人可负责创意策划相关内容'}, profile), [])
        self.assertEqual(profile_hits({'idea': '想做校园服务智能体，找会写代码的同学'}, profile), [])

    def test_close_and_summary(self):
        _, created = self.create()
        rid = created['recruitment']['id']
        self.assertEqual(self.request('GET', '/api/competitions/c1/summary')[1]['recruitment_count'], 1)
        self.request('POST', '/api/recruitments/update', {'id': rid, 'status': 'closed'})
        self.assertEqual(self.request('GET', '/api/recruitments')[1]['recruitments'], [])
        self.assertEqual(len(self.request('GET', '/api/recruitments?mine=1')[1]['recruitments']), 1)
        self.assertEqual(self.request('GET', '/api/competitions/c1/summary')[1]['recruitment_count'], 0)

    def test_other_account_cannot_edit(self):
        _, created = self.create()
        owner_cookie = self.cookie
        self.anonymous()
        self.register(account='other@example.com', name='别人')        # 另一个账号登进来
        status, _ = self.request('POST', '/api/recruitments/update', {
            'id': created['recruitment']['id'], 'title': '冒改', 'owner_id': owner_cookie})
        self.assertEqual(status, 400)

    def test_competition_reads_confirmed_profile_and_returns_cards(self):
        self.request('POST', '/api/profile/save', {'payload': {'major': '数字媒体'}})
        with patch.object(self.store, 'answer', return_value={'answer':
             '✅ 满足 人数：允许一人 [来源：通知]\n❓ 缺少信息 学校: 请补充', 'hits': []}) as ask:
            status, result = self.request('POST', '/api/ask', {'competition_id': 'c1', 'query': '找队友，能参赛吗'})
        self.assertEqual(status, 200)
        self.assertIn('数字媒体', ask.call_args.kwargs['context'])
        self.assertEqual(len(result['eligibility']), 2)
        self.assertIn('发布招募', result['recruit_hint'])

    def test_long_profile_does_not_pollute_keyword_search(self):
        self.request('POST', '/api/profile/save', {'payload': {'experiences': '本人做过剪辑。' * 180}})
        with patch('ai.configured', return_value=False), \
             patch.object(self.store, 'search', wraps=self.store.search) as search:
            status, _ = self.request('POST', '/api/ask', {'competition_id': 'c1', 'query': '人数'})
        self.assertEqual(status, 200)
        self.assertEqual(search.call_args.args[1], '人数')

    def test_recruitment_empty_update_rejected(self):
        _, created = self.create()
        status, _ = self.request('POST', '/api/recruitments/update', {'id': created['recruitment']['id'], 'title': ''})
        self.assertEqual(status, 400)

    def test_upload_without_role_keeps_original_and_search(self):
        from test_store import pdf_bytes
        pdf = pdf_bytes()
        with patch('ai.configured', return_value=False), patch('clients.knowledge_base.push',
             return_value={'status': 'skipped', 'message': 'offline'}):
            status, upload = self.request('POST', '/api/upload', {
                'filename': 'notice.pdf', 'content': base64.b64encode(pdf).decode()})
            self.assertEqual(status, 200)
            status, document = self.request('POST', '/api/confirm', {
                'upload_id': upload['upload_id'], 'name': '新比赛', 'edition': '2026',
                'title': '比赛通知', 'uploader': '小张'})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.file('documents', document['document_id']), pdf)
        rows = self.store.search(document['competition_id'], 'members')
        self.assertIn('1-5', rows[0]['text'])

    def test_modifications_are_computed_from_draft_not_model_claim(self):
        """「本次修改」由我们自己算，不看模型在【本次修改】里写什么（它写的是"无"）。"""
        self.chat('我是大二', '好的。')
        reply = {'answer': '【画像草稿】\n年级：大二\n【本次修改】\n无', 'conversation_id': 'changes'}
        with patch('clients.profile_agent.configured', return_value=True), \
             patch('clients.profile_agent.ask', return_value=reply):
            _, result = self.request('POST', '/api/profile/draft', {})
        self.assertTrue(any('年级' in change for change in result['changes']))


if __name__ == '__main__':
    unittest.main()
