import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as service


class DummyExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))

    def shutdown(self, **_):
        pass


class DownloadApiTest(unittest.TestCase):
    def setUp(self):
        service.jobs.clear()
        service.job_rate.clear()
        service.info_rate.clear()
        self.executor = DummyExecutor()
        self.executor_patch = patch.object(service, "executor", self.executor)
        self.executor_patch.start()
        self.client = TestClient(service.app)

    def tearDown(self):
        self.client.close()
        self.executor_patch.stop()

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_formats_and_cors(self):
        metadata = {
            "title": "테스트 영상",
            "duration": 10,
            "thumbnail": "",
            "qualities": [1080, 720, 360],
        }
        with patch.object(service, "extract_info", return_value=metadata):
            response = self.client.get(
                "/api/formats?videoId=aqz-KE-bpKQ",
                headers={"Origin": service.APP_ORIGIN},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["qualities"], [1080, 720, 360])
        self.assertEqual(response.headers["access-control-allow-origin"], service.APP_ORIGIN)

    def test_job_validation_and_queue(self):
        invalid = self.client.post(
            "/api/jobs",
            json={"videoId": "bad!video00", "mode": "video", "quality": 720, "audioFormat": "mp3"},
        )
        self.assertEqual(invalid.status_code, 400)

        response = self.client.post(
            "/api/jobs",
            json={"videoId": "aqz-KE-bpKQ", "mode": "audio", "quality": 720, "audioFormat": "m4a"},
        )
        self.assertEqual(response.status_code, 202)
        job = response.json()
        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(self.executor.calls), 1)

        status = self.client.get(f"/api/jobs/{job['id']}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "queued")


if __name__ == "__main__":
    unittest.main()
