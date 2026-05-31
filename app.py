import os
import json
import subprocess
import requests
import re
from telebot import TeleBot

# ================= CONFIG =================
BOT_TOKEN = "8970620272:AAE91-X9nNoJRS4mA_Qyd6OSF-Pa9a6EqwQ"
bot = TeleBot(BOT_TOKEN)
DATA_FILE = "data.json"

# ================= JSON STORAGE LOGIC =================
def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"pages": {}, "channels": {}}
    return {"pages": {}, "channels": {}}

def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({"pages": user_pages, "channels": user_m3u8}, f, ensure_ascii=False, indent=4)

db = load_data()
user_pages = db.get("pages", {})
user_m3u8 = db.get("channels", {})
active_page = {}
user_streams = {}

# ================= DASH FIX =================
def fix_dash_url(url):
    if not url:
        return None
    return re.sub(r"https://[^@]*?(video|scontent)[\w\-.]*\.fbcdn\.net", r"https://BeOut@\1.xx.fbcdn.net", url)

# ================= FACEBOOK API =================
def get_new_stream(chat_id):
    chat_id_str = str(chat_id)
    page_name = active_page.get(chat_id)
    
    if not page_name or chat_id_str not in user_pages or page_name not in user_pages[chat_id_str]:
        return {"streamUrl": None, "liveId": None, "dash": None, "token": None}
        
    page = user_pages[chat_id_str][page_name]
    try:
        # Create Live Video
        r = requests.post(
            f"https://graph.facebook.com/v17.0/{page['page_id']}/live_videos",
            params={
                "access_token": page["token"],
                "status": "UNPUBLISHED",
                "title": "Forja TV Stream",
                "description": "Live Stream via Forja Bot",
                "enable_backup_ingest": "true",
            },
            timeout=15
        )
        r_data = r.json()
        live_id = r_data.get("id")
        if not live_id:
            return {"streamUrl": None, "liveId": None, "dash": None, "token": None}
            
        # Get Stream Info
        info = requests.get(
            f"https://graph.facebook.com/v17.0/{live_id}",
            params={
                "access_token": page["token"],
                "fields": "stream_url,secure_stream_url,dash_preview_url",
            },
            timeout=15
        )
        info_data = info.json()
        stream_url = info_data.get("secure_stream_url") or info_data.get("stream_url")
        return {
            "streamUrl": stream_url,
            "liveId": live_id,
            "dash": fix_dash_url(info_data.get("dash_preview_url")),
            "token": page["token"]
        }
    except Exception as e:
        print("API Error:", str(e))
        return {"streamUrl": None, "liveId": None, "dash": None, "token": None}

# ================= FFMPEG - PASSTHROUGH QUALITY =================
def start_ffmpeg(stream_url, source):
    # جودة كيفما هيا من المصدر - بلا حدود ولا تغيير
    command = [
        "ffmpeg",
        "-re",
        "-i", source,
        "-c:v", "copy",
        "-c:a", "copy",
        "-f", "flv",
        "-flvflags", "no_duration_filesize",
        stream_url,
    ]
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ================= FFMPEG WITH OVERLAY FILTER ONLY =================
def start_ffmpeg_with_filters(stream_url, rtmp_url, watermark_path=None, overlay_text=None):
    args = ["ffmpeg", "-re", "-i", stream_url]
    filters = []
    
    if watermark_path:
        args.extend(["-i", watermark_path])
        filters.append("[1:v]scale=100:100[watermark];[0:v][watermark]overlay=10:10")
        
    if overlay_text and overlay_text.strip():
        safe_text = re.sub(r"['\":]", "", overlay_text)
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        filters.append(f"drawtext=text='{safe_text}':fontcolor=white:fontsize=24:x=10:y=H-40:fontfile={font_path}")
        
    if len(filters) > 0:
        args.extend(["-filter_complex", ";".join(filters)])
        # هنا كمان manter qualité original
        args.extend([
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "18",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-f", "flv",
            "-flvflags", "no_duration_filesize",
            rtmp_url
        ])
    else:
        args.extend([
            "-c", "copy",
            "-f", "flv",
            "-flvflags", "no_duration_filesize",
            rtmp_url
        ])
        
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ================= STREAM THREAD =================
def stream_thread(chat_id, source, name):
    try:
        if chat_id in user_streams and name in user_streams[chat_id]:
            stop_stream(chat_id, name)
            
        res = get_new_stream(chat_id)
        stream_url = res["streamUrl"]
        live_id = res["liveId"]
        dash = res["dash"]
        token = res["token"]
        
        if not stream_url:
            bot.send_message(chat_id, f"❌ فشل إنشاء بث لـ: {name}\nتأكد من اختيار الصفحة الصحيحة بـ /usepage")
            return
            
        process = start_ffmpeg(stream_url, source)
        
        if chat_id not in user_streams:
            user_streams[chat_id] = {}
            
        user_streams[chat_id][name] = {
            "process": process,
            "liveId": live_id,
            "token": token,
            "dashUrl": dash
        }
        
        msg = f"🚀 **بدأ البث بنجاح:**\n🎥 القناة: `{name}`\n📊 الجودة: كما هي من المصدر (بدون تعديل)"
        if dash:
            msg += f"\n\n🔗 **رابط DASH للمعاينة:**\n`{dash}`"
            
        bot.send_message(chat_id, msg, parse_mode="Markdown")
    except Exception as e:
        print("Stream Error:", str(e))

# ================= STOP STREAM =================
def stop_stream(chat_id, name):
    info = user_streams.get(chat_id, {}).get(name)
    if not info:
        return
    try:
        info["process"].kill()
        requests.delete(
            f"https://graph.facebook.com/v17.0/{info['liveId']}",
            params={"access_token": info["token"]},
            timeout=5
        )
    except:
        pass
    
    if chat_id in user_streams and name in user_streams[chat_id]:
        del user_streams[chat_id][name]
        
    bot.send_message(chat_id, f"🛑 تم إيقاف: {name}")

# ================= COMMANDS =================
@bot.message_handler(commands=['testall'])
def test_all_streams(msg):
    streams = user_streams.get(msg.chat.id, {})
    if not streams:
        bot.send_message(msg.chat.id, "❌ لا توجد قنوات تبث حالياً لفحصها.")
        return
        
    status_msg = "🧪 **فحص روابط DASH للبثوث النشطة:**\n\n"
    for name, info in streams.items():
        dash_url = info.get("dashUrl")
        if not dash_url:
            status_msg += f"⚪️ **{name}**: لا يوجد رابط DASH لهذا البث.\n"
            continue
        try:
            check = requests.get(dash_url, timeout=10)
            if check.status_code == 200:
                status_msg += f"✅ **{name}**: رابط DASH يعمل بنجاح.\n"
            else:
                status_msg += f"❌ **{name}**: رابط DASH لا يعمل (Error {check.status_code}).\n"
        except:
            status_msg += f"❌ **{name}**: رابط DASH متعطل (خطأ اتصال).\n"
            
    bot.send_message(msg.chat.id, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=['testm3u8'])
def test_m3u8_channels(msg):
    chat_id_str = str(msg.chat.id)
    saved_channels = user_m3u8.get(chat_id_str, {})
    if not saved_channels:
        bot.send_message(msg.chat.id, "❌ لا توجد قنوات محفوظة لفحصها. استخدم /savem3u8 أولاً.")
        return
        
    wait_msg = bot.send_message(msg.chat.id, "⏳ جاري فحص الروابط المحفوظة...")
    report = "🧪 **تقرير فحص القنوات المحفوظة:**\n\n"
    
    for name, url in saved_channels.items():
        link_type = "🔗 URL"
        if ".m3u8" in url.lower():
            link_type = "🎥 M3U8"
        elif ".mpd" in url.lower():
            link_type = "📦 MPD"
            
        try:
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code >= 400:
                response = requests.get(url, timeout=5)
                
            if response.status_code == 200:
                report += f"✅ **{name}**\n┗ النوع: `{link_type}` | الحالة: `شغال`\n\n"
            else:
                report += f"❌ **{name}**\n┗ النوع: `{link_type}` | الحالة: `خطأ {response.status_code}`\n\n"
        except:
            report += f"⚠️ **{name}**\n┗ النوع: `{link_type}` | الحالة: `غير مستجيب`\n\n"
            
    bot.delete_message(msg.chat.id, wait_msg.message_id)
    
    if len(report) > 4000:
        for i in range(0, len(report), 4000):
            bot.send_message(msg.chat.id, report[i:i+4000], parse_mode="Markdown")
    else:
        bot.send_message(msg.chat.id, report, parse_mode="Markdown")

@bot.message_handler(commands=['check'])
def check_tokens(msg):
    chat_id_str = str(msg.chat.id)
    if chat_id_str not in user_pages or not user_pages[chat_id_str]:
        bot.send_message(msg.chat.id, "❌ ليس لديك صفحات مسجلة للتحقق منها.")
        return
        
    status_msg = "🔍 **نتائج التحقق من التوكنات:**\n\n"
    for name, data in user_pages[chat_id_str].items():
        try:
            response = requests.get("https://graph.facebook.com/me", params={"access_token": data["token"]}, timeout=10)
            if response.status_code == 200:
                status_msg += f"✅ **{name}**: هذا التوكن شغال\n"
            else:
                status_msg += f"❌ **{name}**: هذا التوكن غير صالح\n"
        except:
            status_msg += f"⚠️ **{name}**: تعذر التحقق (خطأ في الاتصال)\n"
            
    bot.send_message(msg.chat.id, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=['addpage'])
def add_page(msg):
    match = msg.text.split(maxsplit=3)
    if len(match) < 4:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: `/addpage الاسم ID التوكن`", parse_mode="Markdown")
        return
        
    name = match[1]
    page_id = match[2]
    token = match[3]
    
    chat_id_str = str(msg.chat.id)
    if chat_id_str not in user_pages:
        user_pages[chat_id_str] = {}
        
    user_pages[chat_id_str][name] = {"page_id": page_id, "token": token}
    save_data()
    bot.send_message(msg.chat.id, f"✅ تم إضافة الصفحة `{name}` بنجاح.", parse_mode="Markdown")

@bot.message_handler(commands=['usepage'])
def use_page(msg):
    match = msg.text.split(maxsplit=1)
    if len(match) < 2:
        bot.send_message(msg.chat.id, "❌ يرجى تحديد اسم الصفحة.")
        return
        
    name = match[1].strip()
    chat_id_str = str(msg.chat.id)
    
    if chat_id_str in user_pages and name in user_pages[chat_id_str]:
        active_page[msg.chat.id] = name
        bot.send_message(msg.chat.id, f"🎯 الصفحة النشطة الآن: `{name}`", parse_mode="Markdown")
    else:
        bot.send_message(msg.chat.id, f"❌ الصفحة `{name}` غير موجودة.")

@bot.message_handler(commands=['savem3u8'])
def save_m3u8(msg):
    match = msg.text.split(maxsplit=2)
    if len(match) < 3:
        return
        
    name = match[1]
    url = match[2]
    chat_id_str = str(msg.chat.id)
    
    if chat_id_str not in user_m3u8:
        user_m3u8[chat_id_str] = {}
        
    user_m3u8[chat_id_str][name] = url
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم حفظ القناة: `{name}`", parse_mode="Markdown")

@bot.message_handler(commands=['m3u8list'])
def list_m3u8(msg):
    chat_id_str = str(msg.chat.id)
    data = user_m3u8.get(chat_id_str, {})
    if not data:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة.")
        return
        
    txt = "📺 **القنوات المحفوظة:**\n"
    for n in data.keys():
        txt += f"- `{n}`\n"
    bot.send_message(msg.chat.id, txt, parse_mode="Markdown")

@bot.message_handler(commands=['stopall'])
def stop_all(msg):
    streams = user_streams.get(msg.chat.id, {})
    if not streams:
        bot.send_message(msg.chat.id, "❌ لا توجد بثوث نشطة.")
        return
        
    # نأخذ نسخة من المفاتيح لأننا سنحذف منها أثناء التكرار
    for name in list(streams.keys()):
        stop_stream(msg.chat.id, name)
        
    bot.send_message(msg.chat.id, "🛑 تم تنظيف الرام وإيقاف جميع العمليات.")

# ================= HANDLE TXT FILE =================
@bot.message_handler(content_types=['document'])
def handle_document(msg):
    if not msg.document.file_name.lower().endswith(".txt"):
        return
    try:
        file_info = bot.get_file(msg.document.file_id)
        file_link = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
        response = requests.get(file_link)
        response.encoding = 'utf-8'
        
        chat_id_str = str(msg.chat.id)
        if chat_id_str not in user_m3u8:
            user_m3u8[chat_id_str] = {}
            
        count = 0
        for line in response.text.split("\n"):
            trimmed = line.strip()
            if trimmed and " " in trimmed:
                parts = trimmed.split(maxsplit=1)
                name = parts[0]
                url = parts[1].strip()
                if url.startswith("http"):
                    user_m3u8[chat_id_str][name] = url
                    count += 1
                    
        save_data()
        bot.send_message(msg.chat.id, f"💾 تم استيراد {count} قناة بنجاح.")
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ خطأ في الملف: {str(e)}")

# ================= START BY NAME =================
@bot.message_handler(func=lambda msg: True)
def handle_text_message(msg):
    if msg.text.startswith("/") or msg.document:
        return
    if msg.chat.id not in active_page:
        bot.send_message(msg.chat.id, "⚠️ اختر صفحة أولاً باستخدام `/usepage`", parse_mode="Markdown")
        return
        
    chat_id_str = str(msg.chat.id)
    saved = user_m3u8.get(chat_id_str, {})
    names = msg.text.split("\n")
    started_count = 0
    
    for n in names:
        trimmed = n.strip()
        if trimmed in saved:
            stream_thread(msg.chat.id, saved[trimmed], trimmed)
            started_count += 1
            
    if started_count == 0:
        bot.send_message(msg.chat.id, "❌ لم يتم العثور على اسم قناة مطابق.")

print("🎬 Bot ZenGo is Running ...")
bot.infinity_polling()
