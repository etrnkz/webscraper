"""Premium emoji registry for Web Cloner — mirrors AltaMovies pattern."""

PREMIUM_IDS = {
    "WEB": ("🕸", "5206456392407341290"),      # top welcome
    "STAR": ("⭐️", "5319156402973849743"),    # feature list (repeated)
    # legacy button IDs (still used via Bot API icon)
    "INFO": ("ℹ️", "4913977035174446493"),
    "LAPTOP": ("💻", "4902196816054846269"),
    "HEART": ("💜", "5339364726612713759"),
}

_UNI = {
    "WEB": "🕸",
    "STAR": "⭐️",
    "INFO": "ℹ️",
    "LAPTOP": "💻",
    "HEART": "💜",
}

import os
_USE_PREMIUM = os.getenv("PREMIUM_EMOJI", "true").lower() in ("1", "true", "yes", "on")

def pe(name):
    if _USE_PREMIUM and name in PREMIUM_IDS:
        char, eid = PREMIUM_IDS[name]
        return char, eid
    return _UNI.get(name, "•"), None
