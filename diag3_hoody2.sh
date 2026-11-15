#!/bin/bash
# Find any USER (not bot) Telegram session that could drive @wbclnbot as a real user
echo "===== HOODY2: all .session files ====="
find /root -name "*.session" -not -name "*.session-journal" 2>/dev/null | head -30

echo ""
echo "===== HOODY2: hydrogram Client signature (set_bot_commands?) ====="
grep -rn "def set_bot_commands\|async def set_bot_commands" /root/Dev/WebScraper/venv/lib/python3.13/site-packages/hydrogram/ 2>/dev/null | head -5
echo "--- BotCommand type ---"
grep -rn "class BotCommand" /root/Dev/WebScraper/venv/lib/python3.13/site-packages/hydrogram/types/ 2>/dev/null | head -5

echo ""
echo "===== HOODY2: bot API getMyCommands (current) ====="
TOKEN=$(grep -E "^BOT_TOKEN=" /root/Dev/WebScraper/.env | cut -d= -f2- | tr -d '"'"'"' \r')
curl -s "https://api.telegram.org/bot${TOKEN}/getMyCommands" 2>&1 | head -5

echo ""
echo "===== DONE ====="
