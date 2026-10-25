from __future__ import annotations


def friendly_device_label(user_agent: str | None) -> str:
    """Return a conservative browser/OS label without guessing a device model."""
    value = user_agent or ""
    if "Edg/" in value:
        browser = "Edge"
    elif "OPR/" in value or "Opera/" in value:
        browser = "Opera"
    elif "Chrome/" in value or "CriOS/" in value:
        browser = "Chrome"
    elif "Firefox/" in value or "FxiOS/" in value:
        browser = "Firefox"
    elif "Safari/" in value and "Chrome/" not in value and "Chromium/" not in value:
        browser = "Safari"
    else:
        browser = "Browser"

    if "Windows" in value:
        system = "Windows"
    elif "Android" in value:
        system = "Android"
    elif "iPhone" in value or "iPad" in value:
        system = "iPhone or iPad"
    elif "Macintosh" in value or "Mac OS X" in value:
        system = "Mac"
    elif "Linux" in value:
        system = "Linux"
    else:
        system = "this device"
    return f"{browser} on {system}"
