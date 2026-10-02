import json
import tempfile
import unittest
from unittest.mock import patch

import document_updates
import adp
from store import Store


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name, use_ocr=False)
        for target, value in [('ai.configured', False), ('clients.knowledge_base.configured', False)]:
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch('clients.knowledge_base.push', return_value={'status': 'skipped', 'message': 'offline'})
        p.start(); self.addCleanup(p.stop)
        self.old = self.save('初赛截止：8月22日。每队1至5人。需提交PPT。', name='演示赛')
        self.upload = self.store.upload('new.txt', '初赛截止：9月24日。'.encode())
        self.cid = self.old['competition_id']

    def save(self, text, **kwargs):
        upload = self.store.upload('old.txt', text.encode())
        return self.store.confirm({'upload_id': upload['upload_id'], 'title': '旧通知', 'name': '演示赛',
                                  'edition': '2026', 'uploader': '测试者', **kwargs})

    def comparison(self):
        with self.store.db() as db:
            chunk = db.execute('select label from chunks where document_id=?', (self.old['document_id'],)).fetchone()[0]
        result = {'changes': [{'subject': '初赛截止时间', 'scope': '全比赛', 'stage': '初赛提交',
            'old_document_id': self.old['document_id'], 'old_chunk': chunk, 'old_quote': '初赛截止：8月22日。',
            'new_quote': '初赛截止：9月24日。', 'reason': '同一环节截止时间不同'}]}
        with patch('document_updates.ai.configured', return_value=True), patch('document_updates.ai.complete', return_value=result):
            return document_updates.compare(self.store, self.upload['upload_id'], self.cid)

    def test_partial_update_preserves_other_rules_and_original(self):
        comparison = self.comparison()
        new = self.store.confirm({'upload_id': self.upload['upload_id'], 'competition_id': self.cid,
            'title': '新通知', 'uploader': '测试者', 'session_owner_id': 'owner', 'update_mode': 'partial',
            'comparison_id': comparison['comparison_id'], 'selected_changes': [comparison['changes'][0]['id']]})
        document_updates.process(self.store, new['update_id'])
        hits = self.store.search(self.cid, '初赛截止人数材料')
        text = '\n'.join(h['text'] for h in hits)
        self.assertNotIn('8月22日', text)
        self.assertIn('9月24日', text)
        self.assertIn('1至5人', text)
        self.assertIn('PPT', text)
        original = self.store.preview('documents', self.old['document_id'])
        self.assertIn('8月22日', original['pages'][0])

    def test_unknown_quote_is_not_a_selectable_change(self):
        with patch('document_updates.ai.configured', return_value=True), patch('document_updates.ai.complete',
                return_value={'changes': [{'old_document_id': self.old['document_id'], 'old_chunk': 'N00',
                    'old_quote': 'invented', 'new_quote': '初赛截止：9月24日。'}]}):
            comparison = document_updates.compare(self.store, self.upload['upload_id'], self.cid)
        self.assertEqual(comparison['changes'], [])

    def test_whole_replacement_hides_old_rules_but_keeps_file(self):
        new = self.store.confirm({'upload_id': self.upload['upload_id'], 'competition_id': self.cid,
            'title': '完整新版', 'uploader': '测试者', 'update_mode': 'replace',
            'target_document_ids': [self.old['document_id']]})
        document_updates.process(self.store, new['update_id'])
        self.assertEqual(self.store.search(self.cid, '人数'), [])
        self.assertIsNotNone(self.store.file('documents', self.old['document_id']))

    def test_cannot_replace_document_from_another_competition(self):
        other = self.save('其他比赛每队2人。', name='另一场赛')
        with self.assertRaises(ValueError):
            self.store.confirm({'upload_id': self.upload['upload_id'], 'competition_id': self.cid,
                'title': '新通知', 'uploader': '测试者', 'update_mode': 'replace',
                'target_document_ids': [other['document_id']]})

    def partial(self):
        comparison = self.comparison()
        return self.store.confirm({'upload_id':self.upload['upload_id'],'competition_id':self.cid,
            'title':'补充通知','uploader':'测试者','session_owner_id':'owner','update_mode':'partial',
            'comparison_id':comparison['comparison_id'],'selected_changes':[comparison['changes'][0]['id']]})

    def test_pending_update_keeps_old_evidence(self):
        new = self.partial()
        with self.store.db() as db:
            text = '\n'.join(r['text'] for r in document_updates.effective(db,self.cid))
        self.assertIn('8月22日',text); self.assertNotIn('9月24日',text)
        self.assertEqual(document_updates.get(self.store,new['update_id'])['status'],'pending')

    def test_stale_comparison_cannot_overwrite_changed_sources(self):
        comparison = self.comparison()
        self.save('比赛新增场地规定。',competition_id=self.cid)
        with self.assertRaisesRegex(ValueError,'已经变化'):
            self.store.confirm({'upload_id':self.upload['upload_id'],'competition_id':self.cid,
                'title':'补充通知','uploader':'测试者','update_mode':'partial',
                'comparison_id':comparison['comparison_id'],'selected_changes':[comparison['changes'][0]['id']]})
        self.assertIsNotNone(self.store.file('uploads',self.upload['upload_id']))

    def test_unselected_changed_fact_does_not_override_old_rule(self):
        self.upload = self.store.upload('two.txt','初赛截止：9月24日。每队2至6人。'.encode())
        comparison = self.comparison()
        with self.store.db() as db:
            saved = json.loads(db.execute('SELECT payload FROM upload_comparisons WHERE id=?', (comparison['comparison_id'],)).fetchone()[0])
            saved['changes'].append({**saved['changes'][0], 'id':'other-change','subject':'人数',
                'old_quote':'每队1至5人。','new_quote':'每队2至6人。'})
            db.execute('UPDATE upload_comparisons SET payload=? WHERE id=?',(json.dumps(saved),comparison['comparison_id']))
        new = self.store.confirm({'upload_id':self.upload['upload_id'],'competition_id':self.cid,
            'title':'补充通知','uploader':'测试者','update_mode':'partial','comparison_id':comparison['comparison_id'],
            'selected_changes':[comparison['changes'][0]['id']]})
        document_updates.process(self.store,new['update_id'])
        with self.store.db() as db:
            text='\n'.join(r['text'] for r in document_updates.effective(db,self.cid))
        self.assertIn('9月24日',text); self.assertIn('1至5人',text)
        self.assertNotIn('8月22日',text); self.assertNotIn('2至6人',text)

    def test_uncertain_keeps_both_original_rules(self):
        new = self.store.confirm({'upload_id':self.upload['upload_id'],'competition_id':self.cid,
            'title':'未确认变更','uploader':'测试者','update_mode':'uncertain'})
        document_updates.process(self.store,new['update_id'])
        with self.store.db() as db:
            text='\n'.join(r['text'] for r in document_updates.effective(db,self.cid))
        self.assertIn('8月22日',text); self.assertIn('9月24日',text)

    def test_cloud_failure_keeps_local_old_sources_and_is_retryable(self):
        new = self.partial()
        with patch('document_updates.sync_projection',side_effect=ValueError('学习失败')):
            document_updates.process(self.store,new['update_id'])
        self.assertEqual(document_updates.get(self.store,new['update_id'])['status'],'failed')
        self.assertIn('8月22日','\n'.join(h['text'] for h in self.store.search(self.cid,'截止')))
        document_updates.process(self.store,new['update_id'])
        self.assertEqual(document_updates.get(self.store,new['update_id'])['status'],'ready')

    def test_switch_rollback_restores_exact_domain_and_skips_disabled_files(self):
        new = self.partial()
        domains = {'old':'2','already-disabled':'1'}
        def state(doc_id): return {'domain':int(domains[doc_id])}
        calls = []
        def set_domain(doc_id,domain):
            calls.append((doc_id,domain))
            if doc_id=='old' and domain==1: raise ValueError('模拟停用失败')
        with self.store.db() as db:
            op=dict(db.execute('SELECT * FROM document_updates WHERE id=?',(new['update_id'],)).fetchone())
            sources=document_updates.effective(db,self.cid,op)
        with patch('clients.knowledge_base.configured',return_value=True), \
             patch('clients.knowledge_base.stage',return_value={'status':'uploaded','doc_id':'staged'}), \
             patch('clients.knowledge_base.state',side_effect=state), \
             patch('clients.knowledge_base.wait_ready'), patch('clients.knowledge_base.set_domain',side_effect=set_domain):
            with self.assertRaisesRegex(ValueError,'已还原'):
                document_updates.sync_projection(self.store,op,sources,set(domains))
        self.assertIn(('old',2),calls)
        self.assertNotIn(('already-disabled',4),calls)
        self.assertIn(('staged',1),calls)

    def test_status_adapter_reads_nested_effective_domain(self):
        response={'Summary':{'Lifecycle':{'Status':8,'StatusDesc':'导入完成'},'KnowledgeScope':{'EffectiveDomain':1}}}
        with patch('adp.api_call',return_value=response):
            self.assertEqual(adp.wait_document_ready('doc',domain=1,timeout=0)['domain'],1)

    def test_overlap_fragment_does_not_leak_removed_rule(self):
        with self.store.db() as db:
            row=dict(db.execute('SELECT * FROM chunks WHERE document_id=?',(self.old['document_id'],)).fetchone())
            row.update(id=row['id']+'-overlap',label='N99',seq=99,text='8月22日。每队1至5人。需提交PPT。')
            db.execute('INSERT INTO chunks ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
        new=self.partial()
        document_updates.process(self.store,new['update_id'])
        with self.store.db() as db:
            text='\n'.join(r['text'] for r in document_updates.effective(db,self.cid))
        self.assertNotIn('8月22日',text)
        self.assertIn('PPT',text)


if __name__ == '__main__':
    unittest.main()
