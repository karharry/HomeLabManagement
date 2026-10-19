"""'Is there a newer release?' against GitHub Releases. Only tells you; it never downloads anything."""
from __future__ import annotations

import re

import requests
from PySide6.QtCore import QThread, Signal

from version import __version__, GITHUB_REPO


def _ver(s: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3])


def newer(latest: str, current: str = __version__) -> bool:
    return _ver(latest) > _ver(current)


class UpdateCheck(QThread):
    """Emits (latest_tag, url) when a newer release exists, or ("", "") when up to date.
    Emits (error_message, "") prefixed with "!" if the check itself failed."""
    result = Signal(str, str)

    def run(self):
        if not GITHUB_REPO:
            self.result.emit("!No GitHub repository is configured in version.py.", "")
            return
        try:
            r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=10)
            r.raise_for_status()
            d = r.json()
            tag = d.get("tag_name", "")
            self.result.emit(tag if newer(tag) else "", d.get("html_url", ""))
        except Exception as e:
            self.result.emit(f"!{e}", "")
