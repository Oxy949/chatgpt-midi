"""Errors shared by the browser transport and UI adapter."""


class BrowserError(RuntimeError):
    pass


class VoicePageError(BrowserError):
    """The visible page requires user action; transport retries cannot fix it."""


class BrowserVerificationRequired(VoicePageError):
    """Keep the browser available for a manual verification step."""
