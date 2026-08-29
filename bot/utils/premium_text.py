from hydrogram.enums import MessageEntityType
from hydrogram.types import MessageEntity
from bot.utils import emojis

def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2

class PremiumText:
    def __init__(self):
        self._parts = []
        self._entities = []
        self._offset = 0

    def add(self, text):
        self._parts.append(text)
        self._offset += _utf16_len(text)
        return self

    def bold(self, text):
        start = self._offset
        self._parts.append(text)
        self._offset += _utf16_len(text)
        if text:
            self._entities.append(MessageEntity(type=MessageEntityType.BOLD, offset=start, length=_utf16_len(text)))
        return self

    def italic(self, text):
        start = self._offset
        self._parts.append(text)
        self._offset += _utf16_len(text)
        if text:
            self._entities.append(MessageEntity(type=MessageEntityType.ITALIC, offset=start, length=_utf16_len(text)))
        return self

    def emoji(self, name):
        char, eid = emojis.pe(name)
        self._parts.append(char)
        if eid:
            self._entities.append(MessageEntity(
                type=MessageEntityType.CUSTOM_EMOJI,
                offset=self._offset,
                length=_utf16_len(char),
                custom_emoji_id=int(eid),
            ))
        self._offset += _utf16_len(char)
        return self

    def build(self):
        text = "".join(self._parts)
        return text, self._entities or None
