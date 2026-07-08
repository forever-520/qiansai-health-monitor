import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qt.qt import camera_client


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class CameraClientTests(unittest.TestCase):
    def test_request_image_saves_received_jpeg(self):
        jpeg = b"\xff\xd8\xff\xdbtest-jpeg"
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "frame.jpg"
            with mock.patch("qt.qt.camera_client.urllib.request.urlopen", return_value=FakeResponse(jpeg)):
                saved = camera_client.request_image("127.0.0.1", 9001, out)
            self.assertEqual(saved.read_bytes(), jpeg)
            self.assertEqual(saved, out)


if __name__ == "__main__":
    unittest.main()
