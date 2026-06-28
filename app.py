import telebot
from telebot import types
import subprocess
import time
import requests
import threading
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

# ================= CONFIG =================
BOT_TOKEN = "8935584921:AAGMjeS6CsBw0hXIf0Rbu9nbQbY3n1hfw4k"
bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = "data.json"

# ================= STORAGE & JSON MECHANISM =================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"pages": {}, "channels": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"pages": {}, "channels": {}}

def save_data():
    data = {
        "pages": user_pages,
        "channels": user_m3u8
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# تحميل البيانات عند الإقلاع
data_store = load_data()
user_pages = data_store.get("pages", {})
user_m3u8 = data_store.get("channels", {})

# متغيرات الجلسة المؤقتة
active_page = {}
user_streams = {}
user_waiting_count = {} # لتخزين بيانات القنوات التي تنتظر تحديد عدد التكرار

# ================= MAIN KEYBOARD BUTTONS =================
def get_main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_test_dash = types.KeyboardButton("📊 فحص الـ DASH")
    btn_test_m3u8 = types.KeyboardButton("📺 فحص القنوات المحفوظة")
    btn_check_tokens = types.KeyboardButton("🔑 التحقق من التوكنات")
    btn_list_m3u8 = types.KeyboardButton("📋 عرض القنوات المحفوظة")
    btn_stop_all = types.KeyboardButton("🛑 إيقاف جميع البثوث")
    btn_del_channels = types.KeyboardButton("🗑️ حذف جميع القنوات")
    btn_del_tokens = types.KeyboardButton("🗑️ حذف جميع التوكنات")
    
    markup.add(btn_test_dash, btn_test_m3u8)
    markup.add(btn_check_tokens, btn_list_m3u8)
    markup.add(btn_stop_all)
    markup.add(btn_del_channels, btn_del_tokens)
    return markup

# توليد لوحة الأزرار الرقمية الإنلاين المتطابقة مع الصورة 1000214545_2.png
def get_numeric_inline_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=5)
    row1 = [types.InlineKeyboardButton(str(i), callback_data=f"num_{i}") for i in range(1, 6)]
    row2 = [types.InlineKeyboardButton(str(i), callback_data=f"num_{i}") for i in range(6, 11)]
    markup.row(*row1)
    markup.row(*row2)
    return markup

# ================= REGEX DASH FIX =================
def fix_dash_url(url):
    if not url:
        return None
    
    match = re.search(r"https://([^/]*?(?:video|scontent)[^/]*?\.fbcdn\.net)/", url)
    if match:
        domain = match.group(1)
        if "video" in domain:
            replacement = "https://BeOut@video.xx.fbcdn.net/"
        else:
            replacement = "https://BeOut@scontent.xx.fbcdn.net/"
        
        return re.sub(r"https://[^/]*?(?:video|scontent)[^/]*?\.fbcdn\.net/", replacement, url)
    return url

# دالة مخصصة لاستخراج الـ FB KEY الأصلي من الـ stream_url بدقة
def extract_fb_key(stream_url):
    if not stream_url:
        return "غير متوفر"
    # البحث عن النمط الذي يبدأ بـ FB- ومتبوع بأرقام وحروف
    match = re.search(r"(FB-[\w-]+)", stream_url)
    if match:
        return match.group(1)
    
    # محاولة بديلة في حال كان المفتاح في نهاية الرابط مباشرة
    try:
        parts = stream_url.split('/')
        if parts:
            last_part = parts[-1]
            if "FB-" in last_part:
                return last_part.split('?')[0]
    except:
        pass
    return "غير متوفر"

# ================= FACEBOOK GRAPH API =================
def get_new_stream(chat_id):
    page_name = active_page.get(chat_id)
    if not page_name:
        return None, None, None, None

    page = user_pages[chat_id][page_name]

    try:
        r = requests.post(
            f"https://graph.facebook.com/v17.0/{page['page_id']}/live_videos",
            params={
                "access_token": page["token"],
                "status": "UNPUBLISHED",
                "title": "Live Preview",
                "description": "Preview stream"
            },
            timeout=10
        ).json()

        if "id" not in r:
            return None, None, None, None

        live_id = r["id"]
        info = requests.get(
            f"https://graph.facebook.com/v17.0/{live_id}",
            params={
                "access_token": page["token"],
                "fields": "stream_url,dash_preview_url"
            },
            timeout=10
        ).json()

        return info.get("stream_url"), live_id, fix_dash_url(info.get("dash_preview_url")), page["token"]
    except:
        return None, None, None, None

# ================= FFMPEG ENGINE =================
def launch_ffmpeg(source, stream_url):
    return subprocess.Popen([
        "ffmpeg", "-re",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "1",
        "-i", source,
        "-c", "copy",
        "-f", "flv",
        stream_url
    ], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

# ================= STREAM THREAD =================
def stream_thread(chat_id, source, name):
    stream_url, live_id, dash, token = get_new_stream(chat_id)
    if not stream_url:
        bot.send_message(chat_id, f"❌ فشل إنشاء البث للقناة {name}.", reply_markup=get_main_keyboard())
        return

    start_time = time.time()
    # استخراج الـ FB KEY من رابط البث المولد حالياً
    fb_key_extracted = extract_fb_key(stream_url)

    user_streams.setdefault(chat_id, {})[name] = {
        "proc": None,
        "live_id": live_id,
        "token": token,
        "active": True,
        "source": source,
        "dash_url": dash,
        "start_time": start_time,
        "restarting": False,
        "fb_key": fb_key_extracted
    }

    def send_dash_later():
        time.sleep(20)
        try:
            info = requests.get(
                f"https://graph.facebook.com/v17.0/{live_id}",
                params={"access_token": token, "fields": "dash_preview_url"},
                timeout=10
            ).json()
            fresh = fix_dash_url(info.get("dash_preview_url"))
            if fresh:
                if chat_id in user_streams and name in user_streams[chat_id]:
                    user_streams[chat_id][name]["dash_url"] = fresh  
                
                # الرسالة المعدلة لتشمل اسم القناة، الـ FB KEY المستخرج، والـ DASH الثابت
                msg_text = f"🎥 {name}\n\n🔑 FB KEY:\n`{fb_key_extracted}`\n\n👁️ DASH:\n{fresh}"
                bot.send_message(chat_id, msg_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        except:
            pass

    threading.Thread(target=send_dash_later, daemon=True).start()

    while user_streams.get(chat_id, {}).get(name, {}).get("active", False):
        proc = user_streams[chat_id][name].get("proc")

        if proc is None or proc.poll() is not None:
            user_streams[chat_id][name]["restarting"] = True
            
            # تحديث الروابط والمفاتيح عند إعادة تشغيل البث تلقائياً
            new_stream_url, new_live_id, new_dash, _ = get_new_stream(chat_id)
            if new_stream_url:
                stream_url = new_stream_url
                user_streams[chat_id][name]["live_id"] = new_live_id
                user_streams[chat_id][name]["dash_url"] = new_dash
                user_streams[chat_id][name]["fb_key"] = extract_fb_key(new_stream_url)

            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc
            user_streams[chat_id][name]["restarting"] = False

        if proc.poll() is not None:
            time.sleep(0.33)
            user_streams[chat_id][name]["restarting"] = True
            
            new_stream_url, new_live_id, new_dash, _ = get_new_stream(chat_id)
            if new_stream_url:
                stream_url = new_stream_url
                user_streams[chat_id][name]["live_id"] = new_live_id
                user_streams[chat_id][name]["dash_url"] = new_dash
                user_streams[chat_id][name]["fb_key"] = extract_fb_key(new_stream_url)

            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc
            user_streams[chat_id][name]["restarting"] = False
            
        time.sleep(0.33)

    proc = user_streams.get(chat_id, {}).get(name, {}).get("proc")
    if proc:
        proc.kill()

# ================= STOP STREAM FUNCTION =================
def stop_stream(chat_id, name):
    info = user_streams.get(chat_id, {}).get(name)
    if not info:
        return

    info["active"] = False

    try:
        if info.get("proc"):
            info["proc"].kill()
        requests.delete(
            f"https://graph.facebook.com/v17.0/{info['live_id']}",
            params={"access_token": info["token"]},
            timeout=10
        )
    except:
        pass

    if name in user_streams.get(chat_id, {}):
        del user_streams[chat_id][name]

# ================= ACTION EXECUTION FOR MULTI-STREAM =================
def execute_multi_stream(chat_id, count):
    channels = user_waiting_count[chat_id]["channels"]
    saved = user_m3u8.get(chat_id, {})
    started_total = 0
    
    for name in channels:
        if name in saved:
            source_url = saved[name]
            for i in range(1, count + 1):
                unique_name = f"{name} Line {i}"
                
                if unique_name in user_streams.get(chat_id, {}):
                    bot.send_message(chat_id, f"⚠️ البث '{unique_name}' قيد التشغيل بالفعل.", reply_markup=get_main_keyboard())
                    continue
                    
                threading.Thread(
                    target=stream_thread,
                    args=(chat_id, source_url, unique_name),
                    daemon=True
                ).start()
                started_total += 1
                time.sleep(0.5)
                
    bot.send_message(chat_id, f"✅ جاري إطلاق {started_total} بث متعدد متوازي بنجاح.", reply_markup=get_main_keyboard())
    if chat_id in user_waiting_count:
        del user_waiting_count[chat_id]

# ================= COMMANDS & BUTTONS HANDLERS =================

@bot.message_handler(commands=["start"])
def send_welcome(msg):
    bot.send_message(msg.chat.id, "🎬 أهلاً بك في لوحة تحكم BeOut المحدثة. تم تفعيل أزرار التحكم السريعة بأسفل الشاشة.", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["addpage"])
def add_page(msg):
    try:
        _, name, page_id, token = msg.text.split(maxsplit=3)
    except:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: /addpage الاسم ID التوكن", reply_markup=get_main_keyboard())
        return
    
    str_chat_id = str(msg.chat.id)
    user_pages.setdefault(str_chat_id, {})[name] = {"page_id": page_id, "token": token}
    save_data()
    bot.send_message(msg.chat.id, f"✅ تم إضافة الصفحة {name} بنجاح.", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["usepage"])
def use_page(msg):
    try:
        _, name = msg.text.split(maxsplit=1)
    except:
        return
    
    str_chat_id = str(msg.chat.id)
    if name not in user_pages.get(str_chat_id, {}):
        bot.send_message(msg.chat.id, "❌ الصفحة غير موجودة", reply_markup=get_main_keyboard())
        return
    
    active_page[str_chat_id] = name
    bot.send_message(msg.chat.id, f"🎯 الصفحة النشطة الآن: {name}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["savem3u8"])
def save_m3u8(msg):
    try:
        _, name, url = msg.text.split(maxsplit=2)
    except:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: /savem3u8 الاسم الرابط", reply_markup=get_main_keyboard())
        return
    
    str_chat_id = str(msg.chat.id)
    user_m3u8.setdefault(str_chat_id, {})[name] = url
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم حفظ القناة: {name}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["m3u8list"])
def m3u8_list(msg):
    str_chat_id = str(msg.chat.id)
    data = user_m3u8.get(str_chat_id)
    if not data or len(data) == 0:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة..", reply_markup=get_main_keyboard())
        return
    
    txt = "📺 القنوات المحفوظة:\n"
    for n in data:
        txt += f"- {n}\n"
    bot.send_message(msg.chat.id, txt, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["stopall"])
def stop_all(msg):
    str_chat_id = str(msg.chat.id)
    streams = user_streams.get(str_chat_id)
    if not streams:
        bot.send_message(msg.chat.id, "❌ لا توجد بثوث نشطة", reply_markup=get_main_keyboard())
        return
    
    for name in list(streams.keys()):
        stop_stream(str_chat_id, name)
        bot.send_message(msg.chat.id, f"🛑 تم إيقاف: {name}", reply_markup=get_main_keyboard())
    
    bot.send_message(msg.chat.id, "🛑 تم تنظيف الرام وإيقاف جميع العمليات..", reply_markup=get_main_keyboard())

@bot.message_handler(commands=["check"])
def check_tokens(msg):
    str_chat_id = str(msg.chat.id)
    pages = user_pages.get(str_chat_id, {})
    if not pages:
        bot.send_message(msg.chat.id, "❌ لا توجد صفحات مسجلة لفحصها.", reply_markup=get_main_keyboard())
        return
    
    report = "📋 تقرير فحص التوكنات:\n"
    for name, info in pages.items():
        try:
            r = requests.get(
                f"https://graph.facebook.com/v17.0/{info['page_id']}",
                params={"access_token": info["token"], "fields": "name"},
                timeout=10
            )
            if r.status_code == 200:
                report += f"✅ {name}: هذا التوكن شغال\n"
            else:
                report += f"❌ {name}: هذا التوكن غير صالح\n"
        except:
            report += f"❌ {name}: هذا التوكن غير صالح\n"
            
    bot.send_message(msg.chat.id, report, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["testall"])
def test_all_dash(msg):
    str_chat_id = str(msg.chat.id)
    streams = user_streams.get(str_chat_id, {})
    
    if not streams or len(streams) == 0:
        bot.send_message(msg.chat.id, "❌ لا توجد قنوات تبث حالياً لفحصها.", reply_markup=get_main_keyboard())
        return
    
    report = "🧪 فحص روابط DASH للبثوث النشطة:\n\n"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    for name, info in streams.items():
        dash_url = info.get("dash_url")
        start_time = info.get("start_time", time.time())
        
        elapsed_seconds = int(time.time() - start_time)
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{hours}:{minutes:02d}:{seconds:02d}"
        
        if info.get("restarting", False):
            status_emoji = "🟠"
        elif not dash_url:
            status_emoji = "🔴"
        else:
            try:
                res = requests.get(dash_url, headers=headers, timeout=5, stream=True)
                if res.status_code in [200, 206]:
                    status_emoji = "🟢"
                else:
                    status_emoji = "🔴"
                res.close()
            except:
                status_emoji = "🔴"
                
        report += f"《 {status_emoji} {name}\nㅤㅤㅤㅤㅤ🕑 Time : {time_str} 》\n"
        
    bot.send_message(msg.chat.id, report, reply_markup=get_main_keyboard())

@bot.message_handler(commands=["testm3u8"])
def test_m3u8_channels(msg):
    str_chat_id = str(msg.chat.id)
    channels = user_m3u8.get(str_chat_id, {})
    if not channels:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة..", reply_markup=get_main_keyboard())
        return
    
    status_msg = bot.send_message(msg.chat.id, "⏳ جاري فحص الروابط المحفوظة...")
    report = "🧪 تقرير فحص القنوات المحفوظة:\n\n"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    }
    
    for name, url in channels.items():
        if ".m3u8" in url.lower():
            link_type = "M3U8"
        elif ".mpd" in url.lower():
            link_type = "MPD"
        else:
            link_type = "URL"
            
        try:
            res = requests.get(url, headers=headers, timeout=5, allow_redirects=True, stream=True)
            if res.status_code >= 200 and res.status_code < 400:
                status_emoji = "🟢 شغال ✅"
            else:
                status_emoji = f"🔴 خطأ ({res.status_code}) ❌"
            res.close()
        except:
            status_emoji = "🔴 غير مستجيب ❌"
            
        report += f"《 {status_emoji} {name} ({link_type}) 》\n"
        
    bot.edit_message_text(report, chat_id=msg.chat.id, message_id=status_msg.message_id, reply_markup=get_main_keyboard())

# ================= TXT IMPORT =================
@bot.message_handler(content_types=["document"])
def handle_txt(msg):
    if not msg.document.file_name.lower().endswith(".txt"):
        return
    
    file_info = bot.get_file(msg.document.file_id)
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
    content = requests.get(file_url).text
    
    str_chat_id = str(msg.chat.id)
    user_m3u8.setdefault(str_chat_id, {})
    
    channels_to_process = []
    count = 0
    
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            name, url = line.split(maxsplit=1)
            if url.startswith("http"):
                user_m3u8[str_chat_id][name] = url
                channels_to_process.append(name)
                count += 1
        except:
            pass
            
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم استيراد {count} قناة بنجاح..", reply_markup=get_main_keyboard())
    
    if channels_to_process:
        show_stream_options(msg.chat.id, channels_to_process)

# ================= BUTTONS MECHANISM =================
def show_stream_options(chat_id, channel_names):
    markup = types.InlineKeyboardMarkup(row_width=2)
    session_key = f"list_{int(time.time())}"
    user_waiting_count[str(chat_id)] = {"channels": channel_names}
    
    btn_normal = types.InlineKeyboardButton("▶️ بث عادي (مفرد)", callback_data=f"mode_single_{session_key}")
    btn_multi = types.InlineKeyboardButton("🔀 بث متعدد (تكرار)", callback_data=f"mode_multi_{session_key}")
    markup.add(btn_normal, btn_multi)
    
    channels_str = ", ".join(channel_names)
    bot.send_message(chat_id, f"📋 تم اختيار القنوات: \n◀️ {channels_str}\n\nاختر نوع البث المطلوب:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("mode_") or call.data.startswith("num_"))
def handle_callback_queries(call):
    chat_id = str(call.message.chat.id)
    
    if call.data.startswith("num_"):
        if chat_id not in user_waiting_count or "channels" not in user_waiting_count[chat_id]:
            bot.send_message(chat_id, "❌ حدث خطأ في الجلسة، يرجى إعادة إرسال القناة.", reply_markup=get_main_keyboard())
            return
        
        count = int(call.data.split("_")[1])
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except:
            pass
        execute_multi_stream(chat_id, count)
        return

    data_split = call.data.split("_")
    mode = data_split[1]
    
    if chat_id not in user_waiting_count or "channels" not in user_waiting_count[chat_id]:
        bot.send_message(chat_id, "❌ حدث خطأ في الجلسة، يرجى إعادة إرسال القناة.", reply_markup=get_main_keyboard())
        return
        
    channels = user_waiting_count[chat_id]["channels"]
    saved = user_m3u8.get(chat_id, {})
    
    if mode == "single":
        started = 0
        for name in channels:
            if name in saved:
                if name in user_streams.get(chat_id, {}):
                    bot.send_message(chat_id, f"⚠️ البث '{name}' قيد التشغيل بالفعل.", reply_markup=get_main_keyboard())
                    continue
                threading.Thread(
                    target=stream_thread,
                    args=(chat_id, saved[name], name),
                    daemon=True
                ).start()
                started += 1
        if started > 0:
            bot.send_message(chat_id, f"🚀 جاري بدء تشغيل {started} بث عادي...", reply_markup=get_main_keyboard())
        del user_waiting_count[chat_id]
        
    elif mode == "multi":
        user_waiting_count[chat_id]["awaiting_num"] = True
        bot.send_message(
            chat_id, 
            "🔢 كم من بث تريد في كل قناة؟\n(يمكنك اختيار عدد أو كتابة رقم يصل إلى 20)", 
            reply_markup=get_numeric_inline_keyboard()
        )

# ================= TEXT MESSAGE GENERAL RECEIVER =================
@bot.message_handler(func=lambda m: True)
def process_text_or_count(msg):
    str_chat_id = str(msg.chat.id)
    text = msg.text.strip()
    
    if text == "📊 فحص الـ DASH":
        test_all_dash(msg)
        return
    elif text == "📺 فحص القنوات المحفوظة":
        test_m3u8_channels(msg)
        return
    elif text == "🔑 التحقق من التوكنات":
        check_tokens(msg)
        return
    elif text == "📋 عرض القنوات المحفوظة":
        m3u8_list(msg)
        return
    elif text == "🛑 إيقاف جميع البثوث":
        stop_all(msg)
        return
    elif text == "🗑️ حذف جميع القنوات":
        user_m3u8[str_chat_id] = {}
        save_data()
        bot.send_message(msg.chat.id, "🗑️ تم حذف جميع القنوات المحفوظة من قاعدة البيانات بنجاح.", reply_markup=get_main_keyboard())
        return
    elif text == "🗑️ حذف جميع التوكنات":
        user_pages[str_chat_id] = {}
        save_data()
        bot.send_message(msg.chat.id, "🗑️ تم حذف جميع الصفحات والتوكنات المحفوظة بنجاح.", reply_markup=get_main_keyboard())
        return

    if str_chat_id in user_waiting_count and user_waiting_count[str_chat_id].get("awaiting_num"):
        try:
            count = int(text)
            if count <= 0 or count > 20:
                bot.send_message(msg.chat.id, "⚠️ الرجاء إدخال أو كتابة رقم صحيح يصل إلى 20.", reply_markup=get_main_keyboard())
                return
        except ValueError:
            bot.send_message(msg.chat.id, "⚠️ الرجاء إرسال رقم صحيح فقط.", reply_markup=get_main_keyboard())
            return
            
        execute_multi_stream(str_chat_id, count)
        return

    if str_chat_id not in active_page:
        bot.send_message(msg.chat.id, "⚠️ اختر صفحة أولاً باستخدام /usepage.", reply_markup=get_main_keyboard())
        return

    saved = user_m3u8.get(str_chat_id, {})
    detected_channels = []
    not_found = False

    for name in msg.text.splitlines():
        name = name.strip()
        if not name:
            continue
        if name in saved:
            detected_channels.append(name)
        else:
            not_found = True

    if detected_channels:
        show_stream_options(msg.chat.id, detected_channels)
    elif not_found:
        bot.send_message(msg.chat.id, "❌ لم يتم العثور على اسم قناة مطابق.", reply_markup=get_main_keyboard())

# ================= KEEP-ALIVE SERVER (FOR FREE HOSTING) =================
class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is active and running!")
        
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), RequestHandler)
    server.serve_forever()

# ================= RUN =================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("🎬 Bot BeOut is running ...")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"Error occurred: {e}")
