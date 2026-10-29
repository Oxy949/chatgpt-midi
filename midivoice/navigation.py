"""Select Chat/Work and named projects through the visible ChatGPT UI."""

import json
import re
import asyncio
import time
from urllib.parse import urlsplit

from .errors import BrowserError, BrowserVerificationRequired, VoicePageError
from .state import write_private_json
from .commands import validate_argument


def safe_project_url(value: str | None) -> str | None:
    try:
        url = urlsplit(value or "")
        if url.scheme != "https" or url.netloc != "chatgpt.com":
            return None
        if not re.fullmatch(r"/g/g-p-[a-zA-Z0-9-]{16,80}/project/?", url.path):
            return None
        return "https://chatgpt.com" + url.path.rstrip("/")
    except (TypeError, ValueError, AttributeError):
        return None


NAVIGATION_JS = r"""(()=>{
 const action=__ACTION__, argument=__ARGUMENT__;
 const normalize=s=>(s||'').toLocaleLowerCase('ru').replace(/ё/g,'е')
   .replace(/[^\p{L}\p{N}_\s]/gu,' ').trim().replace(/\s+/g,' ');
 const projectName=s=>normalize(s).replace(/(^|\s)(чат джи пи ти|чат джипити|чат гпт|чат gpt|chat gpt|chat g p t)(?=\s|$)/g,'$1chatgpt');
 const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);
   return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&
     !e.disabled&&e.getAttribute('aria-hidden')!=='true';};
 const point=e=>{e.scrollIntoView({block:'nearest',inline:'nearest'});
   const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};};
 const buttons=[...document.querySelectorAll('button,[role="tab"],[role="button"]')];
 const labels={chat:['чат','chat'],work:['работа','work']};
 const modeButtons={};
 for(const [mode,names] of Object.entries(labels)){
   const matches=buttons.filter(b=>visible(b)&&names.includes(normalize(b.textContent))&&
     b.closest('[role="group"],[role="tablist"]'));
   if(matches.length===1)modeButtons[mode]=matches[0];
 }
 const active=Object.entries(modeButtons).find(([,b])=>
   b.getAttribute('aria-pressed')==='true'||b.getAttribute('aria-selected')==='true');
 const result={origin:location.origin,workspace_mode:active?.[0]||null,has_switcher:!!(modeButtons.chat&&modeButtons.work)};
 for(const dialog of document.querySelectorAll('[role="dialog"]')){
   const text=dialog.textContent||'';
   if(visible(dialog)&&/voice|голос/i.test(text)&&/limit|лимит/i.test(text)){
     const dismiss=[...dialog.querySelectorAll('button')].find(b=>visible(b)&&
       /^(понятно|ok|okay|got it)$/i.test(b.textContent.trim()));
     if(dismiss)result.dismiss_point=point(dismiss);
   }
 }
 if(action==='mode'&&modeButtons[argument])result.point=point(modeButtons[argument]);
 if(action==='project'){
   const matches=new Map();
   for(const a of document.querySelectorAll('a[href]')){
     let url;try{url=new URL(a.getAttribute('href'),location.href);}catch{continue;}
     if(url.origin!=='https://chatgpt.com'||!/^\/g\/g-p-[a-zA-Z0-9-]{16,80}\/project\/?$/.test(url.pathname))continue;
     const name=normalize(a.getAttribute('title')||a.getAttribute('aria-label')||a.textContent);
     if(projectName(name)===projectName(argument)&&visible(a))matches.set(url.pathname.split('/')[2],a);
   }
   for(const row of document.querySelectorAll('[data-app-action-sidebar-project-id][data-app-action-sidebar-project-label]')){
     const id=row.getAttribute('data-app-action-sidebar-project-id');
     if(/^g-p-[a-zA-Z0-9-]{16,80}$/.test(id||'')&&visible(row)&&
       projectName(row.getAttribute('data-app-action-sidebar-project-label'))===projectName(argument))matches.set(id,row);
   }
   result.project_matches=matches.size;
   if(matches.size===1){const [id,link]=[...matches][0];result.target_project_id=id;result.point=point(link);}
   if(!matches.size){
     const expander=buttons.find(b=>visible(b)&&b.getAttribute('aria-expanded')==='false'&&
       ['проекты','projects'].includes(normalize(b.getAttribute('aria-label')||b.textContent)));
     const more=buttons.find(b=>visible(b)&&['все проекты','показать все проекты','view all projects','all projects']
       .includes(normalize(b.getAttribute('aria-label')||b.textContent)));
     const sidebar=buttons.find(b=>visible(b)&&['открыть боковую панель','open sidebar']
       .includes(normalize(b.getAttribute('aria-label'))));
     if(expander||more||sidebar)result.expand_point=point(expander||more||sidebar);
   }
 }
 return result;
})()"""


def navigation_script(action: str = "inspect", argument: str | None = None) -> str:
    if action not in ("inspect", "mode", "project"):
        raise ValueError("Unknown navigation action")
    if action == "mode" and argument not in ("chat", "work"):
        raise ValueError("Unknown workspace mode")
    return NAVIGATION_JS.replace("__ACTION__", json.dumps(action)).replace(
        "__ARGUMENT__", json.dumps(argument, ensure_ascii=True)
    )


class BrowserNavigator:
    def __init__(self, browser):
        self.browser = browser
        self.file = browser.state.directory / "navigation.json"
        self.workspace_mode = "chat"
        self.project_name = None
        self.project_url = None
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            if data.get("workspace_mode") in ("chat", "work"):
                self.workspace_mode = data["workspace_mode"]
            url = safe_project_url(data.get("project_url"))
            if url:
                validate_argument("project", data.get("project_name"))
                self.project_name, self.project_url = data["project_name"], url
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        write_private_json(
            self.file,
            {
                "workspace_mode": self.workspace_mode,
                "project_name": self.project_name,
                "project_url": self.project_url,
            },
        )

    async def inspect(self, action="inspect", argument=None):
        data = await self.browser.evaluate(navigation_script(action, argument)) or {}
        return (
            data
            if data.get("origin", "https://chatgpt.com") == "https://chatgpt.com"
            else {}
        )

    async def end_voice(self):
        if (await self.browser.inspect()).get("end_button"):
            if not await self.browser.click(ending=True):
                raise BrowserError("Could not end the current voice conversation")
            for _ in range(40):
                if not (await self.browser.inspect()).get("end_button"):
                    return
                await asyncio.sleep(0.25)
            raise BrowserError("Voice did not end before navigation")

    async def check_page(self):
        page = await self.browser.inspect()
        if page.get("verification_required"):
            raise BrowserVerificationRequired(
                "Complete the browser verification through VNC, then repeat the command"
            )
        if page.get("login_required"):
            raise VoicePageError("login_required")

    async def new_conversation(self):
        await self.end_voice()
        self.browser.state.save_chat_url(await self.browser.current_url())
        await self.browser.send(
            "Page.navigate",
            {
                "url": self.project_url or "https://chatgpt.com/",
            },
            page=True,
        )

    async def select_mode(self, mode):
        if mode not in ("chat", "work"):
            raise ValueError("Unknown workspace mode")
        await self.end_voice()
        current = await self.browser.current_url()
        self.browser.state.save_chat_url(current)
        target = self.project_url or "https://chatgpt.com/"
        if current != target:
            await self.browser.send("Page.navigate", {"url": target}, page=True)
        deadline = time.monotonic() + self.browser.settings.voice_wait
        clicked = 0.0
        dismissed = False
        while time.monotonic() < deadline:
            await self.check_page()
            page = await self.inspect("mode", mode)
            if page.get("dismiss_point") and not dismissed:
                await self.browser.click_point(page["dismiss_point"])
                dismissed = True
            elif page.get("workspace_mode") == mode:
                self.workspace_mode = mode
                self.save()
                return
            elif page.get("point") and time.monotonic() - clicked >= 3:
                await self.browser.click_point(page["point"])
                clicked = time.monotonic()
            await asyncio.sleep(0.5)
        raise BrowserError("Chat/Work switcher did not confirm the requested mode")

    async def exit_project(self):
        self.project_name = self.project_url = None
        await self.new_conversation()
        self.save()

    async def select_project(self, name):
        validate_argument("project", name)
        await self.end_voice()
        self.browser.state.save_chat_url(await self.browser.current_url())
        deadline = time.monotonic() + min(90, self.browser.settings.voice_wait)
        expanded = set()
        target = None
        while time.monotonic() < deadline:
            await self.check_page()
            if target:
                url = safe_project_url(await self.browser.current_url())
                if url and url.split("/")[4] == target:
                    self.project_name, self.project_url = name, url
                    self.save()
                    return
            else:
                page = await self.inspect("project", name)
                if page.get("dismiss_point") and "quota" not in expanded:
                    expanded.add("quota")
                    await self.browser.click_point(page["dismiss_point"])
                    await asyncio.sleep(0.5)
                    continue
                if page.get("project_matches", 0) > 1:
                    raise BrowserError("Project name is ambiguous")
                if page.get("point"):
                    target = page.get("target_project_id")
                    if not re.fullmatch(r"g-p-[a-zA-Z0-9-]{16,80}", target or ""):
                        raise BrowserError("Project link is not allowed")
                    await self.browser.click_point(page["point"])
                elif page.get("expand_point"):
                    point = page["expand_point"]
                    key = (round(point["x"]), round(point["y"]))
                    if key not in expanded:
                        expanded.add(key)
                        await self.browser.click_point(point)
            await asyncio.sleep(0.5)
        raise BrowserError("Project was not found or did not open")
