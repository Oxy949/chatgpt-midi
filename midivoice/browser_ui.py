"""ChatGPT visible voice controls. No transport or process management."""

import json

# Keep browser automation limited to the documented visible UI. The inspector
# returns only structural flags; it never returns chat contents or auth data.
VOICE_CONTROLS_JS = r"""(()=>{
  const nodes = Array.from(document.querySelectorAll('button,[role="button"]'));
  const voiceRx = /\bvoice\b|voice[-_ ]?mode|voice[-_ ]?chat|голос|разговор|talk to chatgpt|start.{0,15}(call|conversation)/i;
  const endRx = /(end|exit|leave|stop|close).{0,20}(voice|call|conversation)|voice.{0,20}(end|stop|exit)|заверш|законч|выйти из голос/i;
  const dictationRx = /dictat|transcrib|speech[-_ ]to[-_ ]text|диктов|диктант|voice input/i;
  const isVisible = b => {
    if (b.disabled || b.getAttribute('aria-disabled') === 'true') return false;
    const r = b.getBoundingClientRect();
    const style = getComputedStyle(b);
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0 &&
      r.top < innerHeight && r.left < innerWidth && style.visibility !== 'hidden' &&
      style.display !== 'none' && b.getAttribute('aria-hidden') !== 'true';
  };
  const describe = b => {
    const own = [b.getAttribute('aria-label'), b.getAttribute('data-testid'), b.getAttribute('title')];
    const icon = b.querySelector('svg,[data-testid],[aria-label]');
    if (icon) own.push(icon.getAttribute('data-testid'), icon.getAttribute('aria-label'), icon.getAttribute('data-lucide'));
    return own.filter(Boolean).join(' ');
  };
  const controls = nodes.map(b=>{
    const label=describe(b);
    const ends=endRx.test(label), dictates=dictationRx.test(label);
    const starts=voiceRx.test(label) && !ends && !dictates && !/mute|settings|camera|video/i.test(label);
    const candidate=starts||ends;
    const r=b.getBoundingClientRect();
    return {b, starts, ends, candidate, visible:isVisible(b),
      score:(/data-testid|voice[-_ ]?mode|composer[-_ ]?voice/i.test(label)?10:0) +
        (b.closest('form,[data-testid*="composer"]')?5:0),
      x:r.x+r.width/2, y:r.y+r.height/2};
  });
  const ordered=controls.filter(c=>c.visible).sort((a,b)=>b.score-a.score);
  const voice=ordered.find(c=>c.starts);
  const end=ordered.find(c=>c.ends);
  const dialogs = Array.from(document.querySelectorAll('[role="dialog"]')).filter(isVisible);
  let onboarding = null;
  let onboardingStage = null;
  for (const dialog of dialogs) {
    const heading = dialog.querySelector('h1,h2,h3,[role="heading"]');
    const title = (heading?.textContent || dialog.getAttribute('aria-label') || '').trim();
    const introduction = /^(meet voice|знакомьтесь.*голос|знакомство.*голос)/i.test(title);
    const selection = /^(choose your voice|выберите голос)/i.test(title);
    if (!introduction && !selection) continue;
    const button = Array.from(dialog.querySelectorAll('button')).find(b => isVisible(b) &&
      (introduction ? /^(continue|продолжить)$/i : /^(start voice|начать голосовой (чат|разговор))$/i)
        .test((b.getAttribute('aria-label') || b.textContent || '').trim()));
    if (button) { onboarding = button; onboardingStage = introduction ? 'introduction' : 'voice_selection'; break; }
  }
  // Only dedicated notices and dialog headings are examined, never chat messages.
  const notices = [...dialogs, ...document.querySelectorAll('[role="alert"],[data-testid*="limit"]')]
    .filter(isVisible).map(e => (e.textContent || '').slice(0,500)).join(' ');
  const composer = !!document.querySelector('textarea,[contenteditable="true"],[role="textbox"]');
  const turnstile = !composer && nodes.length === 0 && !!document.querySelector(
    'iframe[src*="challenges.cloudflare.com"],script[src*="challenges.cloudflare.com/turnstile"],.cf-turnstile');
  const verification = turnstile || /just a moment|verify you are human|checking your browser|один момент|проверяем ваш браузер/i
    .test(document.title) || !!document.querySelector('#challenge-form,#challenge-running');
  const quota = /voice.{0,80}limit|limit.{0,80}voice|лимит.{0,80}голос|голос.{0,80}лимит/i.test(notices);
  const login = Array.from(document.querySelectorAll('a[href*="/auth/login"],button[data-testid="login-button"]')).some(isVisible);
  const result={url:location.href,ready:document.readyState,
    buttons:nodes.length,composer:composer,
    media_devices:!!navigator.mediaDevices,visibility:document.visibilityState,
    voice_button:!!voice,end_button:!!end,
    onboarding_button:!!onboarding,onboarding_stage:onboardingStage,
    verification_required:verification,voice_limit_reached:quota,login_required:login,
    voice_candidates:controls.filter(c=>c.starts).length,
    end_candidates:controls.filter(c=>c.ends).length,
    hidden_voice_candidates:controls.filter(c=>c.starts&&!c.visible).length,
    viewport:[window.innerWidth,window.innerHeight]};
  if (__CLICK__ === 'start' && !verification && !quota && !login && onboarding) {
    const r=onboarding.getBoundingClientRect(); result.point={x:r.x+r.width/2,y:r.y+r.height/2};
  } else if (__CLICK__ === 'start' && !verification && !quota && !login && voice) result.point={x:voice.x,y:voice.y};
  if (__CLICK__ === 'end' && end) result.point={x:end.x,y:end.y};
  return result;
})()"""


def voice_inspector_script(click: str | None = None) -> str:
    """Generate one consistent DOM inspection and coordinate-selection script."""
    if click not in (None, "start", "end"):
        raise ValueError("Unknown voice button action")
    return VOICE_CONTROLS_JS.replace("__CLICK__", json.dumps(click))
