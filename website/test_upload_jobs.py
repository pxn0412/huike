import threading
import unittest
from unittest.mock import Mock

import upload_jobs


class JobTests(unittest.TestCase):
    def test_job_reports_progress_and_only_owner_can_read_result(self):
        entered = threading.Event(); release = threading.Event(); completed = threading.Event()
        def upload(filename, content, progress):
            progress({'stage': 'recognizing', 'completed': 8, 'total': 23})
            entered.set()
            release.wait(5)
            return {'upload_id': 'temporary', 'has_text': True}
        store = Mock(); store.upload.side_effect = upload
        job_id = upload_jobs.submit(store, 'owner-test', 'x.pdf', b'fixture')
        try:
            self.assertTrue(entered.wait(2))
            self.assertIsNone(upload_jobs.get('other-owner', job_id))
            self.assertEqual(upload_jobs.get('owner-test', job_id)['completed'], 8)
            with self.assertRaises(ValueError):
                upload_jobs.submit(store, 'owner-test', 'second.pdf', b'fixture')
        finally:
            release.set()
        # Poll with a finite deadline; no cloud services are called.
        for _ in range(100):
            job = upload_jobs.get('owner-test', job_id)
            if job['status'] == 'completed':
                break
            completed.wait(.01)
        self.assertEqual(job['result']['upload_id'], 'temporary')
        self.assertNotIn('owner', job)
        store.confirm.assert_not_called()


if __name__ == '__main__':
    unittest.main()
