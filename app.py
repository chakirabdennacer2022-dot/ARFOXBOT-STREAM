import telebot
import subprocess
import requests
import threading
import os
import json
import re  # تم إضافة مكتبة re هنا

# ================= CONFIG =================
BOT_TOKEN = "8970620272:AAE91-X9nNoJRS4mA_Qyd6OSF-Pa9a6EqwQ"
bot = telebot.TeleBot(BOT_TOKEN)

DATA_FILE = "data.json"

# ================= JSON STORAGE LOGIC =================
def load_data():
    """تحميل البيانات من ملف JSON"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"pages": {}, "channels": {}}
    return {"pages": {}, "channels": {}}

def save_data():
    """حفظ البيانات في ملف JSON"""
    data = {
        "pages": user_pages,
        "channels": user_m3u8
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# تهيئة البيانات من الملف عند تشغيل السكربت
db = load_data()
user_pages = db.get("pages", {})
user_m3u8 = db.get("channels", {})
active_page = {}
user_streams = {}

# ================= DASH FIX =================
def fix_dash_url(url):
    if not url: return None
    
    # استخدام تعبير نمطي قوي لقص الدومين بالكامل واستبداله بالخام الصافي المضمون
    if "video" in url:
        url = re.sub(r"https?://[^/]*video[^/]*\.net", "https://BeOut@video.xx.fbcdn.net", url, flags=re.IGNORECASE)
    elif "scontent" in url:
        url = re.sub(r"https?://[^/]*scontent[^/]*\.net", "https://BeOut@scontent.xx.fbcdn.net", url, flags=re.IGNORECASE)
        
    return url

# ================= FACEBOOK API =================
def get_new_stream(chat_id):
    chat_id_str = str(chat_id)
    page_name = active_page.get(chat_id)
    
    if not page_name or chat_id_str not in user_pages or page_name not in user_pages[chat_id_str]:
        return None, None, None, None
        
    page = user_pages[chat_id_str][page_name]

    try:
        r = requests.post(
            f"https://graph.facebook.com/v17.0/{page['page_id']}/live_videos",
            params={
                "access_token": page["token"],
                "status": "UNPUBLISHED",
                "title": "Forja TV Stream",
                "description": "Live Stream via Forja Bot"
            }, timeout=15
        ).json()

        live_id = r.get("id")
        if not live_id: return None, None, None, None

        info = requests.get(
            f"https://graph.facebook.com/v17.0/{live_id}",
            params={"access_token": page["token"], "fields": "stream_url,dash_preview_url"}, 
            timeout=15
        ).json()

        return info.get("stream_url"), live_id, info.get("dash_preview_url"), page["token"]
    except Exception as e:
        print(f"API Error: {e}")
        return None, None, None, None

def start_ffmpeg(stream_url, source):
    command = [
        "ffmpeg",
        "-re",
        "-i", source,
        "-c", "copy",
        "-f", "flv",
        "-flvflags", "no_duration_filesize",
        stream_url
    ]
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ================= FFMPEG ADVANCED TRANSCODING PRO WITH FILTERS =================
def start_ffmpeg_with_filters(stream_url, rtmp_url, watermark_path=None, overlay_text=None):
    # 1. بناء الأمر الأساسي وتحديد مصدر البث والروابط المهتزة
    command = [
        "ffmpeg",
        "-re",                          # القراءة بمعدل البت الطبيعي للفيديو (Real-time)
        "-i", stream_url,               # مصدر البث الأصلي (رابط الـ IPTV أو القناة)
    ]
    
    # --- إعدادات الفلاتر (النص والعلامة المائية) ---
    filters = []
    
    # إضافة الإدخال الثاني إذا وُجد شعار (العلامة المائية)
    if watermark_path:
        command.extend(["-i", watermark_path])
        # ضبط حجم الشعار (100x100) ووضعه في أعلى اليسار
        filters.append("[1:v]scale=100:100[watermark];[0:v][watermark]overlay=10:10")
    
    # إضافة النص أسفل الشاشة إذا وُجد
    if overlay_text and overlay_text.strip():
        safe_text = overlay_text.replace("'", "").replace('"', '').replace(":", "")
        # مسار الخط الافتراضي في أنظمة لينكس وتيرموكس
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        
        if filters:
            filters.append(f"drawtext=text='{safe_text}':fontcolor=white:fontsize=24:x=10:y=H-40:fontfile={font_path}")
        else:
            filters.append(f"drawtext=text='{safe_text}':fontcolor=white:fontsize=24:x=10:y=H-40:fontfile={font_path}")
            
    # دمج الفلاتر المجهزة داخل المصفوفة إذا تم تفعيل أحدها
    if filters:
        command.extend(["-filter_complex", ";".join(filters)])
        
    # --- إعدادات إعادة الترميز وثبات البث (التي كانت بالسكربت الأول) ---
    command.extend([
        # إصلاح التوقيت ومقاومة تقطعات الرابط الأصلي (مهمة جداً لثبات الـ IPTV)
        "-fflags", "+genpts+discardcorrupt",
        "-avoid_negative_ts", "make_zero",
        
        # مرمز الفيديو والسرعة وفورية البث
        "-c:v", "libx264",              # استخدام المرمز القياسي H.264
        "-preset", "veryfast",          # موازنة ممتازة بين سرعة المعالجة وجودة البكسلات
        "-tune", "zerolatency",         # إلغاء الـ Lag والتأخير فوراً بينك وبين السيرفر
        
        # التحكم في صبيب البيانات والـ Bitrate (ثبات الـ CBR المتوافق مع الفيسبوك)
        "-b:v", "2000k",                # صبيب بيانات مستقر ومناسب جداً للإنترنت
        "-maxrate", "2000k",            # منع قفزات الـ Bitrate المفاجئة
        "-bufsize", "4000k",            # حجم البافر لضمان سلاسة التدفق
        "-pix_fmt", "yuv420p",          # تنسيق الألوان القياسي للبث المباشر
        "-g", "60",                     # مفتاح إطار (Keyframe) كل ثانيتين ضبطاً
        
        # --- إعدادات الصوت القياسية المستقرة ---
        "-c:a", "aac",                  # ترميز الصوت بصيغة AAC القياسية
        "-b:a", "128k",                 # جودة صوت نقية ومستقرة
        "-ar", "44100",                 # تثبيت تردد الصوت المتوافق 100% مع البث
        
        # --- مخرج البث وسيرفر الـ RTMP النهائي ---
        "-f", "flv",                    # إجبار حاوية الـ FLV الخاصة بالبث المباشر
        "-flvflags", "no_duration_filesize",
        rtmp_url                        # رابط الـ RTMP المدمج معه الـ Stream Key
    ])
    
    # تشغيل العملية في الخلفية دون حظر السكريبت الأساسي
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# ================= STREAM THREAD =================
def stream_thread(chat_id, source, name):
    try:
        if name in user_streams.get(chat_id, {}):
            stop_stream(chat_id, name)

        stream_url, live_id, dash_url, token = get_new_stream(chat_id)
        if not stream_url:
            bot.send_message(chat_id, f"❌ فشل إنشاء بث لـ: {name}\nتأكد من اختيار الصفحة الصحيحة بـ /usepage")
            return

        # تعديل قاطع ومباشر لروابط البث والمعاينة قبل أي خطوة بمكتبة re
        fixed_stream_url = fix_dash_url(stream_url)
        fixed_dash_url = fix_dash_url(dash_url)

        # تشغيل الفلاتر مع التمرير الصحيح للمتغيرات (مصدر البث أولاً ثم رابط الفيسبوك المعدل)
        process = start_ffmpeg_with_filters(source, fixed_stream_url)
        
        user_streams.setdefault(chat_id, {})[name] = {
            "process": process,
            "live_id": live_id,
            "token": token,
            "dash_url": fixed_dash_url # حفظ الرابط المعدل النظيف للفحص
        }

        msg = f"🚀 **بدأ البث بنجاح:**\n🎥 القناة: `{name}`"
        if fixed_dash_url:
            msg += f"\n\n🔗 **رابط DASH للمعاينة:**\n`{fixed_dash_url}`"
        
        bot.send_message(chat_id, msg, parse_mode="Markdown")
    except Exception as e:
        print(f"Thread Error: {e}")

# ================= STOP STREAM =================
def stop_stream(chat_id, name):
    info = user_streams.get(chat_id, {}).get(name)
    if not info: return

    try:
        info["process"].kill() 
        requests.delete(
            f"https://graph.facebook.com/v17.0/{info['live_id']}",
            params={"access_token": info["token"]}, timeout=5
        )
    except: pass

    if name in user_streams[chat_id]:
        del user_streams[chat_id][name]
    bot.send_message(chat_id, f"🛑 تم إيقاف: {name}")

# ================= NEW: TEST ALL DASH COMMAND =================
@bot.message_handler(commands=["testall"])
def test_all_dash(msg):
    streams = user_streams.get(msg.chat.id, {})
    if not streams:
        bot.send_message(msg.chat.id, "❌ لا توجد قنوات تبث حالياً لفحصها.")
        return

    status_msg = "🧪 **فحص روابط DASH للبثوث النشطة:**\n\n"
    
    for name, info in streams.items():
        dash_url = info.get("dash_url")
        if not dash_url:
            status_msg += f"⚪️ **{name}**: لا يوجد رابط DASH لهذا البث.\n"
            continue
            
        try:
            # محاولة طلب الرابط للتأكد من أنه يعمل (Status 200)
            check = requests.get(dash_url, timeout=10)
            if check.status_code == 200:
                status_msg += f"✅ **{name}**: رابط DASH يعمل بنجاح.\n"
            else:
                status_msg += f"❌ **{name}**: رابط DASH لا يعمل (Error {check.status_code}).\n"
        except:
            status_msg += f"❌ **{name}**: رابط DASH متعطل (خطأ اتصال).\n"
            
    bot.send_message(msg.chat.id, status_msg, parse_mode="Markdown")

# ================= NEW: TEST SAVED M3U8 COMMAND =================
@bot.message_handler(commands=["testm3u8"])
def test_saved_links(msg):
    chat_id_str = str(msg.chat.id)
    saved_channels = user_m3u8.get(chat_id_str, {})

    if not saved_channels:
        bot.send_message(msg.chat.id, "❌ لا توجد قنوات محفوظة لفحصها. استخدم /savem3u8 أولاً.")
        return

    wait_msg = bot.send_message(msg.chat.id, "⏳ جاري فحص الروابط المحفوظة...")
    
    report = "🧪 **تقرير فحص القنوات المحفوظة:**\n\n"
    
    for name, url in saved_channels.items():
        link_type = "🔗 URL"
        if ".m3u8" in url.lower(): link_type = "🎥 M3U8"
        elif ".mpd" in url.lower(): link_type = "📦 MPD"
        
        try:
            # استخدام HEAD لسرعة الفحص، وفي حال فشله نستخدم GET (فقط الرؤوس)
            response = requests.head(url, timeout=5, allow_redirects=True)
            if response.status_code >= 400:
                response = requests.get(url, timeout=5, stream=True)
            
            if response.status_code == 200:
                report += f"✅ **{name}**\n┗ النوع: `{link_type}` | الحالة: `شغال`\n\n"
            else:
                report += f"❌ **{name}**\n┗ النوع: `{link_type}` | الحالة: `خطأ {response.status_code}`\n\n"
        except:
            report += f"⚠️ **{name}**\n┗ النوع: `{link_type}` | الحالة: `غير مستجيب`\n\n"

    bot.delete_message(msg.chat.id, wait_msg.message_id)
    
    if len(report) > 4000:
        for x in range(0, len(report), 4000):
            bot.send_message(msg.chat.id, report[x:x+4000], parse_mode="Markdown")
    else:
        bot.send_message(msg.chat.id, report, parse_mode="Markdown")

# ================= COMMANDS =================
@bot.message_handler(commands=["check"])
def check_tokens(msg):
    chat_id_str = str(msg.chat.id)
    if chat_id_str not in user_pages or not user_pages[chat_id_str]:
        bot.send_message(msg.chat.id, "❌ ليس لديك صفحات مسجلة للتحقق منها.")
        return

    status_msg = "🔍 **نتائج التحقق من التوكنات:**\n\n"
    
    for name, data in user_pages[chat_id_str].items():
        token = data.get("token")
        try:
            response = requests.get(
                f"https://graph.facebook.com/me",
                params={"access_token": token},
                timeout=10
            )
            if response.status_code == 200:
                status_msg += f"✅ **{name}**: هذا التوكن شغال\n"
            else:
                status_msg += f"❌ **{name}**: هذا التوكن غير صالح\n"
        except:
            status_msg += f"⚠️ **{name}**: تعذر التحقق (خطأ في الاتصال)\n"
    
    bot.send_message(msg.chat.id, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=["addpage"])
def add_page(msg):
    try:
        p = msg.text.split(maxsplit=3)
        if len(p) < 4: raise ValueError
        chat_id_str = str(msg.chat.id)
        user_pages.setdefault(chat_id_str, {})[p[1]] = {"page_id": p[2], "token": p[3]}
        save_data() 
        bot.send_message(msg.chat.id, f"✅ تم إضافة الصفحة `{p[1]}` بنجاح.", parse_mode="Markdown")
    except:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: `/addpage الاسم ID التوكن`", parse_mode="Markdown")

@bot.message_handler(commands=["usepage"])
def use_page(msg):
    try:
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            bot.send_message(msg.chat.id, "⚠️ أرسل: `/usepage اسم_الصفحة`", parse_mode="Markdown")
            return
            
        name = parts[1].strip()
        chat_id_str = str(msg.chat.id)
        
        if chat_id_str in user_pages and name in user_pages[chat_id_str]:
            active_page[msg.chat.id] = name
            bot.send_message(msg.chat.id, f"🎯 الصفحة النشطة الآن: `{name}`", parse_mode="Markdown")
        else:
            bot.send_message(msg.chat.id, f"❌ الصفحة `{name}` غير موجودة.")
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ حدث خطأ: {e}")

@bot.message_handler(commands=["savem3u8"])
def save_m3u8(msg):
    try:
        _, name, url = msg.text.split(maxsplit=2)
        chat_id_str = str(msg.chat.id)
        user_m3u8.setdefault(chat_id_str, {})[name] = url
        save_data() 
        bot.send_message(msg.chat.id, f"💾 تم حفظ القناة: `{name}`", parse_mode="Markdown")
    except:
        bot.send_message(msg.chat.id, "⚠️ الصيغة: `/savem3u8 الاسم الرابط`", parse_mode="Markdown")

@bot.message_handler(commands=["m3u8list"])
def m3u8_list(msg):
    chat_id_str = str(msg.chat.id)
    data = user_m3u8.get(chat_id_str)
    if not data:
        bot.send_message(msg.chat.id, "❌ قائمة القنوات فارغة.")
        return
    txt = "📺 **القنوات المحفوظة:**\n"
    for n in data: txt += f"- `{n}`\n"
    bot.send_message(msg.chat.id, txt, parse_mode="Markdown")

@bot.message_handler(commands=["stopall"])
def stop_all(msg):
    streams = user_streams.get(msg.chat.id, {})
    if not streams:
        bot.send_message(msg.chat.id, "❌ لا توجد بثوث نشطة.")
        return
    for name in list(streams.keys()):
        stop_stream(msg.chat.id, name)
    bot.send_message(msg.chat.id, "🛑 تم تنظيف الرام وإيقاف جميع العمليات.")

@bot.message_handler(content_types=["document"])
def handle_txt(msg):
    if not msg.document.file_name.lower().endswith(".txt"): return
    try:
        file_info = bot.get_file(msg.document.file_id)
        content = bot.download_file(file_info.file_path).decode('utf-8')
        chat_id_str = str(msg.chat.id)
        user_m3u8.setdefault(chat_id_str, {})
        count = 0
        for line in content.splitlines():
            line = line.strip()
            if line and " " in line:
                name, url = line.split(maxsplit=1)
                if url.startswith("http"):
                    user_m3u8[chat_id_str][name] = url
                    count += 1
        save_data() 
        bot.send_message(msg.chat.id, f"💾 تم استيراد {count} قناة بنجاح.")
    except Exception as e:
        bot.send_message(msg.chat.id, f"❌ خطأ في الملف: {e}")

@bot.message_handler(func=lambda m: True)
def start_by_name(msg):
    if msg.chat.id not in active_page:
        bot.send_message(msg.chat.id, "⚠️ اختر صفحة أولاً باستخدام `/usepage`")
        return
    
    chat_id_str = str(msg.chat.id)
    saved = user_m3u8.get(chat_id_str, {})
    names = msg.text.splitlines()
    started_count = 0

    for n in names:
        n = n.strip()
        if n in saved:
            threading.Thread(
                target=stream_thread, 
                args=(msg.chat.id, saved[n], n), 
                daemon=True
            ).start()
            started_count += 1

    if started_count == 0:
        bot.send_message(msg.chat.id, "❌ لم يتم العثور على اسم قناة مطابق.")

if __name__ == "__main__":
    print("🎬 Bot ZenGo is Running ...")
    bot.infinity_polling()
