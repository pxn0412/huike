import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from store import Store, per_document
import adp
import agent
import ai
import eval_adp
import evidence_scope


def pdf_bytes(text='Teams can have 1-5 members. Registration deadline 2026-06-30.'):
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 40 750 Td ({text}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        self.ai_patch = patch('ai.configured', return_value=False)
        self.ai_patch.start()
        # 测试绝不联网：智能体入口也关掉（只有真人用网页时才真的去问平台）。
        self.agent_patch = patch('store.clients.agent_client.configured', return_value=False)
        self.agent_patch.start()
        # 测试绝不联网：把“自动进知识库”替换成假的（真上传只在真人使用时发生）。
        self.kb_patch = patch('store.clients.knowledge_base.push',
                              return_value={'status': 'uploaded', 'doc_id': 'test-doc', 'message': ''})
        self.kb_push = self.kb_patch.start()

    def tearDown(self):
        self.ai_patch.stop()
        self.agent_patch.stop()
        self.kb_patch.stop()
        self.tmp.cleanup()

    def upload(self, content=None):
        return self.store.upload('2026-notice.pdf', content or pdf_bytes())

    def confirm(self, upload, **kwargs):
        data = {'upload_id': upload['upload_id'], 'title': 'Notice', 'name': 'Competition A',
                'edition': '2026', 'uploader': 'Test student', 'role': 'student'}
        data.update(kwargs)
        if 'competition_id' not in data:
            exact = next((item for item in self.store.competitions()
                          if item['name'] == data['name'] and item['edition'] == data['edition']), None)
            if exact:
                data['competition_id'] = exact['id']
        return self.store.confirm(data)

    def test_confirmation_required_and_page_preserved(self):
        upload = self.upload()
        self.assertEqual(self.store.competitions(), [])
        result = self.confirm(upload)
        self.assertIsNone(self.store.file('uploads', upload['upload_id']))
        self.assertEqual(self.store.file('documents', result['document_id']), pdf_bytes())
        hits = self.store.search(result['competition_id'], 'members')
        self.assertEqual(hits[0]['page'], 2)
        self.assertIn('1-5', hits[0]['text'])

    def test_cancel_cannot_commit_or_read(self):
        upload = self.upload()
        self.store.cancel(upload['upload_id'])
        with self.assertRaises(ValueError):
            self.confirm(upload)
        self.assertIsNone(self.store.file('uploads', upload['upload_id']))
        self.assertEqual(self.store.competitions(), [])

    def test_duplicate_and_new_version(self):
        first = self.confirm(self.upload())
        duplicate = self.confirm(self.upload())
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(first['document_id'], duplicate['document_id'])
        new = self.confirm(self.upload(pdf_bytes('Teams can have 2-5 members.')))
        self.assertFalse(new['duplicate'])
        self.assertEqual(len(self.store.documents(first['competition_id'])), 2)

    def test_short_and_official_competition_names_require_explicit_association(self):
        first = self.confirm(self.upload(), name='“慧科杯”AI创新大赛')
        official = '浙江广厦建设职业技术大学2026年“慧科杯”AI创新大赛'
        extracted = {'title': '新通知', 'name': official, 'edition': '2026年',
                     'publisher': '', 'published_at': '', 'track': ''}
        with patch('ai.configured', return_value=True), \
                patch('ai.extract_identity', return_value=extracted):
            new_upload = self.upload(pdf_bytes('AI track requires an online link.'))
        self.assertEqual([item['id'] for item in new_upload['competition_matches']],
                         [first['competition_id']])
        matches = self.store.match_competitions(official, '2026年')
        self.assertEqual([item['id'] for item in matches], [first['competition_id']])
        with self.assertRaisesRegex(ValueError, '已有比赛'):
            self.confirm(new_upload, name=official, edition='2026年')
        second = self.confirm(new_upload, name=official, edition='2026年',
                              competition_id=first['competition_id'])
        self.assertEqual(second['competition_id'], first['competition_id'])
        self.assertEqual(len(self.store.competitions()), 1)

    def test_exact_existing_competition_is_not_selected_implicitly(self):
        first = self.confirm(self.upload())
        next_upload = self.upload(pdf_bytes('A second official notice.'))
        with self.assertRaisesRegex(ValueError, '选择已有比赛'):
            self.store.confirm({'upload_id': next_upload['upload_id'], 'title': 'Next notice',
                                'name': 'Competition A', 'edition': '2026',
                                'uploader': 'Test student'})
        self.assertEqual(len(self.store.documents(first['competition_id'])), 1)
        confirmed = self.store.confirm({'upload_id': next_upload['upload_id'], 'title': 'Next notice',
                                        'competition_id': first['competition_id'],
                                        'uploader': 'Test student'})
        self.assertEqual(confirmed['competition_id'], first['competition_id'])

    def test_similar_title_can_be_deliberately_created_as_a_different_competition(self):
        first = self.confirm(self.upload(), name='“慧科杯”AI创新大赛')
        other = self.confirm(self.upload(pdf_bytes('Different school contest.')),
                             name='另一所学校2026年“慧科杯”AI创新大赛',
                             create_new_confirmed=True)
        self.assertNotEqual(other['competition_id'], first['competition_id'])
        self.assertEqual(len(self.store.competitions()), 2)

    def test_matching_does_not_cross_edition_or_unrelated_titles(self):
        self.confirm(self.upload(), name='“慧科杯”AI创新大赛')
        self.assertEqual(self.store.match_competitions('2027年“慧科杯”AI创新大赛', '2027'), [])
        self.assertEqual(self.store.match_competitions('移动杯AI素养赛', '2026'), [])

    def test_event_cup_nickname_is_also_a_candidate(self):
        first = self.confirm(self.upload(), name='浙江广厦建设职业技术大学“慧科杯”AI创新大赛')
        self.assertEqual([item['id'] for item in self.store.match_competitions('慧科杯', '2026')],
                         [first['competition_id']])

    def test_missing_edition_still_suggests_matching_cup_without_auto_association(self):
        first = self.confirm(self.upload(), name='浙江广厦建设职业技术大学“慧科杯”AI创新大赛')
        matches = self.store.match_competitions('“慧科杯”AI创新大赛', '')
        self.assertEqual([item['id'] for item in matches], [first['competition_id']])
        poster = self.upload(pdf_bytes('A poster about AI applications.'))
        with self.assertRaisesRegex(ValueError, '已有比赛'):
            self.store.confirm({'upload_id': poster['upload_id'], 'title': 'Poster',
                                'name': '“慧科杯”AI创新大赛', 'edition': '2026',
                                'uploader': 'Test student'})

    def test_short_exact_name_is_suggested_but_not_merged_automatically(self):
        first = self.confirm(self.upload(), name='演示比赛')
        self.assertEqual([item['id'] for item in self.store.match_competitions('演示比赛2026', '2026')],
                         [first['competition_id']])

    def test_no_cross_competition_results(self):
        first = self.confirm(self.upload())
        other = self.confirm(self.upload(pdf_bytes('This contest is about astronomy.')), name='Competition B')
        self.assertEqual(self.store.search(other['competition_id'], 'members'), [])
        self.assertTrue(self.store.search(first['competition_id'], 'members'))
        with self.assertRaises(ValueError):
            self.store.search(None, 'members')

    def test_overview_question_retrieves_notice_intro_without_word_overlap(self):
        result = self.confirm(self.upload(), title='关于举办慧科杯的通知')
        poster = self.confirm(self.upload(pdf_bytes('Poster OCR noise.')), title='赛道海报')
        with self.store.db() as db:
            db.execute('UPDATE chunks SET heading=?, text=? WHERE document_id=?',
                       ('赛事说明', '本届慧科杯面向在校生开展创新作品展示。', result['document_id']))
            db.execute('UPDATE chunks SET heading=?, text=? WHERE document_id=?',
                       ('', '扫码后联系老师。', poster['document_id']))
        hits = self.store.search(result['competition_id'], '这是什么比赛')
        self.assertTrue(any('本届慧科杯' in hit['text'] for hit in hits))
        self.assertNotIn(poster['document_id'], {hit['document_id'] for hit in hits})
        self.assertEqual(self.store.search(result['competition_id'], '学分兑换办法？'), [])

    def test_track_question_excludes_other_track_but_keeps_common_notice(self):
        common = self.confirm(self.upload())
        ai_doc = self.confirm(self.upload(pdf_bytes('AI agent submission deadline.')),
                              track='仅 AI 智能体应用赛')
        pbl_doc = self.confirm(self.upload(pdf_bytes('PBL submission deadline.')),
                               track='仅 PBL 项目开发赛')
        with self.store.db() as db:
            for document in (common, ai_doc, pbl_doc):
                db.execute('UPDATE chunks SET text=? WHERE document_id=?',
                           ('提交材料 截止时间', document['document_id']))
        hits = self.store.search(common['competition_id'], 'AI智能体赛道提交材料')
        ids = {hit['document_id'] for hit in hits}
        self.assertIn(common['document_id'], ids)
        self.assertIn(ai_doc['document_id'], ids)
        self.assertNotIn(pbl_doc['document_id'], ids)
        all_hits = self.store.search(common['competition_id'], '提交材料')
        self.assertIn(pbl_doc['document_id'], {hit['document_id'] for hit in all_hits})

    def test_unindexed_requested_track_does_not_retrieve_other_track(self):
        pbl_doc = self.confirm(self.upload(pdf_bytes('PBL submission deadline.')),
                               track='仅 PBL 项目开发赛')
        with self.store.db() as db:
            db.execute('UPDATE chunks SET text=? WHERE document_id=?',
                       ('智能体提交材料截止时间', pbl_doc['document_id']))
        self.assertEqual(self.store.search(pbl_doc['competition_id'], '智能体提交材料截止时间'), [])

    def test_one_notice_with_explicit_track_headings_keeps_each_section_scoped(self):
        result = self.confirm(self.upload())
        pages = ['一、通用要求\n队伍人数为一至五人。\n二、AI智能体应用赛\nAI智能体提交日期为八月二十二日。'
                 '\n三、PBL项目开发赛\nPBL提交日期为八月十八日。']
        with self.store.db() as db:
            self.store._index_document(db, result['document_id'], result['competition_id'], pages)
        blocks = self.store.chunks(result['document_id'])
        self.assertTrue(any('智能体' in block['track'] for block in blocks))
        self.assertTrue(any('PBL' in block['track'] for block in blocks))
        hits = self.store.search(result['competition_id'], 'AI智能体赛道提交日期')
        self.assertTrue(any('二十二日' in hit['text'] for hit in hits))
        self.assertFalse(any('十八日' in hit['text'] for hit in hits))

    def test_legacy_chunks_without_scope_are_classified_at_retrieval(self):
        result = self.confirm(self.upload())
        ai_doc = self.confirm(self.upload(pdf_bytes('AI submission date.')),
                              track='仅 AI 智能体应用赛')
        with self.store.db() as db:
            db.execute("UPDATE chunks SET track='', heading='PBL项目开发赛', text='PBL提交日期为八月十八日' "
                       'WHERE document_id=?', (result['document_id'],))
            db.execute("UPDATE chunks SET text='AI智能体提交日期为八月二十二日' WHERE document_id=?",
                       (ai_doc['document_id'],))
        hits = self.store.search(result['competition_id'], 'AI智能体赛道提交日期')
        self.assertEqual({hit['document_id'] for hit in hits}, {ai_doc['document_id']})

    def test_mixed_notice_cannot_be_marked_as_one_track_for_the_whole_file(self):
        with patch('store.extract_document', return_value=(
                ['AI智能体应用赛提交链接。PBL项目开发赛提交方案。'], {})):
            upload = self.upload()
        with self.assertRaisesRegex(ValueError, '多个赛道'):
            self.confirm(upload, track='仅 AI 智能体应用赛')
        result = self.confirm(upload, track='')
        self.assertEqual(len(self.store.documents(result['competition_id'])), 1)

    def test_website_answer_does_not_call_unfiltered_shared_agent(self):
        first = self.confirm(self.upload())
        self.confirm(self.upload(pdf_bytes('A second contest.')), name='Competition B')
        expected = {'answer': '人数为 1-5 人。', 'citations': [], 'status': 'found'}
        with patch('ai.configured', return_value=True), \
                patch('ai.grounded_answer', return_value=expected) as grounded, \
                patch('store.clients.agent_client.configured', return_value=True), \
                patch('store.clients.agent_client.ask') as shared_agent:
            answer = self.store.answer(first['competition_id'], 'members')
        self.assertEqual(answer['mode'], 'grounded')
        self.assertEqual({item['document_id'] for item in grounded.call_args.args[2]},
                         {first['document_id']})
        shared_agent.assert_not_called()

    def test_grounded_followup_uses_only_owners_current_competition_history(self):
        first = self.confirm(self.upload())
        other = self.confirm(self.upload(pdf_bytes('Other contest.')), name='Competition B')
        expected = {'answer': '见原文。', 'citations': [], 'status': 'found'}
        with patch('ai.configured', return_value=True), \
                patch('ai.grounded_answer', return_value=expected) as grounded:
            one = self.store.answer(first['competition_id'], 'members', owner_id='owner-a')
            two = self.store.answer(first['competition_id'], '那材料呢', owner_id='owner-a')
            other_owner = self.store.answer(first['competition_id'], '那材料呢', owner_id='owner-b')
            other_comp = self.store.answer(other['competition_id'], '那材料呢', owner_id='owner-a')
            self.store.reset_conversation('owner-a', first['competition_id'])
            after_reset = self.store.answer(first['competition_id'], '那材料呢', owner_id='owner-a')
        self.assertFalse(one['continued'])
        self.assertTrue(two['continued'])
        self.assertIn('members', grounded.call_args_list[1].args[0])
        self.assertFalse(other_owner['continued'])
        self.assertFalse(other_comp['continued'])
        self.assertFalse(after_reset['continued'])

    def test_invalid_input_cannot_create_orphan_competition(self):
        with self.assertRaises(ValueError):
            self.store.upload('bad.pdf', b'not a pdf')
        upload = self.upload()
        with self.assertRaises(ValueError):
            self.confirm(upload, competition_id='missing')
        with self.assertRaises(ValueError):
            self.confirm(upload, published_at='2026-99-99')
        self.assertEqual(self.store.competitions(), [])

    def test_no_ai_means_no_generated_answer(self):
        result = self.confirm(self.upload())
        answer = self.store.answer(result['competition_id'], 'members')
        self.assertEqual(answer['mode'], 'keyword_evidence')
        self.assertIsNone(answer['answer'])
        self.assertTrue(answer['hits'])

    def test_upload_is_indexed_as_chunks(self):
        result = self.confirm(self.upload(), track='仅 AI 智能体应用赛')
        self.assertGreater(result['chunk_count'], 0)
        blocks = self.store.chunks(result['document_id'])
        self.assertEqual(result['chunk_count'], len(blocks))
        self.assertEqual(blocks[0]['seq'], 1)
        self.assertEqual(blocks[0]['source'], 'chunking')
        self.assertEqual(blocks[0]['track'], '仅 AI 智能体应用赛')
        hits = self.store.search(result['competition_id'], 'members')
        self.assertEqual(hits[0]['page'], 2)
        self.assertTrue(hits[0]['chunk'])
        self.assertIn('1-5', hits[0]['text'])

    def test_document_list_reports_chunk_count(self):
        result = self.confirm(self.upload())
        document = self.store.documents(result['competition_id'])[0]
        self.assertEqual(document['chunk_count'], result['chunk_count'])

    def test_search_returns_nothing_for_absent_fact(self):
        result = self.confirm(self.upload())
        self.assertEqual(self.store.search(result['competition_id'], '指导老师'), [])
        answer = self.store.answer(result['competition_id'], '指导老师')
        self.assertEqual(answer['hits'], [])

    def test_missing_chunks_are_rebuilt_when_store_reopens(self):
        result = self.confirm(self.upload())
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        with connection:
            connection.execute('DELETE FROM chunks')
        connection.close()
        reopened = Store(self.tmp.name, use_ocr=False)
        self.assertEqual(len(reopened.chunks(result['document_id'])), result['chunk_count'])

    def test_chunks_of_unknown_document_raise(self):
        with self.assertRaises(ValueError):
            self.store.chunks('missing-document')

    def test_confirm_pushes_same_chunks_to_knowledge_base(self):
        result = self.confirm(self.upload(), track='仅 AI 智能体应用赛')
        self.assertEqual(result['knowledge_base']['status'], 'uploaded')
        self.assertEqual(self.kb_push.call_count, 1)
        content, filename = self.kb_push.call_args[0][:2]
        self.assertTrue(filename.endswith('.md'), filename)
        self.assertIn('## 块 N01', content.decode('utf-8'))
        self.assertIn('1-5', content.decode('utf-8'))

    def test_knowledge_base_filename_is_unique_and_competition_scoped(self):
        """技术文件名 = 比赛ID-资料ID-内容哈希前12位：同名通知/同名不同届不再互相顶掉。"""
        result = self.confirm(self.upload(), published_at='2026-06-05')
        _, filename = self.kb_push.call_args[0][:2]
        self.assertTrue(filename.startswith(f"{result['competition_id']}-{result['document_id']}-"))
        self.assertRegex(filename, r'^[a-f0-9]{32}-[a-f0-9]{32}-[a-f0-9]{12}\.md$')
        document = self.store.documents(result['competition_id'])[0]
        self.assertEqual(document['kb_file_name'], filename)

    def test_indexed_hash_changes_when_content_changes(self):
        """内容哈希取自“上传给平台的那份 Markdown”，内容变了名字必须跟着变。"""
        first = self.confirm(self.upload())
        _, first_name = self.kb_push.call_args[0][:2]
        second = self.confirm(self.upload(pdf_bytes('Teams can have 2-5 members.')))
        _, second_name = self.kb_push.call_args[0][:2]
        self.assertNotEqual(first_name, second_name)
        first_doc = {d['id']: d for d in self.store.documents(first['competition_id'])}
        full_hash = first_doc[first['document_id']]['indexed_content_hash']
        self.assertEqual(first_name.rsplit('-', 1)[1], f'{full_hash[:12]}.md')
        self.assertEqual(first_doc[second['document_id']]['document_status'], 'current')

    def test_knowledge_base_status_is_stored_on_document(self):
        """知识库状态只存在 kb_* 列里，不再往 metadata 里塞一份（避免两处不一致）。"""
        result = self.confirm(self.upload())
        document = self.store.documents(result['competition_id'])[0]
        self.assertEqual(document['kb_status'], 'uploaded')
        self.assertEqual(document['kb_doc_id'], 'test-doc')
        self.assertNotIn('knowledge_base', document['metadata'])
        self.assertRegex(document['kb_file_name'], r'^[a-f0-9]{32}-[a-f0-9]{32}-[a-f0-9]{12}\.md$')

    def test_knowledge_base_failure_keeps_local_document(self):
        with patch('store.clients.knowledge_base.push',
                   return_value={'status': 'failed', 'doc_id': '', 'message': '网络不可达'}):
            result = self.confirm(self.upload())
        self.assertEqual(result['knowledge_base']['status'], 'failed')
        self.assertEqual(len(self.store.documents(result['competition_id'])), 1)
        self.assertTrue(self.store.search(result['competition_id'], 'members'))

    def test_without_grounded_model_does_not_use_shared_agent(self):
        result = self.confirm(self.upload())
        with patch('store.clients.agent_client.configured', return_value=True), \
                patch('store.clients.agent_client.ask') as asked:
            answer = self.store.answer(result['competition_id'], 'members')
        self.assertEqual(answer['mode'], 'keyword_evidence')
        self.assertTrue(answer['hits'])
        asked.assert_not_called()

    def test_grounded_answer_failure_is_reported_without_using_shared_agent(self):
        result = self.confirm(self.upload())
        with patch('ai.configured', return_value=True), \
                patch('ai.grounded_answer', side_effect=ValueError('网络不可达')), \
                patch('store.clients.agent_client.ask') as shared_agent:
            answer = self.store.answer(result['competition_id'], 'members')
        self.assertEqual(answer['mode'], 'grounded_failed')
        self.assertIsNone(answer['answer'])
        self.assertIn('网络不可达', answer['agent_error'])
        self.assertTrue(answer['hits'])
        shared_agent.assert_not_called()

    def test_pending_documents_are_pushed_without_clicking(self):
        result = self.confirm(self.upload())
        # 造一份"功能上线前入库"的资料：把它已记录的知识库状态清掉。
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        with connection:
            connection.execute("UPDATE documents SET kb_status=''")
        connection.close()
        self.kb_push.reset_mock()
        pending = self.store.sync_pending_documents()
        self.assertEqual(pending, [result['document_id']])
        self.assertEqual(self.kb_push.call_count, 1)
        document = self.store.documents(result['competition_id'])[0]
        self.assertEqual(document['kb_status'], 'uploaded')

    def test_preserves_extraction_metadata(self):
        upload = self.upload()
        result = self.confirm(upload, title='User corrected title')
        doc = self.store.documents(result['competition_id'])[0]
        self.assertEqual(doc['title'], 'User corrected title')
        self.assertEqual(doc['metadata']['ai_extracted_result']['title'], '2026-notice')
        self.assertEqual(doc['metadata']['extraction_method'], 'filename_only')

    def test_ai_unknown_field_does_not_erase_filename_hint(self):
        empty = {key: '' for key in ('title','name','edition','publisher','published_at','track')}
        with patch('ai.configured', return_value=True), \
                patch('ai.extract_identity', return_value={**empty, 'title': 'AI title', 'name': 'AI name'}):
            upload = self.store.upload('2026-notice.pdf', pdf_bytes())
        self.assertEqual(upload['extraction_method'], 'ai')
        self.assertEqual(upload['suggested']['title'], 'AI title')
        self.assertEqual(upload['suggested']['name'], 'AI name')
        # 文件名里的年份线索必须保留，不能被模型的空值覆盖。
        self.assertEqual(upload['suggested']['edition'], '2026')
        self.assertEqual(upload['metadata']['ai_extracted_result']['edition'], '2026')


class CitationTests(unittest.TestCase):
    def test_model_cannot_invent_source(self):
        evidence = [{'evidence_id':'E1','text':'Team size is 1-5.','page':2,'document_id':'trusted'}]
        with patch('ai.complete', return_value={'answer':'You qualify.', 'citations':[{'evidence_id':'E99','quote':'anything'}]}):
            result = ai.grounded_answer('Can I join?', {}, evidence)
        self.assertEqual(result['status'], 'insufficient')
        self.assertNotIn('You qualify', result['answer'])

    def test_citation_location_comes_from_server(self):
        evidence = [{'evidence_id':'E1','text':'Team size is 1-5.','page':2,'document_id':'trusted'}]
        with patch('ai.complete', return_value={'answer':'人数为1-5。','status':'found', 'citations':[{'evidence_id':'E1','quote':'1-5','page':999}]}):
            result = ai.grounded_answer('人数?', {}, evidence)
        self.assertEqual(result['citations'][0]['page'], 2)


class EvidenceScopeTests(unittest.TestCase):
    def test_legacy_unspecified_scope_is_not_treated_as_a_track(self):
        self.assertEqual(evidence_scope.key('赛道范围未单独标注'), '')
        self.assertEqual(evidence_scope.block_track('PBL项目开发赛', '提交材料',
                         '赛道范围未单独标注'), 'PBL 项目开发赛')

    def test_explicit_track_is_selected_even_without_documents_for_it(self):
        self.assertEqual(evidence_scope.question_track('AI智能体赛道要交什么？',
                         ['仅 PBL 项目开发赛']), 'ai-agent')
        self.assertEqual(evidence_scope.question_track('PBL材料是什么？',
                         ['仅 AI 智能体应用赛']), 'pbl')
        self.assertIsNone(evidence_scope.question_track('AI智能体赛道和PBL赛道有什么区别？',
                          ['仅 AI 智能体应用赛']))

    def test_contest_title_with_ai_does_not_select_ai_track(self):
        tracks = ['仅 AI 智能体应用赛', '仅 PBL 项目开发赛']
        self.assertIsNone(evidence_scope.question_track('AI创新大赛什么时候截止？', tracks))
        self.assertEqual(evidence_scope.question_track('PBL材料是什么？', tracks), 'pbl')

    def test_confirmed_ai_track_alias_matches_explicit_ai_agent_question(self):
        tracks = ['AI智能应用赛道', '仅 PBL 项目开发赛']
        self.assertEqual(evidence_scope.question_track('AI智能体赛道的材料？', tracks), 'ai-agent')
        self.assertTrue(evidence_scope.permits('AI 智能体应用赛', 'ai-agent'))

    def test_unambiguous_track_name_in_body_scopes_unheaded_excerpt(self):
        self.assertEqual(evidence_scope.block_track('', 'AI智能体应用赛提交作品链接。'),
                         'AI 智能体应用赛')
        self.assertEqual(evidence_scope.block_track('', 'AI智能体应用赛和PBL项目开发赛均可报名。'),
                         '多赛道混合，待核对')
        self.assertEqual(evidence_scope.block_track('AI智能体应用赛',
                         'AI智能体应用赛与PBL项目开发赛时间不同。'), '多赛道混合，待核对')
        self.assertFalse(evidence_scope.permits('多赛道混合，待核对', 'ai-agent'))


class PushDocumentTests(unittest.TestCase):
    """push_document 只负责判断状态；测试里一律不联网。"""

    base = {'ADP_AUTO_UPLOAD': '', 'ADP_KB_ID': '2100783876846755776', 'ADP_REGION': 'ap-guangzhou',
            'TENCENT_SECRET_ID': 'AKIDtest', 'TENCENT_SECRET_KEY': 'test'}
    credential = {'Bucket': 'bucket', 'Region': 'ap-guangzhou',
                  'Credentials': {'TmpSecretId': 'id', 'TmpSecretKey': 'key', 'Token': 'token'},
                  'UploadPath': '/corp/uin/kb/doc/x.md'}

    def test_switch_off_skips_without_network(self):
        with patch('adp.settings', return_value={**self.base, 'ADP_AUTO_UPLOAD': '0'}), \
                patch('adp.api_call') as api:
            outcome = adp.push_document(b'# x', 'a.md')
        self.assertEqual(outcome['status'], 'skipped')
        self.assertFalse(api.called)

    def test_missing_configuration_skips(self):
        empty = {key: '' for key in self.base}
        with patch('adp.settings', return_value=empty), patch('adp.api_call') as api:
            outcome = adp.push_document(b'# x', 'a.md')
        self.assertEqual(outcome['status'], 'skipped')
        self.assertFalse(api.called)

    def test_duplicate_document_is_reported_not_raised(self):
        error = ValueError('SaveDoc 返回错误：FailedOperation 450020-文档已存在，'
                           '重复文档名称:x.md，文档ID:2101283372262809408')
        with patch('adp.settings', return_value=self.base), \
                patch('adp.existing_document_names', return_value=set()), \
                patch('adp.api_call', side_effect=[self.credential, error]), \
                patch('adp.upload_to_cos', return_value=('etag', 'crc', 3)):
            outcome = adp.push_document(b'# x', 'a.md')
        self.assertEqual(outcome['status'], 'duplicate')
        self.assertEqual(outcome['doc_id'], '2101283372262809408')

    def test_success_returns_document_id(self):
        with patch('adp.settings', return_value=self.base), \
                patch('adp.existing_document_names', return_value=set()), \
                patch('adp.api_call', side_effect=[self.credential, {'DocBizId': '12345'}]), \
                patch('adp.upload_to_cos', return_value=('etag', 'crc', 3)):
            outcome = adp.push_document(b'# x', 'a.md')
        self.assertEqual(outcome['status'], 'uploaded')
        self.assertEqual(outcome['doc_id'], '12345')

    def test_skips_before_uploading_when_name_exists(self):
        with patch('adp.settings', return_value=self.base), \
                patch('adp.existing_document_names', return_value={'a.md'}), \
                patch('adp.describe_documents', return_value=[{'name':'a.md','doc_id':'existing'}]), \
                patch('adp.api_call') as api:
            outcome = adp.push_document(b'# x', 'a.md')
        self.assertEqual(outcome['status'], 'duplicate')
        self.assertEqual(outcome['doc_id'],'existing')
        self.assertFalse(api.called)           # 连上传都不做，省一次白传


class SchemaMigrationTests(unittest.TestCase):
    """v1（只有 hash、状态塞在 metadata 里）→ v2：迁移前备份、只跑一次、老资料照样能检索。"""

    LEGACY_SCHEMA = '''
        CREATE TABLE uploads (id TEXT PRIMARY KEY, filename TEXT, content BLOB, hash TEXT,
            pages TEXT, created_at TEXT, metadata TEXT);
        CREATE TABLE competitions (id TEXT PRIMARY KEY, name TEXT NOT NULL, edition TEXT NOT NULL,
            UNIQUE(name, edition));
        CREATE TABLE documents (id TEXT PRIMARY KEY, competition_id TEXT NOT NULL, filename TEXT,
            title TEXT, publisher TEXT, published_at TEXT, uploaded_at TEXT, uploader TEXT,
            role TEXT, track TEXT, content BLOB, hash TEXT, pages TEXT, metadata TEXT,
            UNIQUE(competition_id, hash), FOREIGN KEY(competition_id) REFERENCES competitions(id));
        CREATE TABLE chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
            competition_id TEXT NOT NULL, seq INTEGER NOT NULL, label TEXT, page INTEGER,
            page_end INTEGER, heading TEXT, track TEXT, ocr INTEGER DEFAULT 0, chars INTEGER,
            text TEXT NOT NULL, source TEXT, FOREIGN KEY(document_id) REFERENCES documents(id));
    '''

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'legacy'
        self.root.mkdir()
        connection = sqlite3.connect(self.root / 'huike.sqlite3')
        with connection:
            connection.executescript(self.LEGACY_SCHEMA)
            connection.execute('INSERT INTO competitions VALUES (?,?,?)', ('c1', '旧比赛', '2025'))
            connection.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                'd1', 'c1', 'old.pdf', '旧通知', '学校', '2025-06-01', '2025-06-02T00:00:00',
                '张三', 'teacher', '', b'%PDF-1.4', 'hash-old', json.dumps(['第 1 页：团队 1-5 人']),
                json.dumps({'knowledge_base': {'status': 'uploaded', 'doc_id': 'kb-7',
                                              'filename': '旧通知-2025-06-01.md', 'message': ''},
                            'extraction_method': 'filename_only'}, ensure_ascii=False)))
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_legacy_document_is_migrated_once_with_backup(self):
        migrated = Store(self.root, use_ocr=False)
        self.assertEqual(migrated.schema_version(), 2)
        backups = list(self.root.glob('huike.sqlite3.bak-*'))
        self.assertEqual(len(backups), 1)
        document = migrated.documents('c1')[0]
        self.assertEqual(document['document_status'], 'current')   # 老资料是当前有效的
        self.assertEqual(document['uploader_name'], '张三')          # uploader → uploader_name
        self.assertEqual(document['uploader_role'], 'teacher')       # role → uploader_role
        self.assertEqual(document['kb_doc_id'], 'kb-7')              # metadata → kb_* 列
        self.assertEqual(document['kb_status'], 'uploaded')
        self.assertEqual(document['kb_file_name'], '旧通知-2025-06-01.md')
        self.assertEqual(document['source_content_hash'], 'hash-old')
        self.assertNotIn('knowledge_base', document['metadata'])     # 不再留第二份
        self.assertEqual(document['metadata']['extraction_method'], 'filename_only')
        self.assertTrue(migrated.search('c1', '团队'))                # 迁移后照样能按块检索
        Store(self.root, use_ocr=False)                              # 再开一次：幂等
        self.assertEqual(len(list(self.root.glob('huike.sqlite3.bak-*'))), 1)

    def test_fresh_database_needs_no_backup(self):
        fresh = Path(self.tmp.name) / 'fresh'
        fresh.mkdir()
        Store(fresh, use_ocr=False)
        self.assertEqual(list(fresh.glob('huike.sqlite3.bak-*')), [])


class UploaderIdentityTests(unittest.TestCase):
    """归属只认服务端给的 session_owner_id；姓名和身份只用于展示。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        self.patches = [patch('ai.configured', return_value=False),
                        patch('store.clients.agent_client.configured', return_value=False),
                        patch('store.clients.knowledge_base.push',
                              return_value={'status': 'uploaded', 'doc_id': 'd', 'message': ''})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def confirm(self, **kwargs):
        upload = self.store.upload('2026-notice.pdf', pdf_bytes())
        data = {'upload_id': upload['upload_id'], 'title': 'Notice', 'name': 'Competition A',
                'edition': '2026', 'uploader': '张三', 'role': 'teacher'}
        return self.store.confirm({**data, **kwargs})

    def row(self):
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        connection.row_factory = sqlite3.Row
        row = connection.execute('SELECT uploader_name, uploader_role, uploader_session_id, '
                                 'document_status FROM documents').fetchone()
        connection.close()
        return row

    def test_session_owner_is_stored_for_undo(self):
        self.confirm(session_owner_id='a' * 32)
        row = self.row()
        self.assertEqual(row['uploader_session_id'], 'a' * 32)
        self.assertEqual(row['uploader_name'], '张三')
        self.assertEqual(row['uploader_role'], 'teacher')     # 身份只存不看：不参与任何判断
        self.assertEqual(row['document_status'], 'current')

    def test_role_is_never_used_for_permission(self):
        """老师上传的资料和学生的没有区别：没有权限分支，只有展示字段。"""
        self.confirm(role='student')
        self.assertEqual(self.row()['uploader_role'], 'student')

    def test_missing_session_owner_stays_empty(self):
        self.confirm()
        self.assertIsNone(self.row()['uploader_session_id'])


class EmptyAnswerTests(unittest.TestCase):
    """空响应不是回答：底层保持空字符串，网站走 agent_failed，报告写“未解析到正文”。"""

    def test_parser_keeps_empty_answer_empty(self):
        self.assertEqual(eval_adp.parse_events('')['answer'], '')
        self.assertEqual(eval_adp.parse_events('event: text.delta\ndata: {"Payload": {}}\n')['answer'], '')

    def test_display_helper_says_missing_instead_of_faking(self):
        self.assertEqual(eval_adp.display_answer(''), '未解析到正文')
        self.assertEqual(eval_adp.display_answer('   '), '未解析到正文')
        self.assertEqual(eval_adp.display_answer('有正文'), '有正文')

    def test_agent_ask_rejects_empty_answer(self):
        settings = {'ADP_APP_KEY': 'k', 'ADP_CHAT_URL': 'http://example.invalid'}
        empty = ({'answer': '', 'quotes': [], 'errors': []}, 'raw')
        with patch('agent.eval_adp.settings', return_value=settings), \
                patch('agent.eval_adp.call', return_value=empty):
            with self.assertRaises(ValueError):
                agent.ask('人数?')

    def test_agent_ask_still_returns_real_answer(self):
        settings = {'ADP_APP_KEY': 'k', 'ADP_CHAT_URL': 'http://example.invalid'}
        ok = ({'answer': ' 1-5 人 ', 'quotes': [], 'errors': []}, 'raw')
        with patch('agent.eval_adp.settings', return_value=settings), \
                patch('agent.eval_adp.call', return_value=ok):
            self.assertEqual(agent.ask('人数?')['answer'], '1-5 人')


class ConversationTests(unittest.TestCase):
    """The published-agent adapter still maintains isolated sessions for profile calls."""

    OWNER = 'a' * 32

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        self.calls = []
        for item in [patch('ai.configured', return_value=False),
                     patch('store.clients.agent_client.configured', return_value=True),
                     # 必须屏蔽上传：否则单测里的 confirm 会真的往知识库传文件
                     patch('store.clients.knowledge_base.push',
                           return_value={'status': 'skipped', 'doc_id': '', 'message': '测试不上传'})]:
            item.start()
            self.addCleanup(item.stop)
        self.agent = patch('store.clients.agent_client.ask', side_effect=self.reply)
        self.agent.start()
        self.addCleanup(self.agent.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def reply(self, question, competition=None, conversation_id=None):
        """模拟平台：不给会话 ID 就新建一个（与 agent.ask 的行为一致）。"""
        self.calls.append(conversation_id)
        return {'answer': '人数为 1-5 人。', 'quotes': [], 'used_reference_indices': [],
                'conversation_id': conversation_id or f'conv-{len(self.calls)}', 'elapsed': 0.5}

    def competition(self, name='Competition A'):
        upload = self.store.upload('2026-notice.pdf', pdf_bytes())
        return self.store.confirm({'upload_id': upload['upload_id'], 'title': 'Notice', 'name': name,
                                   'edition': '2026', 'uploader': '张三', 'role': 'student'})

    def ask(self, cid, question, owner_id=None):
        with self.store.db() as db:
            competition = dict(db.execute('SELECT id,name,edition FROM competitions WHERE id=?',
                                          (cid,)).fetchone())
        return self.store._agent_answer([], competition, question, owner_id)

    def test_follow_up_reuses_the_same_conversation(self):
        first = self.competition()
        self.ask(first['competition_id'], '人数?', owner_id=self.OWNER)
        second = self.ask(first['competition_id'], '那材料呢?', owner_id=self.OWNER)
        self.assertEqual(self.calls, [None, 'conv-1'])       # 第二次带着第一次的会话
        self.assertTrue(second['continued'])

    def test_other_user_does_not_share_the_conversation(self):
        first = self.competition()
        self.ask(first['competition_id'], '人数?', owner_id=self.OWNER)
        other = self.ask(first['competition_id'], '人数?', owner_id='b' * 32)
        self.assertEqual(self.calls, [None, None])            # 别人拿到的是新会话
        self.assertFalse(other['continued'])

    def test_switching_competition_does_not_share_the_conversation(self):
        first = self.competition()
        second = self.competition('Competition B')
        self.ask(first['competition_id'], '人数?', owner_id=self.OWNER)
        self.ask(second['competition_id'], '人数?', owner_id=self.OWNER)
        self.assertEqual(self.calls, [None, None])

    def test_new_session_only_resets_this_competition(self):
        first = self.competition()
        second = self.competition('Competition B')
        self.ask(first['competition_id'], '人数?', owner_id=self.OWNER)
        self.ask(second['competition_id'], '人数?', owner_id=self.OWNER)
        self.store.reset_conversation(self.OWNER, first['competition_id'])
        self.assertIsNone(self.store.conversation_id(self.OWNER, first['competition_id']))
        self.assertEqual(self.store.conversation_id(self.OWNER, second['competition_id']), 'conv-2')

    def test_expired_session_is_rebuilt_and_retried_once(self):
        first = self.competition()
        self.store.remember_conversation(self.OWNER, first['competition_id'], 'stale-session')
        replies = [agent.SessionExpired('ConversationId invalid'), self.reply('人数?')]
        with patch('store.clients.agent_client.ask', side_effect=replies) as asked:
            answer = self.ask(first['competition_id'], '人数?', owner_id=self.OWNER)
        self.assertEqual(asked.call_count, 2)                 # 只重试一次
        self.assertEqual(asked.call_args_list[0][1]['conversation_id'], 'stale-session')
        self.assertIsNone(asked.call_args_list[1][1]['conversation_id'])
        self.assertEqual(answer['mode'], 'agent')
        self.assertFalse(answer['continued'])
        self.assertNotEqual(self.store.conversation_id(self.OWNER, first['competition_id']),
                            'stale-session')

    def test_expired_session_twice_reports_failure(self):
        first = self.competition()
        self.store.remember_conversation(self.OWNER, first['competition_id'], 'stale-session')
        with patch('store.clients.agent_client.ask',
                   side_effect=agent.SessionExpired('ConversationId expired')) as asked:
            answer = self.ask(first['competition_id'], '人数?', owner_id=self.OWNER)
        self.assertEqual(asked.call_count, 2)                 # 不会无限重试
        self.assertEqual(answer['mode'], 'agent_failed')
        self.assertIsNone(answer['answer'])
        self.assertIn('会话已失效', answer['agent_error'])

    def test_without_owner_every_question_stands_alone(self):
        first = self.competition()
        self.ask(first['competition_id'], '人数?')
        self.ask(first['competition_id'], '那材料呢?')
        self.assertEqual(self.calls, [None, None])


class CitationIndexTests(unittest.TestCase):
    """角标只认平台的 quote_info.added，不从回答正文里正则猜。"""

    RAW = '\n'.join([
        'event: text.delta',
        'data: {"Payload": {"Content": "提交材料包括：[1] PPT；[2] 演示视频。"}}',
        '',
        'event: quote_info.added',
        'data: {"Payload": {"QuoteInfo": {"Index": 7, "Position": 6}}}',
        '',
        'event: reference.added',
        'data: {"Payload": {"Reference": {"Index": 7, "DocRefer": '
        '{"DocName": "a.md", "DocBizId": "2101283372262809408", "Content": "报名材料包括 PPT"}}}}',
    ])

    def test_indices_come_from_platform_events(self):
        result = eval_adp.parse_events(self.RAW)
        self.assertEqual(result['used_reference_indices'], [7])
        self.assertEqual(result['marks'], [{'index': 7, 'position': 6}])
        self.assertIn('[1] PPT', result['answer'])            # 正文里的 [1] 原样保留
        self.assertNotIn('[7]', result['answer'])             # 不再往正文里插角标
        self.assertEqual(result['quotes'][0]['index'], 7)
        self.assertEqual(result['quotes'][0]['doc_id'], '2101283372262809408')
        self.assertEqual(result['quotes'][0]['quote_text'], '报名材料包括 PPT')

    def test_reference_without_quote_info_is_not_counted_as_used(self):
        raw = self.RAW.replace('event: quote_info.added', 'event: something.else')
        result = eval_adp.parse_events(raw)
        self.assertEqual(result['used_reference_indices'], [])
        self.assertEqual(len(result['quotes']), 1)            # 引用保留，只是回答没用它


class CitationMappingTests(unittest.TestCase):
    """平台引用 → 本地资料：优先 kb_doc_id，其次文件名，再兼容旧命名；页码只在唯一命中时给。"""

    TEXT = 'Teams can have 1-5 members. Registration deadline 2026-06-30.'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        for item in [patch('ai.configured', return_value=False),
                     patch('store.clients.agent_client.configured', return_value=False),
                     patch('store.clients.knowledge_base.push',
                           return_value={'status': 'uploaded', 'doc_id': 'kb-9', 'message': ''})]:
            item.start()
            self.addCleanup(item.stop)
        upload = self.store.upload('2026-notice.pdf', pdf_bytes(self.TEXT))
        confirmed = self.store.confirm({'upload_id': upload['upload_id'], 'title': 'Notice',
                                        'name': 'Competition A', 'edition': '2026',
                                        'uploader': '张三', 'role': 'student',
                                        'published_at': '2026-06-05'})
        self.cid = confirmed['competition_id']
        self.document = self.store.documents(self.cid)[0]

    def tearDown(self):
        self.tmp.cleanup()

    def mapped(self, quote, used=None):
        return self.store.map_citations(self.cid, [quote], used or [])[0]

    def test_platform_document_id_wins(self):
        item = self.mapped({'index': 1, 'name': '不认识的.md', 'doc_id': 'kb-9', 'quote_text': ''})
        self.assertEqual(item['match'], 'kb_doc_id')
        self.assertEqual(item['document_id'], self.document['id'])
        self.assertEqual(item['title'], 'Notice')
        self.assertFalse(item['used'])

    def test_file_name_match_and_used_flag(self):
        item = self.mapped({'index': 3, 'name': self.document['kb_file_name'],
                            'doc_id': '', 'quote_text': ''}, used=[3])
        self.assertEqual(item['match'], 'kb_file_name')
        self.assertTrue(item['used'])

    def test_legacy_file_name_is_still_recognised(self):
        item = self.mapped({'index': 1, 'name': 'Notice-2026-06-05.md', 'doc_id': '',
                            'quote_text': ''})
        self.assertEqual(item['match'], 'legacy_name')
        self.assertEqual(item['document_id'], self.document['id'])

    def test_unknown_reference_is_not_guessed(self):
        item = self.mapped({'index': 1, 'name': '别的比赛的通知.md', 'doc_id': 'other', 'quote_text': ''})
        self.assertEqual(item['document_id'], '')
        self.assertEqual(item['match'], '')
        self.assertIsNone(item['page'])

    def test_unique_quote_text_gives_page(self):
        item = self.mapped({'index': 1, 'name': self.document['kb_file_name'], 'doc_id': '',
                            'quote_text': 'Teams can have 1-5 members.'})
        self.assertEqual(item['page'], 2)

    def test_short_or_missing_quote_text_gives_no_page(self):
        short = self.mapped({'index': 1, 'name': self.document['kb_file_name'], 'doc_id': '',
                             'quote_text': 'Teams'})
        missing = self.mapped({'index': 1, 'name': self.document['kb_file_name'], 'doc_id': '',
                               'quote_text': '这句话原文里没有出现过，不应该给出页码。'})
        self.assertIsNone(short['page'])
        self.assertIsNone(missing['page'])
        self.assertEqual(missing['document_id'], self.document['id'])   # 仍到文件级

    def test_empty_quotes_map_to_empty_list(self):
        self.assertEqual(self.store.map_citations(self.cid, [], [1, 2]), [])


class KnowledgeBaseLinkTests(unittest.TestCase):
    """认领：把知识库已有的文档对应回本地资料，老资料的引用才能翻回原文。"""

    TEXT = 'Teams can have 1-5 members. Registration deadline 2026-06-30.'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        for item in [patch('ai.configured', return_value=False),
                     patch('store.clients.agent_client.configured', return_value=False)]:
            item.start()
            self.addCleanup(item.stop)
        self.push = patch('store.clients.knowledge_base.push',
                          return_value={'status': 'failed', 'doc_id': '', 'message': '测试里不上传'})
        self.push_mock = self.push.start()
        self.addCleanup(self.push.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, title, name='Competition A', published_at=''):
        upload = self.store.upload('2026-notice.pdf', pdf_bytes(self.TEXT))
        return self.store.confirm({'upload_id': upload['upload_id'], 'title': title, 'name': name,
                                   'edition': '2026', 'uploader': '张三', 'role': 'student',
                                   'published_at': published_at})

    def link(self, existing):
        with patch('store.clients.knowledge_base.existing_documents', return_value=existing):
            return self.store.link_knowledge_documents()

    def test_legacy_title_is_claimed(self):
        result = self.add('2026年6月5日关于举办慧科杯的通知')
        outcome = self.link([{'doc_id': 'kb-1', 'name': '关于举办慧科杯的通知-2026-06-05.md',
                              'status': 8}])
        self.assertEqual([item['how'] for item in outcome['linked']], ['legacy_title'])
        document = self.store.documents(result['competition_id'])[0]
        self.assertEqual(document['kb_doc_id'], 'kb-1')
        self.assertEqual(document['kb_file_name'], '关于举办慧科杯的通知-2026-06-05.md')
        self.assertEqual(document['kb_status'], 'uploaded')

    def test_exact_legacy_name_is_claimed(self):
        self.add('慧科杯的通知')
        outcome = self.link([{'doc_id': 'kb-2', 'name': '慧科杯的通知-未标日期.md', 'status': 8}])
        self.assertEqual([item['how'] for item in outcome['linked']], ['legacy_name'])

    def test_ambiguous_candidates_are_left_alone(self):
        result = self.add('2026年6月5日关于举办慧科杯的通知')
        outcome = self.link([{'doc_id': 'kb-3', 'name': '关于举办慧科杯的通知-2026-06-05.md',
                              'status': 8},
                             {'doc_id': 'kb-4', 'name': '关于举办慧科杯的通知-2026-06-04.md',
                              'status': 8}])
        self.assertEqual(outcome['linked'], [])
        self.assertEqual(len(outcome['ambiguous']), 1)
        self.assertEqual(self.store.documents(result['competition_id'])[0]['kb_doc_id'], '')

    def test_entry_claimed_by_another_document_is_not_reused(self):
        with patch('store.clients.knowledge_base.push',
                   return_value={'status': 'uploaded', 'doc_id': 'kb-9', 'message': ''}):
            self.add('慧科杯的通知', name='Competition A', published_at='2026-06-05')
        other = self.add('慧科杯的通知', name='Competition B', published_at='2026-06-05')
        outcome = self.link([{'doc_id': 'kb-9', 'name': '慧科杯的通知-2026-06-05.md', 'status': 8}])
        self.assertEqual(outcome['linked'], [])
        self.assertEqual(self.store.documents(other['competition_id'])[0]['kb_doc_id'], '')

    def test_claimed_document_is_not_uploaded_again(self):
        self.add('慧科杯的通知', published_at='2026-06-05')
        self.link([{'doc_id': 'kb-5', 'name': '慧科杯的通知-2026-06-05.md', 'status': 8}])
        self.push_mock.reset_mock()
        self.assertEqual(self.store.sync_pending_documents(), [])
        self.assertEqual(self.push_mock.call_count, 0)

    def test_missing_configuration_is_reported_not_raised(self):
        with patch('store.clients.knowledge_base.existing_documents',
                   side_effect=ValueError('缺少配置：ADP_KB_ID')):
            outcome = self.store.link_knowledge_documents()
        self.assertEqual(outcome['linked'], [])
        self.assertIn('缺少配置', outcome['message'])


class ProfileAndFollowTests(unittest.TestCase):
    """画像保存与展示开关、关注比赛（初赛版：一份画像 + 主动关注）。"""

    OWNER = 'a' * 32
    OTHER = 'b' * 32

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        with connection:
            connection.execute('INSERT INTO competitions VALUES (?,?,?)', ('c1', '比赛A', '2026'))
            connection.execute('INSERT INTO competitions VALUES (?,?,?)', ('c2', '比赛B', '2026'))
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_profile_saved_and_read_back(self):
        self.assertIsNone(self.store.profile(self.OWNER))
        saved = self.store.save_profile(self.OWNER, {'introduction': '数字媒体大二', 'skills': ['剪辑']})
        self.assertEqual(saved['profile']['introduction'], '数字媒体大二')
        self.assertNotIn('visibility', saved)                          # 画像只给自己看，没有展示开关
        self.assertEqual(self.store.profile(self.OWNER)['profile']['introduction'], '数字媒体大二')

    def test_profile_requires_login_and_content(self):
        with self.assertRaises(ValueError):
            self.store.save_profile(None, {'introduction': 'x'})
        with self.assertRaises(ValueError):
            self.store.save_profile(self.OWNER, {})
        with self.assertRaises(ValueError):
            self.store.save_profile(self.OWNER, {'introduction': '   '})

    def test_profile_chat_messages_are_stored_in_order(self):
        self.assertEqual(self.store.profile_chat(self.OWNER), [])
        self.store.append_profile_message(self.OWNER, 'user', '参与过短片')
        self.store.append_profile_message(self.OWNER, 'assistant', '这件事里你本人主要负责什么？')
        messages = self.store.profile_chat(self.OWNER)
        self.assertEqual([m['role'] for m in messages], ['user', 'assistant'])
        self.assertEqual(messages[0]['text'], '参与过短片')
        self.assertEqual(messages[1]['text'], '这件事里你本人主要负责什么？')

    def test_profile_chat_is_per_owner_and_can_be_cleared(self):
        other = 'other-owner-id'
        self.store.append_profile_message(self.OWNER, 'user', '我的经历')
        self.store.append_profile_message(other, 'user', '别人的话')
        self.assertEqual([m['text'] for m in self.store.profile_chat(self.OWNER)], ['我的经历'])
        self.store.clear_profile_messages(self.OWNER)
        self.assertEqual(self.store.profile_chat(self.OWNER), [])
        self.assertEqual(len(self.store.profile_chat(other)), 1)      # 清空只清自己的
        self.assertEqual(self.store.profile_chat(None), [])           # 没有身份就没有对话

    def test_follow_and_unfollow(self):
        self.assertFalse(self.store.is_followed(self.OWNER, 'c1'))
        self.store.set_follow(self.OWNER, 'c1')
        self.assertTrue(self.store.is_followed(self.OWNER, 'c1'))
        self.assertEqual([item['id'] for item in self.store.followed_competitions(self.OWNER)], ['c1'])
        self.store.set_follow(self.OWNER, 'c2')
        self.assertEqual(len(self.store.followed_competitions(self.OWNER)), 2)
        self.assertEqual(len(self.store.followed_competitions(self.OWNER, limit=1)), 1)
        self.store.set_follow(self.OWNER, 'c1', follow=False)
        self.assertEqual([item['id'] for item in self.store.followed_competitions(self.OWNER)], ['c2'])

    def test_follow_is_per_user_and_needs_login(self):
        self.store.set_follow(self.OWNER, 'c1')
        self.assertEqual(self.store.followed_competitions(self.OTHER), [])   # 别人看不到
        with self.assertRaises(ValueError):
            self.store.set_follow(None, 'c1')
        with self.assertRaises(ValueError):
            self.store.set_follow(self.OWNER, 'missing')


class RecruitmentTests(unittest.TestCase):
    """队伍招募：发布 / 我的招募 / 结束 / 删除 / 画像只在双授权时展示。"""

    OWNER = 'c' * 32
    OTHER = 'd' * 32

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        with connection:
            connection.execute('INSERT INTO competitions VALUES (?,?,?)', ('c1', '比赛A', '2026'))
            connection.execute('INSERT INTO competitions VALUES (?,?,?)', ('c2', '比赛B', '2026'))
        connection.close()

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **kwargs):
        data = {'competition_id': 'c1', 'title': '寻找一名开发方向队友', 'idea': '校园竞赛智能助手',
                'want_role': '网页开发 / 后端接口', 'need_count': 1, 'contact': '微信 demo123'}
        return self.store.create_recruitment(self.OWNER, {**data, **kwargs})

    def test_create_and_list(self):
        created = self.create()
        self.assertTrue(created['mine'])
        self.assertEqual(created['status'], 'open')
        self.assertEqual(created['competition_name'], '比赛A')
        self.assertEqual(self.store.competition_recruitment_count('c1'), 1)
        self.assertEqual(self.store.competition_recruitment_count('c2'), 0)
        listed = self.store.recruitments(competition_id='c1', viewer_id=self.OTHER)
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0]['mine'])            # 别人看到的是"别人的招募"

    def test_create_validates_required_fields(self):
        with self.assertRaises(ValueError):
            self.store.create_recruitment(None, {})
        with self.assertRaises(ValueError):
            self.create(title='')
        with self.assertRaises(ValueError):
            self.create(contact='')
        with self.assertRaises(ValueError):
            self.create(competition_id='missing')
        with self.assertRaises(ValueError):
            self.create(need_count='abc')

    def test_recruitment_never_carries_the_private_profile(self):
        """主画像只给自己看：招募列表和详情都不再带画像字段。"""
        self.store.save_profile(self.OWNER, {'introduction': '数字媒体大二'})
        created = self.create(title='再来一条')
        detail = self.store.recruitment(created['id'])
        self.assertNotIn('profile', detail)
        self.assertNotIn('show_profile', detail)
        self.assertNotIn('profile', self.store.recruitments()[0])

    def test_only_owner_can_edit_close_or_delete(self):
        created = self.create()
        with self.assertRaises(ValueError):
            self.store.update_recruitment(self.OTHER, created['id'], {'title': '改一下'})
        with self.assertRaises(ValueError):
            self.store.delete_recruitment(self.OTHER, created['id'])
        updated = self.store.update_recruitment(self.OWNER, created['id'], {'title': '改后的标题'})
        self.assertEqual(updated['title'], '改后的标题')
        self.assertEqual(updated['status'], 'open')    # 只改标题不改状态
        with self.assertRaises(ValueError):
            self.store.update_recruitment(self.OWNER, created['id'], {})

    def test_close_hides_from_plaza_but_stays_in_mine(self):
        created = self.create()
        closed = self.store.set_recruitment_status(self.OWNER, created['id'], 'closed')
        self.assertEqual(closed['status'], 'closed')
        self.assertEqual(self.store.recruitments(competition_id='c1'), [])          # 广场看不到
        mine = self.store.recruitments(owner_id=self.OWNER, include_closed=True)    # 我的招募里还在
        self.assertEqual([item['id'] for item in mine], [created['id']])
        self.assertEqual(self.store.competition_recruitment_count('c1'), 0)
        with self.assertRaises(ValueError):
            self.store.set_recruitment_status(self.OWNER, created['id'], 'paused')

    def test_delete_removes_it(self):
        created = self.create()
        self.store.delete_recruitment(self.OWNER, created['id'])
        with self.assertRaises(ValueError):
            self.store.recruitment(created['id'])


class AccountTests(unittest.TestCase):
    """注册与登录分开：注册只创建，登录只校验；一个账号对应一个本机身份。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name, use_ocr=False)

    def tearDown(self):
        self.tmp.cleanup()

    def users(self):
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute('SELECT * FROM users')]
        finally:
            connection.close()

    def test_register_accepts_email_and_phone_and_normalizes(self):
        email = self.store.register('  Zhang@Example.COM ', 'huike123', '', owner_id='a' * 32)
        self.assertEqual((email['kind'], email['account']), ('email', 'zhang@example.com'))
        self.assertEqual(email['name'], 'zhang')                  # 没填显示名称 → 用邮箱前缀
        self.assertTrue(email['created'])
        phone = self.store.register('138-0000-0000', 'huike123', '小李', owner_id='b' * 32)
        self.assertEqual((phone['kind'], phone['account']), ('phone', '13800000000'))
        self.assertEqual(phone['name'], '小李')

    def test_registering_the_same_account_twice_is_rejected(self):
        self.store.register('zhang@example.com', 'huike123', '小张', owner_id='a' * 32)
        with self.assertRaises(ValueError) as caught:
            self.store.register('Zhang@example.com', 'other123', '另一个人', owner_id='b' * 32)
        self.assertIn('已经注册', str(caught.exception))
        self.assertEqual(len(self.users()), 1)                    # 没有建出第二个账号

    def test_login_only_checks_registered_accounts(self):
        with self.assertRaises(ValueError) as caught:
            self.store.login('nobody@example.com', 'huike123')
        self.assertIn('还没注册', str(caught.exception))
        self.assertEqual(self.users(), [])                        # 登录不会顺手建账号
        self.store.register('zhang@example.com', 'huike123', '小张', owner_id='a' * 32)
        signed_in = self.store.login('zhang@example.com', 'huike123')
        self.assertEqual((signed_in['name'], signed_in['owner_id']), ('小张', 'a' * 32))
        self.assertFalse(signed_in['created'])
        with self.assertRaises(ValueError) as wrong:
            self.store.login('zhang@example.com', 'wrong123')
        self.assertIn('不正确', str(wrong.exception))

    def test_password_is_hashed_and_never_plain(self):
        self.store.register('zhang@example.com', 'huike123', '小张', owner_id='a' * 32)
        row = self.users()[0]
        self.assertNotIn('huike123', row['password_hash'])
        self.assertNotEqual(row['password_hash'], row['salt'])

    def test_register_keeps_the_work_done_anonymously(self):
        owner = 'd' * 32
        self.store.save_profile(owner, {'major': '数字媒体'})
        account = self.store.register('13800000000', 'huike123', '', owner_id=owner)
        self.assertEqual(account['owner_id'], owner)              # 绑到当前浏览器身份，不搬走
        self.assertEqual(self.store.profile(owner)['profile']['major'], '数字媒体')
        self.assertEqual(self.store.user_by_owner(owner)['account'], '13800000000')
        self.assertIsNone(self.store.user_by_owner('e' * 32))     # 没注册过 → None

    def test_bad_account_or_short_password_is_rejected(self):
        for account in ('不是邮箱', '12345', '12600000000', ''):
            with self.assertRaises(ValueError):
                self.store.register(account, 'huike123', '', owner_id='f' * 32)
        with self.assertRaises(ValueError):
            self.store.register('zhang@example.com', '123', '', owner_id='f' * 32)
        self.assertEqual(self.users(), [])                        # 失败的注册不建账号


class AnswerHitDedupTests(unittest.TestCase):
    """回答页的本地原文片段：同一份资料只留最相关的一块，最多三份。"""

    def test_one_chunk_per_document_and_limit(self):
        hits = [{'document_id': 'a', 'text': '1'}, {'document_id': 'a', 'text': '2'},
                {'document_id': 'b', 'text': '3'}, {'document_id': 'c', 'text': '4'},
                {'document_id': 'd', 'text': '5'}]
        self.assertEqual([hit['text'] for hit in per_document(hits)], ['1', '3', '4'])
        self.assertEqual(len(per_document(hits, limit=5)), 4)

    def test_hits_without_document_id_fall_back_to_title(self):
        hits = [{'title': '同一份通知', 'text': '1'}, {'title': '同一份通知', 'text': '2'},
                {'title': '另一份', 'text': '3'}]
        self.assertEqual([hit['text'] for hit in per_document(hits)], ['1', '3'])

    def test_empty_hits_are_fine(self):
        self.assertEqual(per_document([]), [])


if __name__ == '__main__':
    unittest.main()
