#!/bin/bash
# Diagnostic evidence collector for Hoody #2 webscraper bot
echo "===== 1. PM2 DESCRIBE ====="
pm2 describe webscraper 2>&1 | head -40

echo ""
echo "===== 2. DEPLOYED GIT COMMIT ====="
cd /root/Dev/WebScraper && git log --oneline -3 2>&1
echo "--- git status ---"
git status --porcelain 2>&1 | head -20

echo ""
echo "===== 3. IS DEPLOYED scraper.py MODULE-LEVEL INITIALISED? ====="
grep -n "crawl_queue = CrawlQueue" /root/Dev/WebScraper/bot/core/scraper.py 2>&1
echo "--- crawl_queue is None check line ---"
grep -n "Server starting up" -B4 /root/Dev/WebScraper/bot/core/scraper.py 2>&1
echo "--- TelegramNotifier class line ---"
grep -n "^class TelegramNotifier" /root/Dev/WebScraper/bot/core/scraper.py 2>&1

echo ""
echo "===== 4. ERROR LOG: TRACEBACKS (last 10) ====="
grep -n "Traceback" /root/.pm2/logs/webscraper-error.log 2>&1 | tail -10
echo "--- last 10 distinct error lines ---"
grep -E "Error|Exception|NameError|CRITICAL" /root/.pm2/logs/webscraper-error.log 2>&1 | tail -20

echo ""
echo "===== 5. LAST 25 LOG LINES (error log) ====="
tail -n 25 /root/.pm2/logs/webscraper-error.log 2>&1

echo ""
echo "===== 6. OUT LOG (bot stdout) last 40 ====="
tail -n 40 /root/.pm2/logs/webscraper-out.log 2>&1

echo ""
echo "===== 7. RUNNING PYTHON PROCESSES ====="
ps aux | grep -E "python|main.py" | grep -v grep 2>&1

echo ""
echo "===== 8. ANY SESSION FILES (for user-client testing) ====="
find /root -maxdepth 4 -name "*.session" -o -maxdepth 4 -name "*.session-journal" 2>/dev/null | head -20

echo ""
echo "===== 9. ENV VALUES IN .env ====="
grep -E "CRAWL_|LOG_LEVEL|API_ID|FORCE_|ADMIN" /root/Dev/WebScraper/.env 2>&1 | sed -E 's/(API_ID=).*/\1<redacted>/'

echo ""
echo "===== 10. ACTIVE CLONE DIRS / ZIPS ====="
ls -la /root/Dev/WebScraper/ 2>&1 | grep -E "clone_|\.zip" | head -20
echo "--- count ---"
ls -d /root/Dev/WebScraper/clone_* 2>/dev/null | wc -l

echo ""
echo "===== 11. SYSTEMD SERVICES (duplicate bot?) ====="
systemctl list-units --type=service --state=running 2>&1 | grep -iE "webscraper|bot|python" | head -10

echo ""
echo "===== 12. PLAYWRIGHT CHROMIUM PRESENT? ====="
ls -la /root/.cache/ms-playwright/ 2>&1

echo ""
echo "===== 13. DB STATE ====="
ls -la /root/Dev/WebScraper/*.db /root/Dev/WebScraper/*.sqlite* 2>&1 | head -5

echo ""
echo "===== DONE ====="
