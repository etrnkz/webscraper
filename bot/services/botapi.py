"""Bot API premium markup upgrade — mirrors AltaMovies pattern.

Hydrogram 0.2.0 (MTProto layer 181) has no icon field for keyboards.
Bot API does: InlineKeyboardButton.icon_custom_emoji_id
We send via MTProto (plain Unicode fallback) then edit via HTTP.
"""
import logging
import re

import aiohttp

import bot.config as config

API = "https://api.telegram.org"

# Captured via user premium emoji tool
# ℹ️ 4913977035174446493, 💻 4902196816054846269, 💜 5339364726612713759
PREMIUM_ICONS = {
    "ℹ️": "4913977035174446493",
    "ℹ": "4913977035174446493",
    "💻": "4902196816054846269",
    "💜": "5339364726612713759",
}

log = logging.getLogger(__name__)

_LEAD_EMOJI = re.compile(r"^[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]+\s*")


def premium_markup_json(markup):
    rows = getattr(markup, "inline_keyboard", None) or getattr(markup, "keyboard", None)
    if not rows or not config.BOT_TOKEN:
        return None
    out_rows = []
    used = False
    for row in rows:
        out_buttons = []
        for btn in row:
            text = getattr(btn, "text", "") or ""
            button = {"text": text}
            icon_id = None
            if text:
                for char, eid in PREMIUM_ICONS.items():
                    if text.startswith(char):
                        # strip the leading emoji (and optional variation selector spacing)
                        stripped = text[len(char):].lstrip()
                        # also handle the case where char without FE0F was used
                        if not stripped and len(text) > len(char):
                            stripped = _LEAD_EMOJI.sub(" ", text).strip()
                        button["text"] = stripped or " "
                        icon_id = eid
                        break
            if icon_id:
                button["icon_custom_emoji_id"] = icon_id
                used = True
            cb = getattr(btn, "callback_data", None)
            url = getattr(btn, "url", None)
            webapp = getattr(btn, "web_app", None)
            if cb:
                button["callback_data"] = cb if isinstance(cb, str) else cb.decode() if isinstance(cb, bytes) else str(cb)
            elif url:
                button["url"] = url
            elif webapp:
                app_url = getattr(webapp, "url", None)
                if app_url:
                    button["web_app"] = {"url": app_url}
            out_buttons.append(button)
        out_rows.append(out_buttons)
    if not used:
        return None
    return {"inline_keyboard": out_rows}


async def upgrade_premium_markup(message, markup):
    payload = premium_markup_json(markup)
    if not payload or not config.BOT_TOKEN:
        return
    chat_id = getattr(message.chat, "id", None)
    message_id = getattr(message, "id", None)
    if chat_id is None or message_id is None:
        return
    body = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": payload,
    }
    url = f"{API}/bot{config.BOT_TOKEN}/editMessageReplyMarkup"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None)
        if not data.get("ok"):
            log.debug("premium markup upgrade declined: %s", (data.get("description") or "")[:120])
        else:
            log.info("Premium markup upgraded for %s:%s", chat_id, message_id)
    except Exception as e:
        log.debug("premium markup upgrade failed: %s", e)
