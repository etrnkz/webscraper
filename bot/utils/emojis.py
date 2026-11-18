"""Premium emoji registry for Web Clone."""

PREMIUM_IDS = {
    "WEB": ("🕸", "5206456392407341290"),      # top welcome
    "STAR": ("⭐️", "5319156402973849743"),    # feature list (repeated)
}


def pe(name):
    char, eid = PREMIUM_IDS[name]
    return char, eid
