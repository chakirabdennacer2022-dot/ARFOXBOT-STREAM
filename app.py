import telebot
import subprocess
import time
import requests
import threading
import json
import os
import re
import xml.etree.ElementTree as ET

# ================= CONFIG =================
BOT_TOKEN = "8973105242:AAGqK-Wr5cyYVDPOD26699QRUj8guauJqiA"
bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = "data.json"

# ================= THE UNIFIED AB-CHAKIR HEADERS =================

# 1. ترويسات الوضع المجاني (تُستخدم حصرياً مع نطاقات z-m-...)
FREE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; AB-CHAKIR dev/AB-CHAKIR; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/88.0.4324.93 Mobile Safari/537.36 [FBAN/EMA;FBLC/ar_AR;FBAV/368.0.0.5.95;FBDM/DisplayMetrics{density=2.0, width=720, height=1352, scaledDensity=2.0, xdpi=294.967, ydpi=294.967, densityDpi=320, noncompatWidthPixels=720, noncompatHeightPixels=1352, noncompatDensity=2.0, noncompatDensityDpi=320, noncompatXdpi=294.967, noncompatYdpi=294.967}]",
    "Upgrade-Insecure-Requests": "1",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1"
}

# 2. الترويسات النظيفة للسيرفر العادي (لتجنب كشف البوت وحظر الطلب)
CLEAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9"
}

# دالة لتحويل ترويساتك لتمريرها لمحرك FFmpeg أثناء سحب/دفع البيانات
def get_ffmpeg_headers_string():
    return "".join(f"{k}: {v}\r\n" for k, v in FREE_HEADERS.items())

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
def get_clean_url(url):
    if not url:
        return url
    return url.replace("https://BeOut@", "https://")

# ================= MPD DEEP PARSER ENGINE =================
def analyze_mpd(mpd_url):
    if not mpd_url:
        return "⚠️ لا يتوفر رابط MPD صالح للتحليل."
        
    try:
        # الاتصال بالـ CDN المجاني لسحب الـ XML مع ترويساتك بالكامل
        r = requests.get(mpd_url, headers=FREE_HEADERS, timeout=10)
        if r.status_code != 200:
            return f"⚠️ تعذر جلب الـ MPD من السيرفر المجاني (HTTP {r.status_code})"
            
        root = ET.fromstring(r.content)
        ns = {'mpd': root.tag.split('}')[0].strip('{')} if '}' in root.tag else {}
        
        adaptation_sets = root.findall('.//mpd:AdaptationSet', ns) if ns else root.findall('.//AdaptationSet')
        
        video_reps = []
        audio_reps = []
        
        for ad_set in adaptation_sets:
            mime_type = ad_set.attrib.get('mimeType', '')
            reps = ad_set.findall('mpd:Representation', ns) if ns else ad_set.findall('Representation')
            
            for rep in reps:
                bandwidth = int(rep.attrib.get('bandwidth', 0)) / 1000
                
                if 'video' in mime_type or rep.attrib.get('width') is not None:
                    width = rep.attrib.get('width', 'N/A')
                    height = rep.attrib.get('height', 'N/A')
                    video_reps.append(f"  📺 `{width}x{height}` 🟢 بمعدل بت: `{bandwidth:.1f} Kbps`")
                elif 'audio' in mime_type:
                    audio_reps.append(f"  🎵 مسار صوتي 🟢 بمعدل بت: `{bandwidth:.1f} Kbps`")
                    
        report = "📊 **تفاصيل بنية البث الداخلي لفيسبوك:**\n"
        if video_reps:
            report += "\n🔹 **الجودات المتوفرة للفيديو:**\n" + "\n".join(video_reps) + "\n"
        if audio_reps:
            report += "\n🔹 **مسارات الصوت النشطة:**\n" + "\n".join(audio_reps) + "\n"
            
        return report
        
    except Exception as e:
        return f"⚠️ خطأ أثناء تحليل ملف الـ MPD برمجياً: `{str(e)}`"

# ================= REGEX DASH TO FREE-MODE CDN FIX =================
def fix_dash_url(url):
    if not url:
        return None
    
    # استبدال أي نطاق ميديا بنطاق الوضع المجاني z-m-scontent ليعمل على مشغلك بدون رصيد
    fixed_url = re.sub(r"https://[^/]*?\.fbcdn\.net", "https://z-m-scontent.xx.fbcdn.net", url)
    return fixed_url

# ================= HYBRID FACEBOOK GRAPH API =================
def get_new_stream(chat_id):
    page_name = active_page.get(chat_id)
    if not page_name:
        bot.send_message(chat_id, "⚠️ الرجاء اختيار صفحة نشطة أولاً عبر الأمر /usepage")
        return None, None, None, None

    page = user_pages[chat_id][page_name]
    res_json = {}

    # الخطوة 1: محاولة إنشاء البث عبر سيرفر الوضع المجاني مع ترويسات الموبايل المجانية
    try:
        r = requests.post(
            f"https://z-m-graph.facebook.com/v19.0/{page['page_id']}/live_videos",
            data={
                "access_token": page["token"],
                "status": "UNPUBLISHED",
                "title": "Live Preview",
                "description": "Preview stream"
            },
            headers=FREE_HEADERS,
            timeout=10
        )
        res_json = r.json()
    except Exception as e:
        res_json = {"error": {"message": str(e)}}

    # الخطوة 2: إذا فشل، نقوم بالتحويل الفوري للسيرفر العادي مع تنظيف الترويسات تماماً لمنع خطأ Unknown Error
    if "id" not in res_json:
        try:
            r = requests.post(
                f"https://graph.facebook.com/v19.0/{page['page_id']}/live_videos",
                data={
                    "access_token": page["token"],
                    "status": "UNPUBLISHED",
                    "title": "Live Preview",
                    "description": "Preview stream"
                },
                headers=CLEAN_HEADERS,  # استخدام الترويسات النظيفة هنا لحل المشكلة
                timeout=10
            )
            res_json = r.json()
        except Exception as e:
            bot.send_message(chat_id, f"❌ **فشل الاتصال بخوادم فيسبوك بالكامل:**\n`{str(e)}`", parse_mode="Markdown")
            return None, None, None, None

    # فحص النتيجة النهائية للإنشاء
    if "id" not in res_json:
        error_msg = res_json.get("error", {}).get("message", "خطأ غير معروف في حساب فيسبوك")
        bot.send_message(chat_id, f"❌ **فشل إنشاء البث:**\n`{error_msg}`", parse_mode="Markdown")
        return None, None, None, None

    live_id = res_json["id"]
    info = {}

    # الخطوة 3: جلب روابط البث
    try:
        info_res = requests.get(
            f"https://z-m-graph.facebook.com/v19.0/{live_id}",
            params={
                "access_token": page["token"],
                "fields": "stream_url,dash_preview_url"
            },
            headers=FREE_HEADERS,
            timeout=10
        )
        info = info_res.json()
        if "stream_url" not in info:
            raise Exception("z-m failed")
    except:
        # جلب البيانات البديل باستخدام الترويسات النظيفة لمنع الحظر
        try:
            info_res = requests.get(
                f"https://graph.facebook.com/v19.0/{live_id}",
                params={
                    "access_token": page["token"],
                    "fields": "stream_url,dash_preview_url"
                },
                headers=CLEAN_HEADERS,
                timeout=10
            )
            info = info_res.json()
        except Exception as e:
            bot.send_message(chat_id, f"❌ **فشل جلب تفاصيل البث:**\n`{str(e)}`", parse_mode="Markdown")
            return None, None, None, None

    raw_dash = info.get("dash_preview_url")
    return info.get("stream_url"), live_id, raw_dash, page["token"]

# ================= FFMPEG ENGINE WITH HEADER INJECTION =================
def launch_ffmpeg(source, stream_url):
    # إرسال ترويسات الوضع المجاني داخل تيار البث للفيسبوك
    ffmpeg_headers = get_ffmpeg_headers_string()
    
    return subprocess.Popen([
        "ffmpeg", "-re",
        "-headers", ffmpeg_headers,
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
        return

    # استخراج الـ MPD المجاني المحول لنطاق الوضع المجاني z-m-scontent
    dash_fixed = fix_dash_url(raw_dash)

    user_streams.setdefault(chat_id, {})[name] = {
        "proc": None,
        "live_id": live_id,
        "token": token,
        "active": True,
        "source": source,
        "dash_url": dash_fixed,
        "raw_dash_url": raw_dash
    }

    def send_dash_later():
        time.sleep(20)
        try:
            # محاولة جلب البيانات المحدثة
            try:
                info = requests.get(
                    f"https://z-m-graph.facebook.com/v19.0/{live_id}",
                    params={"access_token": token, "fields": "dash_preview_url"},
                    headers=FREE_HEADERS,
                    timeout=10
                ).json()
                fresh_raw = info.get("dash_preview_url")
                if not fresh_raw:
                    raise Exception("Try regular")
            except:
                info = requests.get(
                    f"https://graph.facebook.com/v19.0/{live_id}",
                    params={"access_token": token, "fields": "dash_preview_url"},
                    headers=CLEAN_HEADERS,
                    timeout=10
                ).json()
                fresh_raw = info.get("dash_preview_url")
            
            fresh_fixed = fix_dash_url(fresh_raw)
            
            if fresh_fixed:
                if chat_id in user_streams and name in user_streams[chat_id]:
                    user_streams[chat_id][name]["dash_url"] = fresh_fixed  
                    user_streams[chat_id][name]["raw_dash_url"] = fresh_raw
                
                # قراءة وتحليل ملف الـ MPD بالترويسات المجانية المخصصة لك
                mpd_analysis = analyze_mpd(fresh_raw)
                
                message = (
                    f"🎥 **البث نشط الآن للقناة: {name}**\n\n"
                    f"👁️ **رابط الـ DASH للتشغيل على ExoPlayer بدون رصيد:**\n"
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
            
        # محاولة حذف البث من الخادم المجاني وإلا فالعادي (بترويسات نظيفة)
        try:
            requests.delete(
                f"https://z-m-graph.facebook.com/v19.0/{info['live_id']}",
                params={"access_token": info["token"]},
                headers=FREE_HEADERS,
                timeout=10
            )
        except:
            requests.delete(
                f"https://graph.facebook.com/v19.0/{info['live_id']}",
                params={"access_token": info["token"]},
                headers=CLEAN_HEADERS,
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
    
    report = "📋 تقرير فحص التوكنات عبر السيرفر المجاني:\n"
    for name, info in pages.items():
        try:
            # محاولة الفحص بالخوادم المجانية أولاً ثم العادية بترويسات نظيفة
            try:
                r = requests.get(
                    f"https://z-m-graph.facebook.com/v19.0/{info['page_id']}",
                    params={"access_token": info["token"], "fields": "name"},
                    headers=FREE_HEADERS,
                    timeout=10
                )
            except:
                r = requests.get(
                    f"https://graph.facebook.com/v19.0/{info['page_id']}",
                    params={"access_token": info["token"], "fields": "name"},
                    headers=CLEAN_HEADERS,
                    timeout=10
                )
            
            if r.status_code == 200:
                report += f"✅ {name}: هذا التوكن شغال ومصرح\n"
            else:
                report += f"❌ {name}: هذا التوكن غير صالح أو محظور\n"
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
    
    report = "🧪 **فحص روابط DASH للبثوث النشطة بالوضع المجاني (z-m-scontent):**\n\n"
    
    for name, info in streams.items():
        dash_url = info.get("dash_url") # الرابط المجاني المعدل
        
        if not dash_url:
            report += f"⚪️ **{name}**: لا يوجد رابط DASH مجاني مسجل لهذا البث.\n"
            continue
            
        try:
            # اختبار تحميل الرابط المجاني بالترويسات الخاصة بالوضع المجاني لمحاكاة ExoPlayer
            res = requests.get(dash_url, headers=FREE_HEADERS, timeout=10)
            if res.status_code == 200:
                report += f"✅ **{name}**: شغال بنجاح وجاهز للتشغيل على ExoPlayer بدون رصيد.\n"
            else:
                report += f"❌ **{name}**: لا يعمل على النطاق المجاني (HTTP {res.status_code}).\n"
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
    
    status_msg = bot.send_message(msg.chat.id, "⏳ جاري فحص الروابط المحفوظة بالترويسات المخصصة للوضع المجاني...")
    report = "🧪 تقرير فحص القنوات المحفوظة:\n"
    
    for name, url in channels.items():
        if ".m3u8" in url.lower():
            link_type = "M3U8"
        elif ".mpd" in url.lower():
            link_type = "MPD"
        else:
            link_type = "URL"
            
        try:
            # فحص القنوات باستخدام ترويسات مجانية
            res = requests.head(url, headers=FREE_HEADERS, timeout=5, allow_redirects=True)
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
print("🎬 Bot BeOut is running on Facebook Lite Free-Mode Subdomains ...")
bot.polling(non_stop=True)
