#!/bin/bash
# Diagnostic 2: WHO IS THE BOT ACTUALLY? + duplicate processes + DB evidence
echo "===== A. BOT IDENTITY (Bot API getMe) ====="
TOKEN=$(grep -E "^BOT_TOKEN=" /root/Dev/WebScraper/.env | cut -d= -f2- | tr -d '"'"'"' \r')
curl -s "https://api.telegram.org/bot${TOKEN}/getMe" 2>&1 | python3 -c "import sys,json; d=json.load(sys.stdin); r=d.get('result',{}); print('ok=',d.get('ok')); print('username=@'+str(r.get('username'))); print('bot_id=',r.get('id')); print('first_name=',r.get('first_name')); print('can_join_groups=',r.get('can_join_groups'))" 2>&1

echo ""
echo "===== B. SUPERVISOR PROGRAMS (duplicate bot?) ====="
ls -la /etc/supervisor/conf.d/ 2>&1
echo "--- supervisor status ---"
supervisorctl status 2>&1 | head -20

echo ""
echo "===== C. OLD DEPLOY DIRS STILL PRESENT? ====="
ls -la /root/webscraper 2>&1 | head -10
echo "--- any other main.py copies ---"
find /root -maxdepth 4 -name "main.py" -path "*eb*" 2>/dev/null | head -20

echo ""
echo "===== D. ALL LISTENERS / OPEN TELEGRAM CONNECTIONS ====="
ss -tnp 2>/dev/null | grep -E "149\.154|91\.108" | head -20

echo ""
echo "===== E. DB ACTIVITY LOG (crawl evidence) ====="
cd /root/Dev/WebScraper
python3 - <<'PYEOF'
import sqlite3, os
p = "/root/Dev/WebScraper/bot_data.db"
try:
    c = sqlite3.connect(p)
    cur = c.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print("tables:", [r[0] for r in cur.fetchall()])
    for t in ("activity_log", "activity", "users"):
        try:
            cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 15")
            rows = cur.fetchall()
            cur.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in cur.fetchall()]
            print(f"\n--- {t} (cols={cols}) rows={len(rows)} ---")
            for r in rows:
                print(r)
        except Exception as e:
            print(f"({t}: {e})")
except Exception as e:
    print("DB ERROR:", e)
PYEOF

echo ""
echo "===== F. ANY CRAWL OUTPUT DIRS ANYWHERE ====="
find /root -maxdepth 3 -name "clone_*" -type d 2>/dev/null | head -10
find /root -maxdepth 3 -name "clone_*.zip" 2>/dev/null | head -10

echo ""
echo "===== G. LOG: LINES THAT ARE NOT THE RESOURCE-PROBE NOISE ====="
grep -vE "Derived capacity" /root/.pm2/logs/webscraper-error.log 2>/dev/null | tail -40

echo ""
echo "===== H. DONE ====="
