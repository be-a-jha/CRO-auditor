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

        async def stall(browser, url, cfg, outdir, name, want_dom, responses, timeout_ms, reduced):
            try:
                await asyncio.Future()
            finally:
                closed.add(name)

        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "_load_view", stall), \
                patch.object(capture, "CAPTURE_OVERHEAD_SECONDS", 0):
            with self.assertRaisesRegex(capture.CaptureError, "browser capture timed out"):
                await asyncio.wait_for(capture.capture_url(None, "https://example.com", Path(tmp), 50), timeout=1)
        self.assertEqual(closed, {"desktop", "mobile"})

    async def test_retry_waits_for_first_attempt_to_close(self):
        mobile_closed = asyncio.Event()

        async def load(browser, url, cfg, outdir, name, want_dom, responses, timeout_ms, reduced):
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

    async def test_shared_slot_serializes_views_across_pages(self):
        active = 0

        async def load(browser, url, cfg, outdir, name, want_dom, responses, timeout_ms, reduced):
            nonlocal active
            active += 1
            try:
                self.assertEqual(active, 1)
                await asyncio.sleep(0.01)
                return name, url
            finally:
                active -= 1

        slots = asyncio.Semaphore(1)
        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "_load_view", load):
            first, second = await asyncio.gather(
                capture.capture_url(None, "https://example.com/first", Path(tmp) / "first", slots=slots),
                capture.capture_url(None, "https://example.com/second", Path(tmp) / "second", slots=slots),
            )
        self.assertEqual((first.desktop, first.mobile, second.desktop, second.mobile),
                         ("desktop", "mobile", "desktop", "mobile"))

    async def test_browser_crash_retries_with_reduced_resources(self):
        async def load(browser, url, cfg, outdir, name, want_dom, responses, timeout_ms, reduced):
            if not reduced:
                raise capture.ViewCrash(f"{name} crashed")
            return name, url

        with tempfile.TemporaryDirectory() as tmp, patch.object(capture, "_load_view", load):
            result = await capture.capture_url(None, "https://example.com", Path(tmp), slots=asyncio.Semaphore(1))
        self.assertTrue(result.reduced)
        self.assertEqual((result.desktop, result.mobile), ("desktop", "mobile"))


if __name__ == "__main__":
    unittest.main()
