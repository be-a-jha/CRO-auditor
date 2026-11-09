"""Regression tests for stalled viewport operations and retries."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.async_api import TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cro import capture


class CaptureTimeoutTest(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_views_are_cancelled(self):
        closed = set()

        async def stall(browser, url, cfg, outdir, name, want_dom, responses, timeout_ms):
            try:
                await asyncio.Future()
            finally:
                closed.add(name)

        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "_load_view", stall), \
                patch.object(capture, "CAPTURE_OVERHEAD_SECONDS", 0):
            with self.assertRaisesRegex(capture.CaptureError, "Browser capture timed out"):
                await asyncio.wait_for(capture.capture_url(None, "https://example.com", Path(tmp), 50), timeout=1)
        self.assertEqual(closed, {"desktop", "mobile"})

    async def test_retry_waits_for_first_attempt_to_close(self):
        mobile_closed = asyncio.Event()

        async def load(browser, url, cfg, outdir, name, want_dom, responses, timeout_ms):
            if timeout_ms == 100:
                if name == "desktop":
                    raise PlaywrightTimeout("Navigation timed out")
                try:
                    await asyncio.Future()
                finally:
                    await asyncio.sleep(0)
                    mobile_closed.set()
            self.assertTrue(mobile_closed.is_set())
            return name, url

        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "_load_view", load):
            result = await asyncio.wait_for(capture.capture_url(None, "https://example.com", Path(tmp), 100), timeout=1)
        self.assertEqual((result.desktop, result.mobile), ("desktop", "mobile"))


if __name__ == "__main__":
    unittest.main()
