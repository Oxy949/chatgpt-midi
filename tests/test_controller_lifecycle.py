import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, call

from midivoice.config import Settings
from midivoice.controller import Controller
from midivoice.errors import BrowserError, BrowserVerificationRequired
from midivoice.state import StateStore


class FakeBrowser:
    def __init__(self):
        self.running = True
        self.memory_kb = None
        self.inspect = AsyncMock(return_value={"voice_button": True})
        self.click = AsyncMock(side_effect=self._click)
        self.start = AsyncMock(side_effect=self._start)
        self.stop = AsyncMock(side_effect=self._stop)
        self.navigate_home = AsyncMock()

    def _start(self, url=None):
        self.running = True

    def _click(self, ending=False):
        self.inspect.return_value = {"end_button": not ending}
        return True

    def _stop(self, force=False):
        self.running = False


class ControllerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_keeps_manual_verification_open(self):
        browser = FakeBrowser()
        browser.navigate_context = AsyncMock(
            side_effect=BrowserVerificationRequired("Use VNC")
        )
        controller = Controller(browser)
        with self.assertRaises(BrowserVerificationRequired):
            await controller.handle("mode-work")
        self.assertEqual(controller.mode, "verification")
        self.assertTrue(browser.running)
        browser.stop.assert_not_awaited()

    async def test_close_during_acceptance_cannot_reopen_the_browser(self):
        browser = FakeBrowser()
        browser.running = False
        controller = Controller(browser)
        accepted = asyncio.Event()
        resume = asyncio.Event()

        async def progress(event):
            if event.get("stage") == "accepted":
                accepted.set()
                await resume.wait()

        opening = asyncio.create_task(controller.handle("open", progress=progress))
        await accepted.wait()
        await controller.handle("close")
        resume.set()
        self.assertFalse((await opening)["ok"])
        browser.start.assert_not_awaited()

    async def test_navigation_preserves_ready_mode_without_starting_voice(self):
        browser = FakeBrowser()
        browser.navigate_context = AsyncMock(
            return_value={"workspace_mode": "work", "project": "Home"}
        )
        controller = Controller(browser)
        result = await controller.handle("project", argument="Home")
        browser.navigate_context.assert_awaited_once_with("project", "Home")
        browser.click.assert_not_awaited()
        self.assertEqual(result["mode"], "work")

    async def test_close_cancels_project_navigation(self):
        browser = FakeBrowser()
        entered = asyncio.Event()

        async def navigate(*_):
            entered.set()
            await asyncio.Event().wait()

        browser.navigate_context = navigate
        controller = Controller(browser)
        opening = asyncio.create_task(controller.handle("project", argument="Home"))
        await entered.wait()
        await asyncio.wait_for(controller.handle("close"), 0.5)
        self.assertFalse((await opening)["ok"])
        self.assertFalse(browser.running)

    async def test_connecting_does_not_repeatedly_toggle_composer_voice(self):
        from itertools import count

        browser = FakeBrowser()
        browser.click = AsyncMock(return_value=True)
        browser.inspect.side_effect = [{"voice_button": True}] * 4 + [
            {"end_button": True}
        ]
        controller = Controller(
            browser, clock=count(step=4).__next__, sleep=AsyncMock()
        )
        await controller.handle("open", progress=AsyncMock())
        self.assertEqual(browser.click.await_count, 1)

    async def test_onboarding_close_button_is_not_an_active_call(self):
        from itertools import count

        browser = FakeBrowser()
        browser.click = AsyncMock(return_value=True)
        browser.inspect.side_effect = [
            {"voice_button": True},
            {
                "onboarding_button": True,
                "onboarding_stage": "introduction",
                "end_button": True,
            },
            {
                "onboarding_button": True,
                "onboarding_stage": "voice_selection",
                "end_button": True,
            },
            {"voice_button": True},
            {"end_button": True},
        ]
        controller = Controller(
            browser, clock=count(step=4).__next__, sleep=AsyncMock()
        )
        await controller.handle("open", progress=AsyncMock())
        self.assertEqual(browser.click.await_count, 3)

    async def test_ready_hides_window_and_close_restores_logo(self):
        browser = FakeBrowser()
        browser.set_ui_visible = AsyncMock()
        indicator = Mock(error=None)
        controller = Controller(browser, indicator=indicator, sleep=AsyncMock())
        await controller.handle("open", progress=AsyncMock())
        browser.set_ui_visible.assert_has_awaits([call(True), call(False)])
        await controller.handle("close")
        self.assertEqual(
            indicator.show.call_args_list,
            [call("starting"), call("voice"), call("idle")],
        )
        self.assertEqual(browser.set_ui_visible.await_args, call(True))

    async def test_failed_start_releases_logo(self):
        browser = FakeBrowser()
        browser.start = AsyncMock(side_effect=BrowserError("launch failed"))
        browser.running = False
        indicator = Mock(error=None)
        controller = Controller(browser, indicator=indicator)
        with self.assertRaises(BrowserError):
            await controller.handle("open")
        self.assertEqual(
            indicator.show.call_args_list, [call("starting"), call("idle")]
        )

    async def test_manual_verification_timeout_keeps_browser_until_close(self):
        browser = FakeBrowser()
        browser.inspect.return_value = {
            "verification_required": True,
            "url": "https://chatgpt.com/",
        }
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(
                browser,
                state=StateStore(Path(directory)),
                settings=replace(Settings.from_env({}), voice_wait=60),
                clock=unittest.mock.Mock(side_effect=[0, 0, 61, 62]),
                sleep=AsyncMock(),
            )
            with self.assertRaisesRegex(BrowserError, "verification through VNC"):
                await controller.handle("open", progress=AsyncMock())
            self.assertEqual(controller.mode, "verification")
            self.assertTrue(browser.running)
            browser.stop.assert_not_awaited()
            await controller.handle("close")
            self.assertFalse(browser.running)

    async def test_click_alone_does_not_confirm_voice(self):
        browser = FakeBrowser()
        browser.click = AsyncMock(return_value=True)
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(
                browser,
                state=StateStore(Path(directory)),
                settings=replace(Settings.from_env({}), voice_wait=60),
                clock=unittest.mock.Mock(side_effect=[0, 0, 0, 0, 0, 61]),
                sleep=AsyncMock(),
            )
            with self.assertRaisesRegex(BrowserError, "voice_start_not_confirmed"):
                await controller.handle("open", progress=AsyncMock())
            self.assertEqual(controller.mode, "idle")

    async def test_onboarding_then_voice_waits_for_end_control(self):
        from itertools import count

        browser = FakeBrowser()
        browser.inspect.side_effect = [
            {"onboarding_button": True},
            {"voice_button": True},
            {"end_button": True},
        ]
        controller = Controller(browser, clock=count().__next__, sleep=AsyncMock())
        reply = await controller.handle("open", progress=AsyncMock())
        self.assertTrue(reply["voice_ui_ready"])
        self.assertEqual(browser.click.await_count, 2)

    async def test_terminal_page_error_stops_without_clicking(self):
        for issue in ("login_required", "voice_limit_reached"):
            with self.subTest(issue=issue), tempfile.TemporaryDirectory() as directory:
                browser = FakeBrowser()
                browser.inspect.return_value = {issue: True, "voice_button": True}
                controller = Controller(browser, state=StateStore(Path(directory)))
                with self.assertRaisesRegex(BrowserError, issue):
                    await controller.handle("open")
                browser.click.assert_not_awaited()
                self.assertFalse(browser.running)

    async def test_close_also_cancels_already_queued_open(self):
        browser = FakeBrowser()
        inspecting = asyncio.Event()

        async def inspect():
            inspecting.set()
            await asyncio.Event().wait()

        browser.inspect.side_effect = inspect
        controller = Controller(browser)
        first = asyncio.create_task(controller.handle("open"))
        await inspecting.wait()
        queued = asyncio.create_task(controller.handle("open"))
        await asyncio.sleep(0)
        await asyncio.wait_for(controller.handle("close"), 0.5)
        self.assertFalse((await first)["ok"])
        self.assertFalse((await queued)["ok"])
        self.assertFalse(browser.running)

    async def test_status_and_diagnose_bypass_conversation_lock(self):
        browser = FakeBrowser()
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory))
            state.write_diagnostic({"issue": "test"})
            controller = Controller(browser, state=state)
            await controller.lock.acquire()
            try:
                status = await asyncio.wait_for(controller.handle("status"), 0.5)
                diagnostic = await asyncio.wait_for(controller.handle("diagnose"), 0.5)
            finally:
                controller.lock.release()
            self.assertTrue(status["browser_running"])
            self.assertEqual(diagnostic["diagnostic"], {"issue": "test"})

    async def test_close_cancels_loading_and_releases_lock(self):
        browser = FakeBrowser()
        inspecting = asyncio.Event()

        async def inspect():
            inspecting.set()
            await asyncio.Event().wait()

        browser.inspect.side_effect = inspect
        controller = Controller(browser)
        opening = asyncio.create_task(controller.handle("open"))
        await asyncio.wait_for(inspecting.wait(), 0.5)
        result = await asyncio.wait_for(controller.handle("close"), 0.5)
        self.assertEqual(result["mode"], "idle")
        self.assertFalse((await opening)["ok"])
        self.assertFalse(controller.lock.locked())
        self.assertFalse(browser.running)

    async def test_external_cancellation_stops_browser(self):
        browser = FakeBrowser()
        inspecting = asyncio.Event()

        async def inspect():
            inspecting.set()
            await asyncio.Event().wait()

        browser.inspect.side_effect = inspect
        controller = Controller(browser)
        opening = asyncio.create_task(controller.handle("open"))
        await inspecting.wait()
        opening.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await opening
        self.assertFalse(browser.running)
        self.assertEqual(controller.mode, "idle")

    async def test_crashed_voice_session_can_restart(self):
        browser = FakeBrowser()
        browser.running = False
        controller = Controller(browser)
        controller.mode = "voice"
        self.assertEqual((await controller.handle("status"))["mode"], "idle")
        result = await controller.handle("open")
        browser.start.assert_awaited_once()
        self.assertTrue(result["click_sent"])
        self.assertFalse(result["verified"])

    async def test_failed_start_stops_browser_and_restores_idle(self):
        browser = FakeBrowser()
        browser.running = False
        browser.start.side_effect = BrowserError("failed launch")
        controller = Controller(browser)
        with self.assertRaises(BrowserError):
            await controller.handle("open")
        browser.stop.assert_awaited_once()
        self.assertEqual(controller.mode, "idle")

    async def test_timeout_writes_redacted_diagnostic_and_stops_browser(self):
        browser = FakeBrowser()
        browser.inspect.return_value = {
            "url": "https://chatgpt.com/",
            "chat_text": "secret",
        }
        with tempfile.TemporaryDirectory() as directory:
            state = StateStore(Path(directory))
            settings = replace(Settings.from_env({}), voice_wait=60)
            controller = Controller(
                browser,
                state=state,
                settings=settings,
                clock=unittest.mock.Mock(side_effect=[0, 0, 61, 62]),
                sleep=AsyncMock(),
            )
            with self.assertRaises(BrowserError):
                await controller.handle("open", progress=AsyncMock())
            self.assertNotIn("chat_text", state.read_diagnostic())
            self.assertFalse(browser.running)
