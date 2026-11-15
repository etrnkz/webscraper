#!/bin/bash
# Inspect Hydrogram BotCommandScope variants and BotCommandScopeChat signature
V=/root/Dev/WebScraper/venv/lib/python3.13/site-packages/hydrogram

echo "===== bot_command_scope files ====="
ls "$V/types/bots_and_keyboards/" | grep -i command_scope

echo ""
echo "===== bot_command_scope_chat.py ====="
cat "$V/types/bots_and_keyboards/bot_command_scope_chat.py" 2>&1 | tail -60

echo ""
echo "===== bot_command_scope_users.py ====="
cat "$V/types/bots_and_keyboards/bot_command_scope_users.py" 2>&1 | tail -50

echo ""
echo "===== exports in types/__init__.py ====="
grep -n "BotCommandScope" "$V/types/__init__.py" | head -20

echo ""
echo "===== raw SetBotCommands lang_code type ====="
grep -rn "class SetBotCommands" "$V/raw/functions/bots/" | head -3

echo ""
echo "===== DONE ====="
