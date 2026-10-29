"""ChatGPT web voice adapter. CDP transport and launch policy are separate."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import shutil

from .browser_launch import (
    voice_launch_flags,
)
from .browser_ui import voice_inspector_script
from .cdp import CDPConnection
from .config import Settings
from .errors import BrowserError
from .resources import browser_pss_kb
from .state import StateStore


class Chromium:
    """One ChatGPT tab; uses only visible controls and a private CDP pipe."""

    def __init__(
        self, executable: str | None = None, *, settings: Settings | None = None
    ):
        self.settings = settings or Settings.from_env()
        self.executable = executable or self.settings.chromium
        self.state = StateStore(self.settings.state_dir)
        self.connection = CDPConnection()
        self.session: str | None = None
        self.target_id: str | None = None
        self.ui_hidden = False
        self.extension_flag_enabled: bool | None = None
        self.rtc_target: str | None = None
        self.rtc_session: str | None = None
        self.rtc_legacy_request = False
        from .navigation import BrowserNavigator

        self.navigator = BrowserNavigator(self)

    @property
    def proc(self):
        return self.connection.proc

    @property
    def running(self) -> bool:
        return bool(
            self.proc and self.proc.poll() is None and not self.connection.error
        )

    @property
    def memory_kb(self) -> int | None:
        return browser_pss_kb(self.proc.pid) if self.running else None

    async def start(self, url: str | None = None):
        if self.running:
            return
        self.session = None
        binary = shutil.which(self.executable)
        if not binary:
            raise BrowserError(f"Chromium is missing: {self.executable}")
        from pathlib import Path

        if Path(binary).name != "midi-chromium":
            self.settings.profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        restored = self.state.last_chat_url()
        project = self.navigator.project_url
        if project and not restored.startswith(
            project.removesuffix("/project") + "/c/"
        ):
            restored = project
        flags = voice_launch_flags(url or restored, self.settings)
        effective = await self.connection.start(
            binary,
            flags + list(self.settings.extra_flags),
            self.settings.profile_dir,
            self.settings.state_dir / "chromium.log",
            alpine_root=self.settings.alpine_root,
            display=self.settings.display,
        )
        self.extension_flag_enabled = "--disable-extensions" not in effective
        try:
            for _ in range(30):
                if self.proc.poll() is not None:
                    raise BrowserError("Chromium exited; see chromium.log")
                result = await self.send("Target.getTargets")
                # The dedicated profile has only our own --app page.
                # Initial targets may expose an internal loading URL.
                pages = [
                    t for t in result.get("targetInfos", []) if t.get("type") == "page"
                ]
                page = next(
                    (
                        t
                        for t in pages
                        if (t.get("url") or "").startswith("https://chatgpt.com/")
                    ),
                    None,
                )
                if not page and pages and _ == 29:
                    page = pages[0]
                if page:
                    self.target_id = page["targetId"]
                    attached = await self.send(
                        "Target.attachToTarget",
                        {
                            "targetId": page["targetId"],
                            "flatten": True,
                        },
                    )
                    self.session = attached["sessionId"]
                    await self.send("Inspector.enable", page=True)
                    await self.send(
                        "Browser.grantPermissions",
                        {
                            "origin": "https://chatgpt.com",
                            "permissions": ["audioCapture"],
                        },
                    )
                    await self.send(
                        "Emulation.setEmulatedMedia",
                        {
                            "features": [
                                {"name": "prefers-reduced-motion", "value": "reduce"}
                            ]
                        },
                        page=True,
                    )
                    return
                await asyncio.sleep(0.3)
            raise BrowserError("No ChatGPT page opened")
        except BaseException:
            await self.stop(force=True)
            raise

    async def send(self, method: str, params: dict | None = None, page: bool = False):
        if page and not self.session:
            raise BrowserError("No attached page")
        return await self.connection.send(
            method, params, session=self.session if page else None
        )

    async def evaluate(self, js: str):
        result = await self.send(
            "Runtime.evaluate",
            {
                "expression": js,
                "returnByValue": True,
                "awaitPromise": True,
            },
            page=True,
        )
        if "exceptionDetails" in result:
            raise BrowserError("Browser script failed")
        return result.get("result", {}).get("value")

    async def current_url(self):
        return await self.evaluate("location.href")

    async def set_ui_visible(self, visible: bool):
        """Minimize visual work without freezing the page or its audio timers."""
        if not self.target_id:
            return
        result = await self.send(
            "Browser.getWindowForTarget", {"targetId": self.target_id}
        )
        await self.send(
            "Browser.setWindowBounds",
            {
                "windowId": result["windowId"],
                "bounds": {"windowState": "normal" if visible else "minimized"},
            },
        )
        if visible:
            await self.send("Page.bringToFront", page=True)
        # The window manager applies the request asynchronously.
        for _ in range(10):
            self.ui_hidden = (
                await self.evaluate("document.visibilityState")
            ) == "hidden"
            if self.ui_hidden == (not visible):
                break
            await asyncio.sleep(0.05)

    async def inspect(self):
        return await self.evaluate(voice_inspector_script())

    async def click(self, ending: bool = False) -> bool:
        page = await self.evaluate(voice_inspector_script("end" if ending else "start"))
        point = (page or {}).get("point")
        if not point:
            return False
        await self.click_point(point)
        return True

    async def click_point(self, point):
        for type_ in ("mousePressed", "mouseReleased"):
            await self.send(
                "Input.dispatchMouseEvent",
                {
                    "type": type_,
                    "button": "left",
                    "clickCount": 1,
                    **point,
                },
                page=True,
            )

    async def new_conversation(self):
        await self.navigator.new_conversation()

    async def navigate_context(self, action, argument=None):
        if action == "project":
            await self.navigator.select_project(argument)
        elif action == "home":
            await self.navigator.exit_project()
        else:
            await self.navigator.select_mode(
                "chat" if action == "mode-chat" else "work"
            )
        return {
            "workspace_mode": self.navigator.workspace_mode,
            "project": self.navigator.project_name,
        }

    async def extension_status(self) -> dict:
        """Inspect local profile and targets, not extension storage or secrets."""
        from .browser_diagnostics import extension_inventory, active_extension_targets

        target_infos = (await self.send("Target.getTargets")).get("targetInfos", [])
        installed = extension_inventory(self.settings.profile_dir)
        workers = active_extension_targets(target_infos)
        return {
            "extensions_allowed_by_launch_flags": self.extension_flag_enabled,
            "installed": installed,
            "active_extension_targets": workers,
            "warning": "Extensions allowed/installed does not prove a VPN connection is active",
        }

    async def snapshot(self) -> dict:
        """Save private PNG+HTML of the existing headless tab; don't navigate."""
        from .browser_diagnostics import (
            write_snapshot,
            HTML_SNAPSHOT_JS,
            page_structure_js,
        )

        if not self.proc or self.proc.poll() is not None or not self.session:
            raise BrowserError("Browser is not active. Start `open` first.")
        structure = await self.evaluate(page_structure_js())
        html = await self.evaluate(HTML_SNAPSHOT_JS)
        screenshot = None
        screenshot_error = None
        try:
            result = await self.send(
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True},
                page=True,
            )
            screenshot = base64.b64decode(result["data"], validate=True)
        except (
            BrowserError,
            ValueError,
            KeyError,
            TimeoutError,
            asyncio.TimeoutError,
        ) as exc:
            screenshot_error = type(exc).__name__ + ": " + str(exc)[:120]
        report = write_snapshot(
            self.settings.state_dir / "snapshots",
            structure or {},
            html or "",
            screenshot,
        )
        if screenshot_error:
            report["screenshot_error"] = screenshot_error
        report["extensions"] = await self.extension_status()
        return report

    async def network_check(self) -> dict:
        """An unauthenticated browser-context IP check via CDP, no site reload.

        This confirms a reachable endpoint and browser egress IP. A comparison
        with a non-VPN route is required before claiming VPN routing.
        """
        if not self.session:
            raise BrowserError("No active browser tab")
        from .browser_diagnostics import decode_ip_response

        report = {
            "endpoint": "https://api.ipify.org?format=json",
            "browser_egress_ip": None,
            "vpn_confirmed": False,
        }
        try:
            frame_tree = await self.send("Page.getFrameTree", page=True)
            frame_id = frame_tree["frameTree"]["frame"]["id"]
            result = await self.send(
                "Network.loadNetworkResource",
                {
                    "frameId": frame_id,
                    "url": report["endpoint"],
                    "options": {"disableCache": True, "includeCredentials": False},
                },
                page=True,
            )
            resource = result.get("resource", {})
            report["http_status"] = resource.get("httpStatusCode")
            report["net_error"] = resource.get("netErrorName")
            if not resource.get("success"):
                report["error"] = "CDP network request failed"
            elif resource.get("stream"):
                stream = resource["stream"]
                try:
                    chunk = await self.send(
                        "IO.read", {"handle": stream, "size": 4096}, page=True
                    )
                    raw = chunk.get("data", "")
                    if chunk.get("base64Encoded"):
                        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
                    report["browser_egress_ip"] = decode_ip_response(raw)
                finally:
                    with contextlib.suppress(Exception):
                        await self.send("IO.close", {"handle": stream}, page=True)
            else:
                report["error"] = "CDP did not return a stream"
        except (
            BrowserError,
            KeyError,
            ValueError,
            OSError,
            asyncio.TimeoutError,
        ) as exc:
            report["error"] = type(exc).__name__ + ": " + str(exc)[:160]
        report["note"] = (
            "Compare browser_egress_ip to direct station IP to verify VPN routing; identical IP does not always disprove VPN"
        )
        return report

    async def navigate_home(self):
        await self.send("Page.navigate", {"url": "https://chatgpt.com/"}, page=True)

    async def audio_stats(self):
        from .webrtc_diagnostics import RTC_STATS_JS, RTC_REQUEST_149_JS

        if self.rtc_session is None:
            created = await self.send(
                "Target.createTarget",
                {
                    "url": "chrome://webrtc-internals/",
                    "background": True,
                },
            )
            self.rtc_target = created["targetId"]
            attached = await self.send(
                "Target.attachToTarget",
                {
                    "targetId": self.rtc_target,
                    "flatten": True,
                },
            )
            self.rtc_session = attached["sessionId"]
            version = await self.send("Browser.getVersion")
            self.rtc_legacy_request = "/149." in version.get("product", "")
        for _ in range(10):
            if self.rtc_legacy_request:
                await self.connection.send(
                    "Runtime.evaluate",
                    {
                        "expression": RTC_REQUEST_149_JS,
                    },
                    session=self.rtc_session,
                )
            result = await self.connection.send(
                "Runtime.evaluate",
                {
                    "expression": RTC_STATS_JS,
                    "returnByValue": True,
                },
                session=self.rtc_session,
            )
            if "exceptionDetails" in result:
                raise BrowserError("WebRTC diagnostics script failed")
            data = result.get("result", {}).get("value") or {}
            if data.get("ready"):
                return data
            await asyncio.sleep(0.25)
        return {"ready": False, "connections": []}

    async def stop(self, force: bool = False):
        try:
            if self.rtc_target and not self.connection.error:
                with contextlib.suppress(Exception):
                    await self.send("Target.closeTarget", {"targetId": self.rtc_target})
            await self.connection.stop(force=force)
        finally:
            self.session = None
            self.target_id = None
            self.ui_hidden = False
            self.rtc_target = self.rtc_session = None
