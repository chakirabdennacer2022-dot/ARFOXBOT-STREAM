import telebot
import subprocess
import time
import requests
import threading
import json
import os
import re
import xml.etree.ElementTree as ET  # مكتبة تحليل الـ XML لمعالجة الـ MPD

# ================= CONFIG =================
BOT_TOKEN = "8973105242:AAGqK-Wr5cyYVDPOD26699QRUj8guauJqiA"
bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = "data.json"

# الترويسات الأمنية لتفادي حظر فيسبوك (HTTP 403 Forbidden)
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

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

# ================= HELPER FUNCTIONS =================

# تنظيف الرابط من بروكسي BeOut لكي يتمكن سيرفر البوت من فحصه مباشرة
def get_clean_url(url):
    if not url:
        return url
    return url.replace("https://BeOut@", "https://")

# ================= MPD DEEP PARSER ENGINE =================
def analyze_mpd(mpd_url):
    """
    يقوم هذا التابع بالاتصال برابط الـ MPD الأصلي، وتحليل الـ XML الخاص به،
    واستخراج كافة تفاصيل الجودة ومعدلات البت (Bitrates) ومسارات الصوت بدقة.
    """
    if not mpd_url:
        return "⚠️ لا يتوفر رابط MPD صالح للتحليل."
        
    try:
        # جلب ملف الـ XML الخاص بالـ MPD باستخدام الهيدرز الآمنة
        r = requests.get(mpd_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return f"⚠️ تعذر جلب الـ MPD من السيرفر (HTTP {r.status_code})"
            
        # تحليل محتوى الـ XML
        root = ET.fromstring(r.content)
        
        # استخراج النطاق (Namespace) إذا كان موجوداً لتجنب فشل البحث
        ns = {'mpd': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
        
        # العثور على مجموعات التكيف (Adaptation Sets)
        adaptation_sets = root.findall('.//mpd:AdaptationSet', ns) if ns else root.findall('.//AdaptationSet')
        
        video_reps = []
        audio_reps = []
        
        for ad_set in adaptation_sets:
            mime_type = ad_set.attrib.get('mimeType', '')
            reps = ad_set.findall('mpd:Representation', ns) if ns else ad_set.findall('Representation')
            
            for rep in reps:
                bandwidth = int(rep.attrib.get('bandwidth', 0)) / 1000  # تحويل إلى Kbps
                
                # تصنيف جودات الفيديو
                if 'video' in mime_type or rep.attrib.get('width') is not None:
                    width = rep.attrib.get('width', 'N/A')
                    height = rep.attrib.get('height', 'N/A')
                    video_reps.append(f"  📺 `{width}x{height}` 🟢 بمعدل بت: `{bandwidth:.1f} Kbps`")
                # تصنيف مسارات الصوت
                elif 'audio' in mime_type:
                    audio_reps.append(f"  🎵 مسار صوتي 🟢 بمعدل بت: `{bandwidth:.1f} Kbps`")
                    
        # تنسيق التقرير النهائي للمستخدم
        report = "📊 **تفاصيل بنية البث الداخلي لفيسبوك:**\n"
        if video_reps:
            report += "\n🔹 **الجودات المتوفرة للفيديو:**\n" + "\n".join(video_reps) + "\n"
        if audio_reps:
            report += "\n🔹 **مسارات الصوت النشطة:**\n" + "\n".join(audio_reps) + "\n"
            
        return report
        
    except Exception as e:
        return f"⚠️ خطأ أثناء تحليل ملف الـ MPD برمجياً: `{str(e)}`"

# ================= REGEX DASH FIX =================
def fix_dash_url(url):
    if not url:
        return None
    
    # البحث عن النمط الذي يحتوي على video أو scontent وينتهي بـ .fbcdn.net
    match = re.search(r"https://([^/]*?(?:video|scontent)[^/]*?\.fbcdn\.net)/", url)
    if match:
        domain = match.group(1)
        if "video" in domain:
            replacement = "https://BeOut@video.xx.fbcdn.net/"
        else:
            replacement = "https://BeOut@scontent.xx.fbcdn.net/"
        
        # استبدال الجزء الأول بالكامل مع الحفاظ على بقية معاملات الرابط
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
            headers=HEADERS,
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
            headers=HEADERS,
            timeout=10
        ).json()

        raw_dash = info.get("dash_preview_url")
        return info.get("stream_url"), live_id, raw_dash, page["token"]
    except:
        return None, None, None, None

# ================= FFMPEG ENGINE =================
def launch_ffmpeg(source, stream_url):
    return subprocess.Popen([
        "ffmpeg", "-re",
        "-i", source,
        "-c:v", "copy",
        "-c:a", "aac",
        "-f", "flv",
        stream_url
    ], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

# ================= STREAM THREAD =================
def stream_thread(chat_id, source, name):
    stream_url, live_id, raw_dash, token = get_new_stream(chat_id)
    if not stream_url:
        bot.send_message(chat_id, "❌ فشل إنشاء البث.")
        return

    # حفظ الرابط المعدل والمصفي
    dash_fixed = fix_dash_url(raw_dash)

    user_streams.setdefault(chat_id, {})[name] = {
        "proc": None,
        "live_id": live_id,
        "token": token,
        "active": True,
        "source": source,
        "dash_url": dash_fixed,
        "raw_dash_url": raw_dash  # حفظ الرابط الخام لاستخدامه في التحليل والفحص اللاحق
    }

    def send_dash_later():
        time.sleep(20)
        try:
            info = requests.get(
                f"https://graph.facebook.com/v17.0/{live_id}",
                params={"access_token": token, "fields": "dash_preview_url"},
                headers=HEADERS,
                timeout=10
            ).json()
            
            fresh_raw = info.get("dash_preview_url")
            fresh_fixed = fix_dash_url(fresh_raw)
            
            if fresh_fixed:
                if chat_id in user_streams and name in user_streams[chat_id]:
                    user_streams[chat_id][name]["dash_url"] = fresh_fixed  
                    user_streams[chat_id][name]["raw_dash_url"] = fresh_raw
                
                # تحليل الـ MPD في الخلفية وإخراج النتيجة
                mpd_analysis = analyze_mpd(fresh_raw)
                
                # الرسالة التوضيحية المتكاملة والمنظمة بشكل فائق
                message = (
                    f"🎥 **البث نشط الآن للقناة: {name}**\n\n"
                    f"👁️ **رابط الـ DASH المعدل للتشغيل:**\n"
                    f"`{fresh_fixed}`\n\n"
                    f"{mpd_analysis}"
                )
                
                bot.send_message(chat_id, message, parse_mode="Markdown")
        except Exception as e:
            print(f"Error in send_dash_later: {e}")

    threading.Thread(target=send_dash_later, daemon=True).start()

    while user_streams.get(chat_id, {}).get(name, {}).get("active", False):
        proc = user_streams[chat_id][name].get("proc")

        if proc is None or proc.poll() is not None:
            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc

        if proc.poll() is not None:
            time.sleep(2)
            proc = launch_ffmpeg(source, stream_url)
            user_streams[chat_id][name]["proc"] = proc
            
        time.sleep(1)

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
            headers=HEADERS,
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
                headers=HEADERS,
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
        # استخدام الرابط الأصلي الخام (بدون BeOut) للتجربة من السيرفر بنجاح وبأمان
        raw_dash_url = info.get("raw_dash_url")
        
        if not raw_dash_url:
            report += f"⚪️ **{name}**: لا يوجد رابط DASH مسجل لهذا البث.\n"
            continue
            
        try:
            # الفحص مع ترويسة هامة لمنع حظر خادم فيسبوك
            res = requests.get(raw_dash_url, headers=HEADERS, timeout=10)
            if res.status_code == 200:
                # محاولة التحليل السريعة لمعرفة الجودة أثناء الفحص
                report += f"✅ **{name}**: شغال بنجاح وجاهز للبث.\n"
            else:
                report += f"❌ **{name}**: لا يعمل (خطأ فيسبوك: {res.status_code}).\n"
        except:
            report += f"❌ **{name}**: متعطل أو منقطع الاتصال.\n"
            
    bot.send_message(msg.chat.id, report, parse_mode="Markdown")

@bot.message_handler(commands=["testm3u8"])
def test_m3u8_channels(msg):
    str_chat_id = str(msg.chat.id)
    channels = user_m3u8.get(str_chat_id, {})
    if not channels:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة..")
        return
    
    status_msg = bot.send_message(msg.chat.id, "⏳ جاري فحص الروابط المحفوظة باستخدام الترويسات الآمنة...")
    report = "🧪 تقرير فحص القنوات المحفوظة:\n"
    
    for name, url in channels.items():
        if ".m3u8" in url.lower():
            link_type = "M3U8"
        elif ".mpd" in url.lower():
            link_type = "MPD"
        else:
            link_type = "URL"
            
        try:
            # تم دمج HEADERS هنا لضمان عدم الحظر من مزودي الـ IPTV
            res = requests.head(url, headers=HEADERS, timeout=5, allow_redirects=True)
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
    count = 0
    
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            name, url = line.split(maxsplit=1)
            if url.startswith("http"):
                user_m3u8[str_chat_id][name] = url
                count += 1
        except:
            pass
            
    save_data()
    bot.send_message(msg.chat.id, f"💾 تم استيراد {count} قناة بنجاح..")

# ================= TEXT MESSAGE GENERAL RECEIVER =================
@bot.message_handler(func=lambda m: True)
def start_by_name(msg):
    str_chat_id = str(msg.chat.id)
    if str_chat_id not in active_page:
        bot.send_message(msg.chat.id, "⚠️ اختر صفحة أولاً باستخدام /usepage.")
        return

    saved = user_m3u8.get(str_chat_id, {})
    started = 0
    not_found = False

    for name in msg.text.splitlines():
        name = name.strip()
        if not name:
            continue
        if name in saved:
            if name in user_streams.get(str_chat_id, {}):
                bot.send_message(msg.chat.id, f"⚠️ البث '{name}' قيد التشغيل بالفعل.")
                continue
            threading.Thread(
                target=stream_thread,
                args=(str_chat_id, saved[name], name),
                daemon=True
            ).start()
            started += 1
        else:
            not_found = True

    if started == 0 and not_found:
        bot.send_message(msg.chat.id, "❌ لم يتم العثور على اسم قناة مطابق.")

# ================= RUN =================
print("🎬 Bot BeOut is running ...")
bot.polling(non_stop=True)
