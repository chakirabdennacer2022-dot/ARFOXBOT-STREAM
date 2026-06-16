import telebot
import subprocess
import time
import requests
import threading
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

# ================= CONFIG =================
BOT_TOKEN = "8970620272:AAE91-X9nNoJRS4mA_Qyd6OSF-Pa9a6EqwQ"
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
pending_streams = {}  # لتخزين القنوات المؤقتة التي تنتظر تحديد عدد التكرارات

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
        bot.send_message(chat_id, f"❌ فشل إنشاء البث لـ {name}.")
        return

    user_streams.setdefault(chat_id, {})[name] = {
        "proc": None,
        "live_id": live_id,
        "token": token,
        "active": True,
        "source": source,
        "dash_url": dash  
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
                bot.send_message(chat_id, f"🎥 {name}\n👁️ DASH:\n{fresh}")
        except:
            pass

    threading.Thread(target=send_dash_later, daemon=True).start()

    while user_streams.get(chat_id, {}).get(name, {}).get("active", False):
        proc = user_streams[chat_id][name].get("proc")

        if proc is None or proc.poll() is not None:
            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc

        if proc.poll() is not None:
            time.sleep(0.33)
            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc
            
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

# ================= COMMANDS HANDLERS =================

@bot.message_handler(commands=["addpage"])
def add_page(msg):
    try:
        _, name, page_id, token = msg.text.split(maxsplit=3)
    except:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: /addpage الاسم ID التوكن")
        return
    
    str_chat_id = str(msg.chat.id)
    user_pages.setdefault(str_chat_id, {})[name] = {"page_id": page_id, "token": token}
    save_data()
    bot.send_message(msg.chat.id, f"✅ تم إضافة الصفحة {name} بنجاح.")

@bot.message_handler(commands=["usepage"])
def use_page(msg):
    try:
        _, name = msg.text.split(maxsplit=1)
    except:
        return
    
    str_chat_id = str(msg.chat.id)
    if name not in user_pages.get(str_chat_id, {}):
        bot.send_message(msg.chat.id, "❌ الصفحة غير موجودة")
        return
    
    active_page[str_chat_id] = name
    bot.send_message(msg.chat.id, f"🎯 الصفحة النشطة الآن: {name}")

@bot.message_handler(commands=["savem3u8"])
def save_m3u8(msg):
    try:
        _, name, url = msg.text.split(maxsplit=2)
    except:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: /savem3u8 الاسم الرابط")
        return
    
    str_chat_id = str(msg.chat.id)
    user_m3u8.setdefault(str_chat_id, {})[name] = url
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم حفظ القناة: {name}")

@bot.message_handler(commands=["m3u8list"])
def m3u8_list(msg):
    str_chat_id = str(msg.chat.id)
    data = user_m3u8.get(str_chat_id)
    if not data or len(data) == 0:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة..")
        return
    
    txt = "📺 القنوات المحفوظة:\n"
    for n in data:
        txt += f"- {n}\n"
    bot.send_message(msg.chat.id, txt)

@bot.message_handler(commands=["stopall"])
def stop_all(msg):
    str_chat_id = str(msg.chat.id)
    streams = user_streams.get(str_chat_id)
    if not streams:
        bot.send_message(msg.chat.id, "❌ لا توجد بثوث نشطة")
        return
    
    for name in list(streams.keys()):
        stop_stream(str_chat_id, name)
        bot.send_message(msg.chat.id, f"🛑 تم إيقاف: {name}")
    
    bot.send_message(msg.chat.id, "🛑 تم تنظيف الرام وإيقاف جميع العمليات..")

@bot.message_handler(commands=["check"])
def check_tokens(msg):
    str_chat_id = str(msg.chat.id)
    pages = user_pages.get(str_chat_id, {})
    if not pages:
        bot.send_message(msg.chat.id, "❌ لا توجد صفحات مسجلة لفحصها.")
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
            
    bot.send_message(msg.chat.id, report)

@bot.message_handler(commands=["testall"])
def test_all_dash(msg):
    str_chat_id = str(msg.chat.id)
    streams = user_streams.get(str_chat_id, {})
    
    if not streams or len(streams) == 0:
        bot.send_message(msg.chat.id, "❌ لا توجد قنوات تبث حالياً لفحصها.")
        return
    
    report = "🧪 **فحص روابط DASH للبثوث النشطة:**\n\n"
    
    for name, info in streams.items():
        dash_url = info.get("dash_url")
        
        if not dash_url:
            report += f"⚪️ **{name}**: لا يوجد رابط DASH لهذا البث.\n"
            continue
            
        try:
            res = requests.get(dash_url, timeout=10)
            if res.status_code == 200:
                report += f"✅ **{name}**: رابط DASH يعمل بنجاح.\n"
            else:
                report += f"❌ **{name}**: رابط DASH لا يعمل (Error {res.status_code}).\n"
        except:
            report += f"❌ **{name}**: رابط DASH متعطل (خطأ اتصال).\n"
            
    bot.send_message(msg.chat.id, report, parse_mode="Markdown")

@bot.message_handler(commands=["testm3u8"])
def test_m3u8_channels(msg):
    str_chat_id = str(msg.chat.id)
    channels = user_m3u8.get(str_chat_id, {})
    if not channels:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة..")
        return
    
    status_msg = bot.send_message(msg.chat.id, "⏳ جاري فحص الروابط المحفوظة...")
    report = "🧪 تقرير فحص القنوات المحفوظة:\n"
    
    for name, url in channels.items():
        if ".m3u8" in url.lower():
            link_type = "M3U8"
        elif ".mpd" in url.lower():
            link_type = "MPD"
        else:
            link_type = "URL"
            
        try:
            res = requests.head(url, timeout=5, allow_redirects=True)
            if res.status_code >= 200 and res.status_code < 400:
                status = "شغال ✅"
            else:
                status = f"خطأ ({res.status_code}) ❌"
        except:
            status = "غير مستجيب ❌"
            
        report += f"- {name} ({link_type}) -> {status}\n"
        
    bot.edit_message_text(report, chat_id=msg.chat.id, message_id=status_msg.message_id)

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
    
    imported_channels = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            name, url = line.split(maxsplit=1)
            if url.startswith("http"):
                user_m3u8[str_chat_id][name] = url
                imported_channels.append(name)
        except:
            pass
            
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم استيراد {len(imported_channels)} قناة بنجاح..")
    
    # بعد الاستيراد بنجاح، يسأل المستخدم مباشرة كم بثاً يريد لهذه القنوات المستوردة
    if imported_channels:
        if str_chat_id not in active_page:
            bot.send_message(msg.chat.id, "⚠️ اختر صفحة أولاً باستخدام /usepage لبدء البث.")
            return
        pending_streams[str_chat_id] = imported_channels
        msg_ask = bot.send_message(msg.chat.id, "🔢 كم من بث تريد في كل قناة؟ (أرسل الرقم فقط، مثال: 5)")
        bot.register_next_step_handler(msg_ask, process_count_step)

# ================= TEXT MESSAGE GENERAL RECEIVER =================
@bot.message_handler(func=lambda m: True)
def start_by_name(msg):
    str_chat_id = str(msg.chat.id)
    if str_chat_id not in active_page:
        bot.send_message(msg.chat.id, "⚠️ اختر صفحة أولاً باستخدام /usepage.")
        return

    saved = user_m3u8.get(str_chat_id, {})
    channels_to_start = []
    not_found = False

    for name in msg.text.splitlines():
        name = name.strip()
        if not name:
            continue
        if name in saved:
            channels_to_start.append(name)
        else:
            not_found = True

    if not channels_to_start and not_found:
        bot.send_message(msg.chat.id, "❌ لم يتم العثور على اسم قناة مطابق.")
        return

    if channels_to_start:
        pending_streams[str_chat_id] = channels_to_start
        msg_ask = bot.send_message(msg.chat.id, "🔢 كم من بث تريد في كل قناة؟ (أرسل الرقم فقط، مثال: 5)")
        bot.register_next_step_handler(msg_ask, process_count_step)

# ================= MULTI-STREAM PROCESSOR =================
def process_count_step(msg):
    str_chat_id = str(msg.chat.id)
    try:
        count = int(msg.text.strip())
        if count <= 0:
            raise ValueError
    except ValueError:
        bot.send_message(msg.chat.id, "❌ يرجى إدخال رقم صحيح أكبر من 0.")
        return

    channels = pending_streams.get(str_chat_id, [])
    if not channels:
        bot.send_message(msg.chat.id, "❌ حدث خطأ، يرجى إعادة إرسال أسماء القنوات.")
        return

    saved = user_m3u8.get(str_chat_id, {})
    started_count = 0

    for name in channels:
        if name in saved:
            source_url = saved[name]
            for i in range(1, count + 1):
                # توليد اسم فريد لكل خط بث منعاً للتداخل بالرام وببيانات الـ DASH
                stream_loop_name = f"{name}_{i}"
                
                if stream_loop_name in user_streams.get(str_chat_id, {}):
                    bot.send_message(msg.chat.id, f"⚠️ البث '{stream_loop_name}' قيد التشغيل بالفعل.")
                    continue
                
                threading.Thread(
                    target=stream_thread,
                    args=(str_chat_id, source_url, stream_loop_name),
                    daemon=True
                ).start()
                started_count += 1
                time.sleep(0.5) # تأخير بسيط لتجنب ضغط الطلبات المتتالية على الفيس بوك API

    bot.send_message(msg.chat.id, f"🚀 جاري إطلاق {started_count} بث بالتوازي... ستصلك روابط DASH تباعاً.")
    # تنظيف الذاكرة المؤقتة للطلب
    if str_chat_id in pending_streams:
        del pending_streams[str_chat_id]

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
