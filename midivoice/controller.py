"""Conversation lifecycle, independent of CLI and socket framing."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import Protocol

from .browser import Chromium
from .browser_diagnostics import voice_diagnostic
from .config import CHATGPT_HOME, Settings
from .errors import BrowserError, VoicePageError, BrowserVerificationRequired
from .state import StateStore, safe_chat_url
from .indicators import NoIndicator
from .commands import validate_argument


class VoiceBrowser(Protocol):
    """The controller depends on this interface, not Chromium/CDP internals."""

    @property
    def running(self) -> bool: ...
    @property
    def memory_kb(self) -> int | None: ...
    async def start(self, url: str | None = None): ...
    async def stop(self, force: bool = False): ...
    async def inspect(self) -> dict: ...
    async def click(self, ending: bool = False) -> bool: ...
    async def current_url(self) -> str: ...
    async def navigate_home(self): ...
    async def snapshot(self) -> dict: ...
    async def network_check(self) -> dict: ...
    async def extension_status(self) -> dict: ...
    async def new_conversation(self): ...
    async def navigate_context(
        self, action: str, argument: str | None = None
    ) -> dict: ...


class Controller:
    def __init__(
        self,
        browser: VoiceBrowser | None = None,
        *,
        settings: Settings | None = None,
        state: StateStore | None = None,
        clock=None,
        sleep=None,
        indicator=None,
    ):
        self.settings = settings or Settings.from_env()
        self.state = state or StateStore(self.settings.state_dir)
        self.browser = (
            browser if browser is not None else Chromium(settings=self.settings)
        )
        self.clock = clock or time.monotonic
        self.sleep = sleep or asyncio.sleep
        self.mode = "idle"
        self.lock = asyncio.Lock()
        self.opening: asyncio.Task | None = None
        self.generation = 0
        self.indicator = indicator or NoIndicator()

    def _set_mode(self, mode):
        self.mode = mode
        self.indicator.show(mode)

    async def _show_ui(self, visible):
        # Optional capability: non-graphical backends need no window management.
        method = getattr(self.browser, "set_ui_visible", None)
        if method is not None:
            try:
                await method(visible)
            except Exception as exc:
                print(f"Browser window update failed: {type(exc).__name__}", flush=True)

    async def watch(self):
        """Clear the logo after a crashed browser without polling its renderer."""
        while True:
            await asyncio.sleep(2)
            if (
                self.mode in ("voice", "chat", "work", "verification")
                and not self.browser.running
            ):
                self._set_mode("idle")
                with contextlib.suppress(Exception):
                    await self.browser.stop(force=True)

    async def status(self):
        alive = self.browser.running
        if self.mode in ("voice", "chat", "work") and not alive:
            self._set_mode("idle")
        state = {
            "mode": "idle" if self.mode == "voice" and not alive else self.mode,
            "browser_running": alive,
            "ui_hidden": bool(getattr(self.browser, "ui_hidden", False)),
        }
        if self.indicator.error:
            state["indicator_error"] = self.indicator.error
        if alive:
            navigator = getattr(self.browser, "navigator", None)
            if navigator is not None:
                state.update(
                    workspace_mode=navigator.workspace_mode,
                    project=navigator.project_name,
                )
            state["browser_pss_kb"] = self.browser.memory_kb
            try:
                page = await self.browser.inspect()
                state.update(
                    {
                        "voice_button": bool(page.get("voice_button")),
                        "end_button": bool(page.get("end_button")),
                        "voice_ui_ready": bool(page.get("end_button")),
                        "ui_hidden": page.get("visibility") == "hidden",
                        "verification_required": bool(
                            page.get("verification_required")
                        ),
                        "chat_url": safe_chat_url(page.get("url")),
                    }
                )
                if self.mode == "voice" and not page.get("end_button"):
                    self._set_mode("idle")
                    state["mode"] = "idle"
            except Exception as exc:
                state["error"] = str(exc) or type(exc).__name__
        return state

    async def _ensure_browser(self, new: bool = False):
        if not self.browser.running:
            navigator = getattr(self.browser, "navigator", None)
            new_url = (
                (getattr(navigator, "project_url", None) or CHATGPT_HOME)
                if new
                else None
            )
            await self.browser.start(new_url)
        elif new:
            method = getattr(self.browser, "new_conversation", None)
            await (method() if method is not None else self.browser.navigate_home())

    async def _open(self, new: bool = False, progress=None):
        wait_seconds = self.settings.voice_wait
        self._set_mode("starting")
        latest = None
        started = self.clock()
        last_notice = -1
        last_problem = None
        redirected = new
        click_sent = False
        voice_started = False
        onboarding_sent: set[str] = set()
        next_click = 0.0
        try:
            if progress:
                await progress(
                    {
                        "event": "progress",
                        "stage": "starting_browser",
                        "elapsed_s": 0,
                        "timeout_s": wait_seconds,
                    }
                )
            await self._ensure_browser(new)
            await self._show_ui(True)
            # The official ChatGPT app on Midi has taken several minutes to
            # fully mount its React controls. Never refresh a still-loading
            # chatgpt.com page: that restarts the entire expensive download.
            while self.clock() - started < wait_seconds:
                if not self.browser.running:
                    raise BrowserError(
                        "Chromium exited while loading; inspect chromium.log"
                    )
                try:
                    page = await self.browser.inspect()
                    if page is not None:
                        latest = page
                        last_problem = None
                    for flag, issue in (
                        ("login_required", "login_required"),
                        ("voice_limit_reached", "voice_limit_reached"),
                    ):
                        if page and page.get(flag):
                            self.state.write_diagnostic(voice_diagnostic(page, issue))
                            raise VoicePageError(issue)
                    if (
                        page
                        and page.get("end_button")
                        and not page.get("onboarding_button")
                    ):
                        if self.settings.quiet_ui:
                            await self._show_ui(False)
                        self._set_mode("voice")
                        return {
                            "ok": True,
                            "mode": self.mode,
                            "already_active": not click_sent,
                            "click_sent": click_sent,
                            "voice_ui_ready": True,
                            "verified": False,
                        }
                    if (
                        page
                        and not page.get("verification_required")
                        and (page.get("voice_button") or page.get("onboarding_button"))
                        and self.clock() >= next_click
                    ):
                        stage = (
                            page.get("onboarding_stage")
                            if page.get("onboarding_button")
                            else None
                        )
                        can_click = (
                            (stage or "unknown") not in onboarding_sent
                            if page.get("onboarding_button")
                            else not voice_started
                        )
                        if can_click and await self.browser.click(ending=False):
                            click_sent = True
                            if page.get("onboarding_button"):
                                onboarding_sent.add(stage or "unknown")
                            if (
                                not page.get("onboarding_button")
                                or stage == "voice_selection"
                            ):
                                voice_started = True
                            next_click = self.clock() + 3
                    url = (page or {}).get("url") or ""
                    # A wrong initial target can happen on a restored profile.
                    # Navigate once ONLY when genuinely outside ChatGPT.
                    if (
                        not redirected
                        and url
                        and not url.startswith("https://chatgpt.com/")
                        and self.clock() - started >= 45
                    ):
                        await self.browser.navigate_home()
                        redirected = True
                except VoicePageError:
                    raise
                except (BrowserError, asyncio.TimeoutError, OSError) as exc:
                    # Renderer can briefly stall under extreme RAM pressure.
                    # A single CDP timeout must not abort the entire attempt.
                    last_problem = type(exc).__name__
                elapsed = self.clock() - started
                notice = int(elapsed // 15)
                if notice > last_notice:
                    last_notice = notice
                    snapshot = voice_diagnostic(latest, "waiting_for_voice_control")
                    event = {
                        "event": "progress",
                        "stage": "waiting_for_voice_ui",
                        "elapsed_s": int(elapsed),
                        "timeout_s": wait_seconds,
                        "page": snapshot,
                    }
                    if last_problem:
                        event["last_error_type"] = last_problem
                    if progress:
                        await progress(event)
                    else:
                        print(
                            "Voice UI loading: "
                            + json.dumps(event, ensure_ascii=False),
                            flush=True,
                        )
                await self.sleep(1)
            issue = (
                "browser_verification_required"
                if latest and latest.get("verification_required")
                else "voice_start_not_confirmed"
                if click_sent
                else "voice_control_not_found"
            )
            data = voice_diagnostic(latest, issue)
            self.state.write_diagnostic(data)
            if issue == "browser_verification_required":
                raise BrowserVerificationRequired(
                    "Complete the browser verification through VNC, then run `open` again. "
                    "The browser remains open until `close`."
                )
            raise BrowserError(
                f"{issue} after {wait_seconds}s; "
                "run `python3 -m midivoice diagnose`; "
                "diagnostic=" + json.dumps(data, ensure_ascii=False)
            )
        except BrowserVerificationRequired:
            self._set_mode("verification")
            raise
        except BaseException:
            self._set_mode("idle")
            # Keeping an undetected browser alive wastes RAM on 1GB hardware.
            with contextlib.suppress(Exception):
                await self.browser.stop()
            raise

    async def _close(self):
        try:
            if self.browser.running:
                await self._show_ui(True)
                with contextlib.suppress(Exception):
                    await self.browser.click(ending=True)
                    # The site can create the conversation URL after call ending.
                    for _ in range(12):
                        await self.sleep(0.25)
                        if self.state.save_chat_url(await self.browser.current_url()):
                            break
                with contextlib.suppress(Exception):
                    self.state.save_chat_url(await self.browser.current_url())
        finally:
            try:
                await self.browser.stop()
            finally:
                self._set_mode("idle")
        return {"ok": True, "mode": "idle"}

    async def _navigate(self, action, argument, progress):
        resume_voice = self.mode == "voice"
        self._set_mode("starting")
        try:
            await self._ensure_browser(new=not self.browser.running)
            await self._show_ui(True)
            context = await self.browser.navigate_context(action, argument)
            self._set_mode(context["workspace_mode"])
            if resume_voice:
                result = await self._open(progress=progress)
                return {**result, **context}
            return {"ok": True, "mode": self.mode, **context}
        except BrowserVerificationRequired:
            self._set_mode("verification")
            raise
        except BaseException:
            navigator = getattr(self.browser, "navigator", None)
            self._set_mode(
                getattr(navigator, "workspace_mode", "chat")
                if self.browser.running
                else "idle"
            )
            raise

    async def handle(self, action: str, progress=None, argument=None):
        validate_argument(action, argument)
        generation = self.generation
        if action == "close":
            self.generation += 1
            if self.opening is not None and not self.opening.cancelling():
                self.opening.cancel()
        if progress and action in (
            "open",
            "new",
            "close",
            "project",
            "home",
            "mode-chat",
            "mode-work",
        ):
            await progress({"event": "progress", "stage": "accepted"})
        # Diagnostics must be available while `open` holds the conversation
        # control lock for several minutes. CDP itself multiplexes request IDs.
        if action == "status":
            return {"ok": True, **(await self.status())}
        if action in ("ui-show", "ui-hide"):
            if not self.browser.running:
                return {"ok": False, "error": "No Chromium running"}
            await self._show_ui(action == "ui-show")
            return {
                "ok": True,
                "ui_hidden": bool(getattr(self.browser, "ui_hidden", False)),
            }
        if action == "diagnose":
            diagnostic = self.state.read_diagnostic()
            return (
                {"ok": True, "diagnostic": diagnostic}
                if diagnostic is not None
                else {"ok": False, "error": "No previous voice diagnostic"}
            )
        if action == "audio-stats":
            if not self.browser.running:
                return {"ok": False, "error": "No Chromium running"}
            return {"ok": True, "audio": await self.browser.audio_stats()}
        if action in ("snapshot", "vpn-check"):
            if not self.browser.running:
                return {"ok": False, "error": "No Chromium running; run `open` first"}
            if action == "snapshot":
                return {"ok": True, "snapshot": await self.browser.snapshot()}
            return {
                "ok": True,
                "vpn": await self.browser.network_check(),
                "extensions": await self.browser.extension_status(),
            }
        async with self.lock:
            if action in ("project", "mode-chat", "mode-work", "home", "open", "new"):
                if generation != self.generation:
                    return {"ok": False, "error": "Operation cancelled"}
                operation = (
                    self._open(new=action == "new", progress=progress)
                    if action in ("open", "new")
                    else self._navigate(action, argument, progress)
                )
                self.opening = asyncio.create_task(operation)
                try:
                    return await self.opening
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    return {"ok": False, "error": "Operation cancelled"}
                finally:
                    self.opening = None
            if action == "close":
                return await self._close()
            raise ValueError(
                "Expected: open, new, close, status, snapshot, vpn-check or diagnose"
            )
