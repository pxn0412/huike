"""Owner-scoped local background uploads; never confirms or publishes a document."""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

_pool = ThreadPoolExecutor(max_workers=2)
_lock = threading.Lock()
_jobs = {}


def submit(store, owner, filename, content):
    with _lock:
        for key in list(_jobs):
            if _jobs[key]['status'] != 'processing' and time.time() - _jobs[key]['updated_at'] > 3600:
                del _jobs[key]
        if sum(j['status'] == 'processing' for j in _jobs.values()) >= 4:
            raise ValueError('当前处理任务较多，请稍后重试。')
        if any(j['owner'] == owner and j['status'] == 'processing' for j in _jobs.values()):
            raise ValueError('你已有资料正在识别，请等待完成后再上传。')
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {'owner': owner, 'status': 'processing', 'stage': 'queued', 'updated_at': time.time()}

    def update(values):
        with _lock:
            _jobs[job_id].update(values, updated_at=time.time())

    def run():
        try:
            update({'stage': 'reading'})
            result = store.upload(filename, content, progress=update)
            update({'status': 'completed', 'result': result})
        except ValueError as exc:
            update({'status': 'failed', 'error': str(exc)})
        except Exception:
            update({'status': 'failed', 'error': '资料处理失败，请重试；尚未加入比赛。'})
    _pool.submit(run)
    return job_id


def get(owner, job_id):
    with _lock:
        job = _jobs.get(job_id)
        if not job or job['owner'] != owner:
            return None
        return {k: v for k, v in job.items() if k != 'owner'}
