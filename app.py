import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import subprocess
import time
import requests
import threading
import json
import os

# ================= CONFIG =================
BOT_TOKEN = "8970620272:AAE91-X9nNoJRS4mA_Qyd6OSF-Pa9a6EqwQ"
bot = telebot.TeleBot(BOT_TOKEN)

AD_URL      = ""
AD_INTERVAL = 15 * 60
AD_DURATION = 15

# ================= STORAGE =================
DATA_FILE    = "bot_data.json"
user_pages   = {}
active_page  = {}
user_streams = {}
user_m3u8    = {}
ad_set_time  = 0.0

# القائمة الرئيسية للتحقق من الأزرار ومنع تداخل الاستجابة
MAIN_BUTTONS = [
    "📺 قائمة القنوات", "📄 الصفحات", "🎬 بدء البث", "🛑 إيقاف بث",
    "🛑✖️ إيقاف الكل", "📊 حالة البثوث", "📢 إعدادات الإعلان",
    "🗑️ حذف قناة", "🗑️ حذف توكن", "🔧 تثبيت FFmpeg", "ℹ️ مساعدة"
]

# ================= SAVE / LOAD =================
def save_data():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "user_pages":  {str(k): v for k, v in user_pages.items()},
                "user_m3u8":   {str(k): v for k, v in user_m3u8.items()},
                "active_page": {str(k): v for k, v in active_page.items()},
                "AD_URL":      AD_URL,
                "AD_INTERVAL": AD_INTERVAL,
                "AD_DURATION": AD_DURATION
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[save_data error] {e}")

def load_data():
    global user_pages, user_m3u8, active_page, AD_URL, AD_INTERVAL, AD_DURATION
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        user_pages  = {int(k): v for k, v in d.get("user_pages", {}).items()}
        user_m3u8   = {int(k): v for k, v in d.get("user_m3u8",  {}).items()}
        active_page = {int(k): v for k, v in d.get("active_page",{}).items()}
        AD_URL      = d.get("AD_URL", "")
        AD_INTERVAL = d.get("AD_INTERVAL", 15 * 60)
        AD_DURATION = d.get("AD_DURATION", 15)
        print(f"[load_data] {len(user_pages)} users loaded")
    except Exception as e:
        print(f"[load_data error] {e}")

# ================= MENUS =================
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("📺 قائمة القنوات"),
        KeyboardButton("📄 الصفحات"),
        KeyboardButton("🎬 بدء البث"),
        KeyboardButton("🛑 إيقاف بث"),
        KeyboardButton("🛑✖️ إيقاف الكل"),
        KeyboardButton("📊 حالة البثوث"),
        KeyboardButton("📢 إعدادات الإعلان"),
        KeyboardButton("🗑️ حذف قناة"),
        KeyboardButton("🗑️ حذف توكن"),
        KeyboardButton("🔧 تثبيت FFmpeg"),
        KeyboardButton("ℹ️ مساعدة")
    )
    return markup

# ================= FFMPEG =================
def check_ffmpeg():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except:
        return False

def install_ffmpeg_via_apt():
    try:
        subprocess.run(['apt', 'update'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        result = subprocess.run(['apt', 'install', '-y', 'ffmpeg'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode == 0
    except:
        return False

# ================= DASH FIX =================
def fix_dash_url(url):
    if not url:
        return None
    if "scontent-" in url and ".fbcdn.net" in url:
        end = url.find(".fbcdn.net")
        return "https://video.xx.fbcdn.net" + url[end + len(".fbcdn.net"):]
    return url

# ================= LAUNCH FFMPEG =================
def launch_ffmpeg(source, stream_url):
    input_args = []
    url = source.lower()

    if url.startswith("rtsp://"):
        input_args = ["-rtsp_transport", "tcp"]
    elif url.startswith("http") and (".m3u8" in url or "m3u8" in url):
        input_args = [
            "-reconnect", "1",
            "-reconnect_at_eof", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
    elif url.startswith("http"):
        input_args = [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]

    cmd = (
        ["ffmpeg", "-re"]
        + input_args
        + ["-i", source,
           "-c:v", "copy",
           "-c:a", "aac",
           "-b:a", "128k",
           "-f", "flv",
           stream_url]
    )
    print(f"[ffmpeg] {' '.join(cmd)}")
    return subprocess.Popen(cmd, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

# ================= FACEBOOK =================
def get_new_stream(chat_id):
    page_name = active_page.get(chat_id)
    if not page_name:
        return None, None, None, None
    page = user_pages[chat_id][page_name]
    r = requests.post(
        f"https://graph.facebook.com/v17.0/{page['page_id']}/live_videos",
        params={"access_token": page["token"], "status": "UNPUBLISHED",
                "title": "Live Preview", "description": "Preview stream"}
    ).json()
    if "id" not in r:
        return None, None, None, None
    live_id = r["id"]
    info = requests.get(
        f"https://graph.facebook.com/v17.0/{live_id}",
        params={"access_token": page["token"], "fields": "stream_url,dash_preview_url"}
    ).json()
    return info.get("stream_url"), live_id, fix_dash_url(info.get("dash_preview_url")), page["token"]

# ================= STREAM THREAD =================
def stream_thread(chat_id, source, name):
    stream_url, live_id, dash, token = get_new_stream(chat_id)
    if not stream_url:
        bot.send_message(chat_id, "❌ فشل إنشاء البث.")
        return

    bot.send_message(chat_id, f"✅ بدأ البث\n🎥 {name}")

    def send_dash_later():
        for attempt in range(3):
            time.sleep(20)
            try:
                info = requests.get(
                    f"https://graph.facebook.com/v17.0/{live_id}",
                    params={"access_token": token, "fields": "stream_url,dash_preview_url"}
                ).json()
                fresh_dash = fix_dash_url(info.get("dash_preview_url"))
                rtmps      = info.get("stream_url") or stream_url
                if fresh_dash:
                    bot.send_message(
                        chat_id,
                        f"🎥 *{name}*\n\n📡 RTMPS:\n`{rtmps}`\n\n👁️ DASH:\n`{fresh_dash}`",
                        parse_mode="Markdown"
                    )
                    return
            except:
                pass
        bot.send_message(chat_id, f"🎥 *{name}*\n\n📡 RTMPS:\n`{stream_url}`", parse_mode="Markdown")

    threading.Thread(target=send_dash_later, daemon=True).start()

    user_streams.setdefault(chat_id, {})[name] = {
        "proc": None, "live_id": live_id, "token": token, "active": True,
        "start_time": time.time(), "source": source, "stream_url": stream_url
    }

    last_ad_time = time.time()
    restart_delay = 3      
    max_restarts  = 10     
    restart_count = 0

    proc = launch_ffmpeg(source, stream_url)
    user_streams[chat_id][name]["proc"] = proc

    while user_streams.get(chat_id, {}).get(name, {}).get("active", False):

        if AD_URL and ad_set_time > last_ad_time:
            last_ad_time = ad_set_time

        elapsed = time.time() - last_ad_time

        if AD_URL and elapsed >= AD_INTERVAL:
            proc = user_streams[chat_id][name].get("proc")
            if proc:
                proc.kill()
                try: proc.wait(timeout=3)
                except: pass

            if not user_streams.get(chat_id, {}).get(name, {}).get("active", False):
                break

            ad_proc = launch_ffmpeg(AD_URL, stream_url)
            user_streams[chat_id][name]["proc"] = ad_proc

            deadline = time.time() + AD_DURATION
            while time.time() < deadline:
                if not user_streams.get(chat_id, {}).get(name, {}).get("active", False):
                    break
                if ad_proc.poll() is not None:
                    break
                time.sleep(0.5)

            ad_proc.kill()
            try: ad_proc.wait(timeout=3)
            except: pass

            last_ad_time = time.time()

            if not user_streams.get(chat_id, {}).get(name, {}).get("active", False):
                break

            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc
            restart_count = 0

        else:
            proc = user_streams[chat_id][name].get("proc")

            if proc is None or proc.poll() is not None:
                if restart_count >= max_restarts:
                    bot.send_message(chat_id, f"⚠️ فشل البث بعد {max_restarts} محاولة: {name}")
                    break

                time.sleep(restart_delay)

                if not user_streams.get(chat_id, {}).get(name, {}).get("active", False):
                    break

                restart_count += 1
                print(f"[stream_thread] إعادة تشغيل #{restart_count} للقناة: {name}")
                proc = launch_ffmpeg(source, stream_url)
                user_streams[chat_id][name]["proc"] = proc
            else:
                restart_count = 0
                time.sleep(2)

    proc = user_streams.get(chat_id, {}).get(name, {}).get("proc")
    if proc:
        proc.kill()
    user_streams.get(chat_id, {}).pop(name, None)

# ================= STOP =================
def stop_stream(chat_id, name, notify=True):
    info = user_streams.get(chat_id, {}).get(name)
    if not info:
        return
    info["active"] = False
    try:
        if info.get("proc"): info["proc"].kill()
        requests.delete(
            f"https://graph.facebook.com/v17.0/{info['live_id']}",
            params={"access_token": info["token"]}
        )
    except: pass
    user_streams.get(chat_id, {}).pop(name, None)
    if notify:
        bot.send_message(chat_id, f"🛑 تم إيقاف: {name}")

# ================= /start =================
@bot.message_handler(commands=["start"])
def start_cmd(msg):
    bot.send_message(
        msg.chat.id,
        "👋 *مرحباً بك في بوت البث المباشر*\n\nاستخدم الأزرار أدناه للتحكم الكامل.",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ================= PAGE COMMANDS =================
@bot.message_handler(commands=["addpage"])
def add_page(msg):
    try:
        _, name, page_id, token = msg.text.split(maxsplit=3)
    except:
        bot.send_message(msg.chat.id,
            "📝 *إضافة صفحة*\n\nالصيغة:\n`/addpage الاسم PAGE_ID TOKEN`",
            parse_mode="Markdown")
        return
    user_pages.setdefault(msg.chat.id, {})[name] = {"page_id": page_id, "token": token}
    save_data()
    bot.send_message(msg.chat.id, f"✅ تمت إضافة الصفحة: *{name}*", parse_mode="Markdown")

@bot.message_handler(commands=["savem3u8"])
def save_m3u8(msg):
    try:
        _, name, url = msg.text.split(maxsplit=2)
    except:
        bot.send_message(msg.chat.id,
            "📝 *إضافة قناة*\n\nالصيغة:\n`/savem3u8 الاسم الرابط`",
            parse_mode="Markdown")
        return
    user_m3u8.setdefault(msg.chat.id, {})[name] = url
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم حفظ القناة: *{name}*", parse_mode="Markdown")

# ================= AD COMMANDS =================
@bot.message_handler(commands=["setad"])
def set_ad(msg):
    global AD_URL, ad_set_time
    try:
        _, url = msg.text.split(maxsplit=1)
        url = url.strip()
        if url.lower() == "off":
            AD_URL = ""
            bot.send_message(msg.chat.id, "🔕 تم إيقاف الإعلان.")
        else:
            AD_URL = url
            ad_set_time = time.time()
            save_data()
            bot.send_message(msg.chat.id, "✅ تم تعيين رابط الإعلان.")
    except:
        bot.send_message(msg.chat.id,
            "📝 الصيغة: `/setad الرابط`", parse_mode="Markdown")

@bot.message_handler(commands=["setinterval"])
def set_interval(msg):
    global AD_INTERVAL
    try:
        _, minutes = msg.text.split(maxsplit=1)
        AD_INTERVAL = int(minutes) * 60
        save_data()
        bot.send_message(msg.chat.id, f"✅ الإعلان كل *{AD_INTERVAL // 60}* دقيقة", parse_mode="Markdown")
    except:
        bot.send_message(msg.chat.id,
            f"📝 الصيغة: `/setinterval الدقائق`\nالحالي: {AD_INTERVAL // 60} دقيقة", parse_mode="Markdown")

@bot.message_handler(commands=["setduration"])
def set_duration(msg):
    global AD_DURATION
    try:
        _, secs = msg.text.split(maxsplit=1)
        AD_DURATION = int(secs)
        save_data()
        bot.send_message(msg.chat.id, f"✅ مدة الإعلان: *{AD_DURATION}* ثانية", parse_mode="Markdown")
    except:
        bot.send_message(msg.chat.id,
            f"📝 الصيغة: `/setduration الثواني`\nالحالي: {AD_DURATION} ثانية", parse_mode="Markdown")

# ================= TXT IMPORT =================
@bot.message_handler(content_types=["document"])
def handle_txt(msg):
    if not msg.document.file_name.lower().endswith(".txt"):
        return
    file_info = bot.get_file(msg.document.file_id)
    file_url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    content   = requests.get(file_url).text
    user_m3u8.setdefault(msg.chat.id, {})
    count = 0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            name, url = line.split(maxsplit=1)
            if url.startswith("http"):
                user_m3u8[msg.chat.id][name] = url
                count += 1
        except:
            pass
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم استيراد *{count}* قناة", parse_mode="Markdown", reply_markup=main_menu())

# ================= STOP ONE =================
@bot.message_handler(commands=["stop"])
def stop_one(msg):
    try:
        _, name = msg.text.split(maxsplit=1)
    except:
        bot.send_message(msg.chat.id, "الاستخدام: `/stop الاسم`", parse_mode="Markdown")
        return
    if name not in user_streams.get(msg.chat.id, {}):
        bot.send_message(msg.chat.id, f"❌ لا يوجد بث نشط: {name}")
        return
    stop_stream(msg.chat.id, name)

# ================= BUTTON HANDLERS =================

@bot.message_handler(func=lambda m: m.text == "📺 قائمة القنوات")
def btn_channels(m):
    data = user_m3u8.get(m.chat.id)
    if not data:
        bot.send_message(m.chat.id,
            "❌ لا توجد قنوات محفوظة.\n\nأضف قناة:\n`/savem3u8 الاسم الرابط`\n\nأو أرسل ملف `.txt`",
            parse_mode="Markdown", reply_markup=main_menu())
        return
    active = user_streams.get(m.chat.id, {})
    txt = f"📺 *القنوات المحفوظة ({len(data)}):*\n\n"
    for n in data:
        txt += f"{'🟢' if n in active else '⭕'} `{n}`\n"
    bot.send_message(m.chat.id, txt, parse_mode="Markdown", reply_markup=main_menu())

# ================= PAGES BUTTON =================
@bot.message_handler(func=lambda m: m.text == "📄 الصفحات")
def btn_pages(m):
    pages = user_pages.get(m.chat.id, {})
    if not pages:
        bot.send_message(m.chat.id,
            "❌ لا توجد صفحات.\n\nأضف صفحة:\n`/addpage الاسم PAGE_ID TOKEN`",
            parse_mode="Markdown", reply_markup=main_menu())
        return
    current = active_page.get(m.chat.id, "—")
    markup  = InlineKeyboardMarkup(row_width=1)
    for pname in pages:
        label = f"✅ {pname}" if pname == current else f"📄 {pname}"
        markup.add(InlineKeyboardButton(label, callback_data=f"pg_{pname[:55]}"))
    bot.send_message(m.chat.id,
        f"📄 *الصفحات* — النشطة: `{current}`\n\nاضغط لتفعيل صفحة:",
        reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("pg_"))
def cb_select_page(call):
    short   = call.data[3:]
    chat_id = call.message.chat.id
    pages   = user_pages.get(chat_id, {})
    name    = next((p for p in pages if p[:55] == short), None)
    if not name:
        bot.answer_callback_query(call.id, "❌ غير موجودة")
        return
    active_page[chat_id] = name
    bot.answer_callback_query(call.id, f"✅ {name}")
    markup = InlineKeyboardMarkup(row_width=1)
    for pname in pages:
        label = f"✅ {pname}" if pname == name else f"📄 {pname}"
        markup.add(InlineKeyboardButton(label, callback_data=f"pg_{pname[:55]}"))
    bot.edit_message_text(
        f"📄 *الصفحات* — النشطة: `{name}`\n\nاضغط لتفعيل صفحة:",
        chat_id=chat_id, message_id=call.message.message_id,
        reply_markup=markup, parse_mode="Markdown"
    )

# ================= DELETE CHANNEL BUTTON =================
@bot.message_handler(func=lambda m: m.text == "🗑️ حذف قناة")
def btn_delete_channel(m):
    data = user_m3u8.get(m.chat.id, {})
    if not data:
        bot.send_message(m.chat.id, "❌ لا توجد قنوات محفوظة.", reply_markup=main_menu())
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for name in data:
        markup.add(InlineKeyboardButton(f"🗑️ {name}", callback_data=f"delch_{name[:55]}"))
    markup.add(InlineKeyboardButton("🗑️ حذف الكل", callback_data="delch_ALL"))
    bot.send_message(m.chat.id, "🗑️ *اختر القناة للحذف:*", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("delch_"))
def cb_delete_channel(call):
    chat_id = call.message.chat.id
    key     = call.data[6:]  

    if key == "ALL":
        count = len(user_m3u8.get(chat_id, {}))
        user_m3u8[chat_id] = {}
        save_data()
        bot.answer_callback_query(call.id, f"✅ تم حذف {count} قناة")
        bot.edit_message_text(f"🗑️ تم حذف جميع القنوات ({count})", chat_id=chat_id,
                              message_id=call.message.message_id)
        return

    data = user_m3u8.get(chat_id, {})
    name = next((n for n in data if n[:55] == key), None)
    if not name:
        bot.answer_callback_query(call.id, "❌ غير موجودة")
        return

    if name in user_streams.get(chat_id, {}):
        stop_stream(chat_id, name, notify=False)

    del user_m3u8[chat_id][name]
    save_data()
    bot.answer_callback_query(call.id, f"✅ تم حذف: {name}")

    remaining = user_m3u8.get(chat_id, {})
    if not remaining:
        bot.edit_message_text("🗑️ تم حذف جميع القنوات.", chat_id=chat_id,
                              message_id=call.message.message_id)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for n in remaining:
        markup.add(InlineKeyboardButton(f"🗑️ {n}", callback_data=f"delch_{n[:55]}"))
    markup.add(InlineKeyboardButton("🗑️ حذف الكل", callback_data="delch_ALL"))
    bot.edit_message_text("🗑️ *اختر القناة للحذف:*", chat_id=chat_id,
                          message_id=call.message.message_id,
                          reply_markup=markup, parse_mode="Markdown")

# ================= DELETE TOKEN BUTTON =================
@bot.message_handler(func=lambda m: m.text == "🗑️ حذف توكن")
def btn_delete_token(m):
    pages = user_pages.get(m.chat.id, {})
    if not pages:
        bot.send_message(m.chat.id, "❌ لا توجد صفحات محفوظة.", reply_markup=main_menu())
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for pname in pages:
        markup.add(InlineKeyboardButton(f"🗑️ {pname}", callback_data=f"delpg_{pname[:55]}"))
    markup.add(InlineKeyboardButton("🗑️ حذف الكل", callback_data="delpg_ALL"))
    bot.send_message(m.chat.id, "🗑️ *اختر التوكن للحذف:*", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("delpg_"))
def cb_delete_token(call):
    chat_id = call.message.chat.id
    key     = call.data[6:]  

    if key == "ALL":
        count = len(user_pages.get(chat_id, {}))
        user_pages[chat_id]  = {}
        active_page.pop(chat_id, None)
        save_data()
        bot.answer_callback_query(call.id, f"✅ تم حذف {count} توكن")
        bot.edit_message_text(f"🗑️ تم حذف جميع التوكنات ({count})", chat_id=chat_id,
                              message_id=call.message.message_id)
        return

    pages = user_pages.get(chat_id, {})
    name  = next((p for p in pages if p[:55] == key), None)
    if not name:
        bot.answer_callback_query(call.id, "❌ غير موجودة")
        return

    del user_pages[chat_id][name]

    if active_page.get(chat_id) == name:
        active_page.pop(chat_id, None)

    save_data()
    bot.answer_callback_query(call.id, f"✅ تم حذف: {name}")

    remaining = user_pages.get(chat_id, {})
    if not remaining:
        bot.edit_message_text("🗑️ تم حذف جميع التوكنات.", chat_id=chat_id,
                              message_id=call.message.message_id)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for p in remaining:
        markup.add(InlineKeyboardButton(f"🗑️ {p}", callback_data=f"delpg_{p[:55]}"))
    markup.add(InlineKeyboardButton("🗑️ حذف الكل", callback_data="delpg_ALL"))
    bot.edit_message_text("🗑️ *اختر التوكن للحذف:*", chat_id=chat_id,
                          message_id=call.message.message_id,
                          reply_markup=markup, parse_mode="Markdown")

# ================= START STREAM BUTTON =================
@bot.message_handler(func=lambda m: m.text == "🎬 بدء البث")
def btn_start_stream(m):
    pages = user_pages.get(m.chat.id, {})
    if not pages:
        bot.send_message(m.chat.id,
            "❌ لا توجد صفحات.\nأضف صفحة أولاً:\n`/addpage الاسم PAGE_ID TOKEN`",
            parse_mode="Markdown", reply_markup=main_menu())
        return
    if not user_m3u8.get(m.chat.id):
        bot.send_message(m.chat.id,
            "❌ لا توجد قنوات.\nأضف قناة:\n`/savem3u8 الاسم الرابط`\nأو أرسل ملف `.txt`",
            parse_mode="Markdown", reply_markup=main_menu())
        return
    current = active_page.get(m.chat.id, "—")
    markup  = InlineKeyboardMarkup(row_width=1)
    for pname in pages:
        label = f"✅ {pname}" if pname == current else f"📄 {pname}"
        markup.add(InlineKeyboardButton(label, callback_data=f"sb_{pname[:55]}"))
    bot.send_message(m.chat.id,
        f"🔑 *اختر التوكن للبث:*\nالحالية: `{current}`",
        reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda c: c.data.startswith("sb_"))
def cb_select_page_stream(call):
    short   = call.data[3:]
    chat_id = call.message.chat.id
    pages   = user_pages.get(chat_id, {})
    name    = next((p for p in pages if p[:55] == short), None)
    if not name:
        bot.answer_callback_query(call.id, "❌ غير موجودة")
        return
    active_page[chat_id] = name
    save_data()
    bot.answer_callback_query(call.id, f"✅ {name}")
    bot.edit_message_text(
        f"🎬 *بدء البث*\n🔑 التوكن: `{name}`\n\nأرسل أسماء القنوات (كل اسم في سطر):",
        chat_id=chat_id, message_id=call.message.message_id, parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_streams)

def process_streams(msg):
    # حل المشكلة: إذا ضغط المستخدم على زر آخر بدلاً من كتابة الأسماء، يتم إلغاء الخطوة فوراً وتمرير الزر لوظيفته الأصلية
    if msg.text in MAIN_BUTTONS:
        bot.clear_step_handler_by_chat_id(msg.chat.id)
        # إعادة توجيه الرسالة داخلياً للـ handlers المخصصة للأزرار
        bot.process_new_messages([msg])
        return

    saved   = user_m3u8.get(msg.chat.id, {})
    started, already, not_found = 0, [], []

    for name in msg.text.splitlines():
        name = name.strip()
        if not name:
            continue
        if name not in saved:
            not_found.append(name)
            continue
        if name in user_streams.get(msg.chat.id, {}):
            already.append(name)
            continue
        threading.Thread(
            target=stream_thread,
            args=(msg.chat.id, saved[name], name),
            daemon=True
        ).start()
        started += 1
        time.sleep(1)

    parts = []
    if started:   parts.append(f"🚀 تم تشغيل *{started}* بث")
    if already:   parts.append(f"⚠️ يعمل مسبقاً: {', '.join(already)}")
    if not_found: parts.append(f"❌ غير موجود: {', '.join(not_found)}")
    bot.send_message(msg.chat.id, "\n".join(parts) or "❌ لا شيء", parse_mode="Markdown", reply_markup=main_menu())

# ================= STOP BUTTON =================
@bot.message_handler(func=lambda m: m.text == "🛑 إيقاف بث")
def btn_stop(m):
    streams = user_streams.get(m.chat.id, {})
    if not streams:
        bot.send_message(m.chat.id, "❌ لا توجد بثوث نشطة.", reply_markup=main_menu())
        return
    markup = InlineKeyboardMarkup(row_width=1)
    for name in streams:
        markup.add(InlineKeyboardButton(f"🛑 {name}", callback_data=f"st_{name[:55]}"))
    bot.send_message(m.chat.id, "🛑 اختر البث للإيقاف:", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("st_"))
def cb_stop(call):
    short   = call.data[3:]
    chat_id = call.message.chat.id
    name    = next((n for n in user_streams.get(chat_id, {}) if n[:55] == short), None)
    if name:
        stop_stream(chat_id, name)
    bot.answer_callback_query(call.id)

# ================= STOP ALL BUTTON =================
@bot.message_handler(func=lambda m: m.text == "🛑✖️ إيقاف الكل")
def btn_stopall(m):
    streams = user_streams.get(m.chat.id)
    if not streams:
        bot.send_message(m.chat.id, "❌ لا توجد بثوث نشطة.", reply_markup=main_menu())
        return
    names = list(streams.keys())
    for name in names:
        stop_stream(m.chat.id, name, notify=False)
    bot.send_message(m.chat.id, f"🛑 تم إيقاف *{len(names)}* بث", parse_mode="Markdown", reply_markup=main_menu())

# ================= STATUS BUTTON =================
@bot.message_handler(func=lambda m: m.text == "📊 حالة البثوث")
def btn_status(m):
    streams = user_streams.get(m.chat.id, {})
    if not streams:
        bot.send_message(m.chat.id, "📊 لا توجد بثوث نشطة.", reply_markup=main_menu())
        return
    now = time.time()
    txt = f"📊 *البثوث النشطة ({len(streams)}):*\n\n"
    for name, info in streams.items():
        elapsed = int(now - info.get("start_time", now))
        h, rem  = divmod(elapsed, 3600)
        mn, s   = divmod(rem, 60)
        dur     = f"{h}س {mn}د" if h else f"{mn}د {s}ث"
        proc    = info.get("proc")
        st      = "🟢" if (proc and proc.poll() is None) else "🟡"
        txt    += f"{st} *{name}* — {dur}\n`{info.get('live_id','—')}`\n\n"
    bot.send_message(m.chat.id, txt, parse_mode="Markdown", reply_markup=main_menu())

# ================= AD SETTINGS BUTTON =================
@bot.message_handler(func=lambda m: m.text == "📢 إعدادات الإعلان")
def btn_ad_settings(m):
    status = f"🔗 `{AD_URL}`" if AD_URL else "🔕 *غير مفعّل*"
    bot.send_message(
        m.chat.id,
        f"📢 *إعدادات الإعلان*\n\n"
        f"الرابط: {status}\n"
        f"الفترة: *{AD_INTERVAL // 60}* دقيقة\n"
        f"المدة: *{AD_DURATION}* ثانية\n\n"
        f"الأوامر:\n"
        f"`/setad الرابط` — تعيين رابط الإعلان\n"
        f"`/setad off` — إيقاف الإعلان\n"
        f"`/setinterval الدقائق` — تغيير الفترة\n"
        f"`/setduration الثواني` — تغيير المدة",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ================= FFMPEG INSTALL BUTTON =================
@bot.message_handler(func=lambda m: m.text == "🔧 تثبيت FFmpeg")
def btn_install_ffmpeg(m):
    if check_ffmpeg():
        bot.send_message(m.chat.id, "✅ FFmpeg مثبّت بالفعل.", reply_markup=main_menu())
        return
    msg = bot.send_message(m.chat.id, "⏳ جارٍ تثبيت FFmpeg...")
    ok  = install_ffmpeg_via_apt()
    if ok:
        bot.edit_message_text("✅ تم تثبيت FFmpeg بنجاح.", chat_id=m.chat.id, message_id=msg.message_id)
    else:
        bot.edit_message_text("❌ فشل تثبيت FFmpeg. جرّب يدوياً: `apt install ffmpeg`",
                              chat_id=m.chat.id, message_id=msg.message_id, parse_mode="Markdown")

# ================= HELP BUTTON =================
@bot.message_handler(func=lambda m: m.text == "ℹ️ مساعدة")
def btn_help(m):
    bot.send_message(
        m.chat.id,
        "ℹ️ *دليل الاستخدام*\n\n"
        "*إضافة صفحة فيسبوك:*\n`/addpage الاسم PAGE_ID TOKEN`\n\n"
        "*إضافة قناة:*\n`/savem3u8 الاسم الرابط`\n\n"
        "*استيراد قنوات:*\nأرسل ملف `.txt` (كل سطر: `الاسم الرابط`)\n\n"
        "*بدء البث:*\nاضغط 🎬 ثم اختر التوكن ثم أرسل أسماء القنوات\n\n"
        "*إيقاف بث:*\nاضغط 🛑 واختر البث\n\n"
        "*حذف قناة:*\nاضغط 🗑️ حذف قناة\n\n"
        "*حذف توكن:*\nاضغط 🗑️ حذف توكن\n\n"
        "*إعدادات الإعلان:*\n"
        "`/setad الرابط` — تعيين رابط\n"
        "`/setad off` — إيقاف\n"
        "`/setinterval 30` — كل 30 دقيقة\n"
        "`/setduration 15` — مدة 15 ثانية",
        parse_mode="Markdown",
        reply_markup=main_menu()
    )

# ================= MAIN =================
load_data()
print("🤖 البوت يعمل...")
bot.infinity_polling(timeout=30, long_polling_timeout=20)
