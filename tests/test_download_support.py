"""Network interruption must not publish partial files or hammer a limited host."""

import io
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration/updater"))
import support


class Response(io.BytesIO):
    def __init__(self, data=b"complete", headers=None):
        super().__init__(data)
        self.headers = headers or {"Content-Length": str(len(data))}


class Downloads(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "payload"
        self.target.write_bytes(b"old complete bytes")
        self.env = patch.dict(os.environ, {"DPE_PROFILE_DIR": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.url = "https://github.com/example/project/releases/download/v1/asset"
        self.opener = Mock()
        self.patch = patch.object(support.urllib.request, "build_opener", return_value=self.opener)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.sleep = patch.object(support.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_interruption_retries_once_and_publishes_only_complete_file(self):
        self.opener.open.side_effect = [Response(b"part", {"Content-Length": "8"}), Response()]
        support.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_exhausted_transport_retries_preserve_old_file(self):
        self.opener.open.side_effect = urllib.error.URLError("connection reset")
        with self.assertRaises(urllib.error.URLError):
            support.download(self.url, self.target)
        self.assertEqual(self.opener.open.call_count, 2)
        self.assertEqual(self.target.read_bytes(), b"old complete bytes")

    def test_invalid_length_and_oversize_are_not_retried(self):
        for headers in (
            {"Content-Length": "100"},
            {"Content-Length": "-1"},
            {"Content-Length": "bad"},
        ):
            self.opener.open.reset_mock()
            self.opener.open.return_value = Response(headers=headers)
            with self.assertRaises(ValueError):
                support.download(self.url, self.target, limit=8)
            self.assertEqual(self.opener.open.call_count, 1)
            self.assertEqual(self.target.read_bytes(), b"old complete bytes")

    def test_failed_publish_preserves_old_file_and_removes_temporary(self):
        self.opener.open.return_value = Response()
        with patch.object(Path, "replace", side_effect=PermissionError("read only")):
            with self.assertRaises(PermissionError):
                support.download(self.url, self.target)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(list(self.root.iterdir()), [self.target])
        self.assertEqual(self.target.read_bytes(), b"old complete bytes")

    def test_cooldown_survives_separate_calls_and_expires(self):
        self.opener.open.side_effect = urllib.error.HTTPError(
            self.url,
            403,
            "limited",
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4600"},
            None,
        )
        with patch.object(support.time, "time", return_value=1000):
            for _ in range(2):
                with self.assertRaises(support.RateLimited) as caught:
                    support.download(self.url, self.target)
                self.assertEqual(caught.exception.until, 4600)
        self.assertEqual(self.opener.open.call_count, 1)
        self.opener.open.side_effect = None
        self.opener.open.return_value = Response()
        with patch.object(support.time, "time", return_value=4601):
            support.download(self.url, self.target)
        self.assertEqual(self.opener.open.call_count, 2)

    def test_retry_after_and_busy_server_never_immediately_retry(self):
        for code in (429, 503):
            with self.subTest(code=code), patch.object(support.time, "time", return_value=1000):
                support.cooldown_file(self.url).unlink(missing_ok=True)
                self.opener.open.reset_mock()
                self.opener.open.side_effect = urllib.error.HTTPError(
                    self.url, code, "busy", {"Retry-After": "120"}, None
                )
                with self.assertRaises(support.RateLimited) as caught:
                    support.download(self.url, self.target)
                self.assertEqual(caught.exception.until, 1120)
                self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(support.retry_time({"Retry-After": "invalid"}) > 0, True)

    def test_permanent_http_failure_is_not_retried_or_mislabeled(self):
        for code in (403, 404):
            self.opener.open.reset_mock()
            self.opener.open.side_effect = urllib.error.HTTPError(
                self.url, code, "denied", {}, None
            )
            with self.assertRaises(urllib.error.HTTPError):
                support.download(self.url, self.target)
            self.assertEqual(self.opener.open.call_count, 1)
            self.assertFalse(support.cooldown_file(self.url).exists())

    def test_different_host_is_not_blocked_by_github_cooldown(self):
        path = support.cooldown_file(self.url)
        path.parent.mkdir(parents=True)
        support.write_json(path, {"until": support.time.time() + 3600})
        self.opener.open.return_value = Response()
        support.download("https://discord.com/asset", self.target)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_retry_after_http_date_and_reset_choose_later_deadline(self):
        with patch.object(support.time, "time", return_value=1000):
            self.assertEqual(
                support.retry_time({"Retry-After": "Thu, 01 Jan 1970 00:20:00 GMT"}), 1200
            )
            self.assertEqual(
                support.retry_time(
                    {
                        "Retry-After": "120",
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": "1500",
                    }
                ),
                1500,
            )

    def test_stream_read_error_removes_partial_and_recovers(self):
        response = Response()
        response.read = Mock(side_effect=[b"partial", ConnectionResetError("reset")])
        self.opener.open.side_effect = [response, Response()]
        support.download(self.url, self.target)
        self.assertEqual(self.target.read_bytes(), b"complete")
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_unknown_length_is_still_bounded(self):
        self.opener.open.return_value = Response(b"too much data", {"Content-Length": "0"})
        with self.assertRaisesRegex(ValueError, "size limit"):
            support.download(self.url, self.target, limit=8)
        self.assertEqual(self.opener.open.call_count, 1)
        self.assertEqual(self.target.read_bytes(), b"old complete bytes")
        self.assertEqual(list(self.root.iterdir()), [self.target])
