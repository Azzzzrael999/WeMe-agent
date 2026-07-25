from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from server import create_server


class HealthEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server(host="127.0.0.1", port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_returns_up(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/health", timeout=2) as response:
            payload = json.load(response)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"status": "UP", "service": "video-provider-mock"})

    def test_business_routes_are_not_implemented_on_day_one(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{self.base_url}/mock/v1/video-meetings", timeout=2)

        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
