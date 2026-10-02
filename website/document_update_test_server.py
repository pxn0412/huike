"""Offline browser fixture for update confirmation. Never writes to cloud or real data."""
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from http.server import ThreadingHTTPServer


def comparison(_system,payload,**_kwargs):
    new='\n'.join(p['text'] for p in payload['new_pages'])
    changes=[]
    for old_quote,new_quote,subject,stage in [
        ('初赛截止：8月22日。','初赛截止：9月24日。','初赛截止时间','初赛提交'),
        ('每队1至5人。','每队2至6人。','队伍人数','组队')]:
        source=next((s for s in payload['old_sources'] if old_quote in s['text']),None)
        if source and new_quote in new:
            changes.append({'old_document_id':source['document_id'],'old_chunk':source['label'],
                'old_quote':old_quote,'new_quote':new_quote,'subject':subject,'scope':'全比赛',
                'stage':stage,'reason':'测试用逐字原文对比'})
    return {'changes':changes}


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='huike-update-browser-') as root:
        os.environ['HUIKE_DATA_DIR']=root
        import server
        server.STORE.use_ocr=False
        with patch('ai.configured',return_value=False), \
             patch('clients.knowledge_base.configured',return_value=False), \
             patch('clients.knowledge_base.push',return_value={'status':'skipped','message':'离线测试'}), \
             patch('document_updates.ai',SimpleNamespace(configured=lambda:True,complete=comparison)), \
             patch('adp.api_call',side_effect=AssertionError('Tests must not call cloud APIs')):
            with server.STORE.db() as db:
                db.execute('INSERT INTO competitions VALUES (?,?,?)',('c1','演示比赛','2026'))
            upload=server.STORE.upload('old.txt','初赛截止：8月22日。每队1至5人。需提交PPT。'.encode())
            server.STORE.confirm({'upload_id':upload['upload_id'],'competition_id':'c1','title':'初赛原通知','uploader':'测试者'})
            ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('HUIKE_PORT','8779'))),server.Handler).serve_forever()
