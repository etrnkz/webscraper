#!/bin/bash
# Read exact Hydrogram set_bot_commands + BotCommand API from the deployed venv
V=/root/Dev/WebScraper/venv/lib/python3.13/site-packages/hydrogram

echo "===== set_bot_commands.py ====="
cat "$V/methods/bots/set_bot_commands.py" 2>&1

echo ""
echo "===== bot_command.py ====="
cat "$V/types/bots_and_keyboards/bot_command.py" 2>&1

echo ""
echo "===== is set_bot_commands on Client? ====="
grep -rn "set_bot_commands" "$V/client.py" "$V/methods/bots/__init__.py" 2>&1 | head -5

echo ""
echo "===== hydrogram version ====="
cat "$V/__init__.py" 2>&1 | grep -i "__version__" | head -3

echo ""
echo "===== DONE ====="
