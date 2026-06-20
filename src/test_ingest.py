import os
import sys
import datetime
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

# Add src root to sys.path to allow imports
SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

# Prevent connection errors by mocking motor before importing main_api
with patch("motor.motor_asyncio.AsyncIOMotorClient"):
    from fastapi.testclient import TestClient
    from main_api import app
    import core.config
    from core.jwt_native import encode_jwt

class TestIngestConstraints(unittest.TestCase):
    def setUp(self):
        # Configure local test JWT settings
        self.secret = "TEST_SECRET_KEY_FOR_UPLOADS"
        core.config.ADMIN_JWT_SECRET = self.secret
        
        payload = {
            "iss": "obd-cortex-admin",
            "aud": "obd-cortex-admin-api",
            "exp": int((datetime.datetime.utcnow() + datetime.timedelta(hours=1)).timestamp()),
            "user": "test_verifier"
        }
        self.token = encode_jwt(payload, self.secret)
        self.headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

    @patch("routes.ingest.col_jobs")
    def test_upload_under_10mb(self, mock_col_jobs):
        # Mock database insertion to run test without local mongo dependency
        mock_col_jobs.insert_one = AsyncMock()
        
        # 5MB file (5 * 1024 * 1024 bytes)
        content = b"a" * (5 * 1024 * 1024)
        files = {"file": ("test.txt", content, "text/plain")}
        
        response = self.client.post("/api/ingest", files=files, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        self.assertTrue(mock_col_jobs.insert_one.called)

    def test_upload_over_10mb(self):
        # 10MB + 1 byte file (10 * 1024 * 1024 + 1 bytes)
        content = b"a" * (10 * 1024 * 1024 + 1)
        files = {"file": ("large_test.txt", content, "text/plain")}
        
        response = self.client.post("/api/ingest", files=files, headers=self.headers)
        self.assertEqual(response.status_code, 413)
        self.assertIn("File too large", response.json()["detail"])

if __name__ == "__main__":
    unittest.main()
