import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from store import Store
import adp
import ai


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
        self.agent_patch = patch('store.agent.configured', return_value=False)
        self.agent_patch.start()
        # 测试绝不联网：把“自动进知识库”替换成假的（真上传只在真人使用时发生）。
        self.kb_patch = patch('store.adp.push_document',
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
        return self.store.confirm({**data, **kwargs})

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

    def test_no_cross_competition_results(self):
        first = self.confirm(self.upload())
        other = self.confirm(self.upload(pdf_bytes('This contest is about astronomy.')), name='Competition B')
        self.assertEqual(self.store.search(other['competition_id'], 'members'), [])
        self.assertTrue(self.store.search(first['competition_id'], 'members'))
        with self.assertRaises(ValueError):
            self.store.search(None, 'members')

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

    def test_knowledge_base_filename_carries_publish_date(self):
        self.confirm(self.upload(), published_at='2026-06-05')
        _, filename = self.kb_push.call_args[0][:2]
        self.assertTrue(filename.endswith('-2026-06-05.md'), filename)

    def test_knowledge_base_status_is_stored_on_document(self):
        result = self.confirm(self.upload())
        document = self.store.documents(result['competition_id'])[0]
        status = document['metadata']['knowledge_base']
        self.assertEqual(status['status'], 'uploaded')
        self.assertEqual(status['chunk_count'], result['chunk_count'])

    def test_knowledge_base_failure_keeps_local_document(self):
        with patch('store.adp.push_document',
                   return_value={'status': 'failed', 'doc_id': '', 'message': '网络不可达'}):
            result = self.confirm(self.upload())
        self.assertEqual(result['knowledge_base']['status'], 'failed')
        self.assertEqual(len(self.store.documents(result['competition_id'])), 1)
        self.assertTrue(self.store.search(result['competition_id'], 'members'))

    def test_answer_uses_published_agent(self):
        result = self.confirm(self.upload())
        reply = {'answer': '人数为 1-5 人。',
                 'quotes': [{'index': 1, 'name': 'a.md', 'url': 'https://example.com'}],
                 'elapsed': 1.2}
        with patch('store.agent.configured', return_value=True), \
                patch('store.agent.ask', return_value=reply) as asked:
            answer = self.store.answer(result['competition_id'], 'members')
        self.assertEqual(answer['mode'], 'agent')
        self.assertEqual(answer['answer'], '人数为 1-5 人。')
        self.assertEqual(answer['quotes'][0]['name'], 'a.md')
        self.assertTrue(answer['hits'])            # 本地原文片段仍然照给
        self.assertEqual(asked.call_args[0][0], 'members')
        # 必须带上"当前比赛"，否则智能体会拿别的比赛资料回答。
        self.assertIn('Competition A', asked.call_args[1]['competition'])

    def test_answer_reports_agent_failure_without_faking(self):
        result = self.confirm(self.upload())
        with patch('store.agent.configured', return_value=True), \
                patch('store.agent.ask', side_effect=ValueError('网络不可达')):
            answer = self.store.answer(result['competition_id'], 'members')
        self.assertEqual(answer['mode'], 'agent_failed')
        self.assertIsNone(answer['answer'])
        self.assertIn('网络不可达', answer['agent_error'])
        self.assertTrue(answer['hits'])

    def test_pending_documents_are_pushed_without_clicking(self):
        result = self.confirm(self.upload())
        # 造一份"功能上线前入库"的资料：把它已记录的知识库状态清掉。
        connection = sqlite3.connect(Path(self.tmp.name) / 'huike.sqlite3')
        with connection:
            connection.execute('UPDATE documents SET metadata=?', (json.dumps({}),))
        connection.close()
        self.kb_push.reset_mock()
        pending = self.store.sync_pending_documents()
        self.assertEqual(pending, [result['document_id']])
        self.assertEqual(self.kb_push.call_count, 1)
        document = self.store.documents(result['competition_id'])[0]
        self.assertEqual(document['metadata']['knowledge_base']['status'], 'uploaded')

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
                patch('adp.api_call') as api:
            outcome = adp.push_document(b'# x', 'a.md')
        self.assertEqual(outcome['status'], 'duplicate')
        self.assertFalse(api.called)           # 连上传都不做，省一次白传


if __name__ == '__main__':
    unittest.main()
