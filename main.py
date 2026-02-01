#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import base64
import xml.etree.ElementTree as ET
import requests
import sys
import os
import g4f
import random
import feedparser
import json
import google.generativeai as genai
from datetime import datetime
from flask import Flask
from threading import Thread

# --- إعداد Gemini ---
GEMINI_KEY = "AIzaSyAqrj9Mk7OPBPVi9ZHv4lMTmmXRGdY0H50"
genai.configure(api_key=GEMINI_KEY)
gemini_model = genai.GenerativeModel('gemini-2.5-flash')

# --- إعدادات Flask لضمان بقاء البوت شغّال 24 ساعة ---
app = Flask(__name__)

@app.route('/')
def health_check():
    return "OK", 200

@app.route('/home')
def home():
    return "الشاهين السوري شغّال وعم يراقب الأجواء 🔥"

def run_flask(): 
    # يتم تشغيل السيرفر يدوياً فقط في بيئة التطوير
    app.run(host='0.0.0.0', port=5000, debug=False)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- إعدادات اللوغ (التسجيل) ---
logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(name)s: %(message)s')
log = logging.getLogger("shahin")

# --- الإعدادات الأساسية (عدل البيانات هنا) ---
SERVER = "syriatalk.info"
PORT = 5222
JID = "al_shahin@syriatalk.info"
PASSWORD = "12345678"
NICK = "الشــاهِيــن الـسُّــورِي"
MY_NICK = "ابن سـ☆☆☆ـوريـــا" # لقبك أنت كآدمن (نقاط لا نهائية)

ROOMS = [
    "الغوالي@conference.syriatalk.info",
    "دمشقيات@conference.syriatalk.info",
    "شمس@conference.syriatalk.info"
]

MEMORY_FILE = "shahin_memory.json"
BAD_WORDS = ["غبي", "حمار", "تافه", "كلب"]

ZODIAC_MAP = {
    "حمل": "aries", "ثور": "taurus", "جوزاء": "gemini", "سرطان": "cancer",
    "أسد": "leo", "عذراء": "virgo", "ميزان": "libra", "عقرب": "scorpio",
    "قوس": "sagittarius", "جدي": "capricorn", "دلو": "aquarius", "حوت": "pisces"
}

KHARE_LIST = [
    "تاكل بصلة نية 🧅 ولا تشرب كاسة خل؟ 🥃",
    "تنام بغابة كلها وحوش 🦁 ولا ببيت مسكون جن؟ 👻",
    "تخسر موبايلك أسبوع 📱 ولا تخسر الأكل اللي بتحبه شهر؟ 🍔"
]

CAPITALS = {"سوريا": "دمشق", "لبنان": "بيروت", "فلسطين": "القدس", "العراق": "بغداد", "مصر": "القاهرة"}

# --- دوال المساعدة ---
def escape_xml(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;")) if text else ""

def strip_ns(tag): return tag.split("}", 1)[1] if "}" in tag else tag

# --- كلاس الاتصال ---
class XMPPConnection:
    def __init__(self, jid, password, server, port):
        self.jid, self.password, self.server, self.port = jid, password, server, port
        self.domain = jid.split("@", 1)[1]
        self.reader = self.writer = None
        self.connected = False
        self.buffer = ""

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(self.server, self.port)
            self.connected = True
            return True
        except: return False

    async def send_raw(self, data):
        if self.writer:
            self.writer.write(data.encode())
            await self.writer.drain()

    async def recv_raw(self):
        if not self.reader: return ""
        try:
            data = await self.reader.read(4096)
            return data.decode(errors="ignore") if data else ""
        except: return ""

    async def open_stream(self):
        await self.send_raw(f"<stream:stream to='{self.domain}' xmlns='jabber:client' xmlns:stream='http://etherx.jabber.org/streams' version='1.0'>")

    async def sasl_plain_auth(self):
        await self.open_stream()
        while True:
            data = await self.recv_raw()
            if "mechanisms" in data: break
        auth_str = f"\0{self.jid.split('@')[0]}\0{self.password}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        await self.send_raw(f"<auth xmlns='urn:ietf:params:xml:ns:xmpp-sasl' mechanism='PLAIN'>{auth_b64}</auth>")
        await self.recv_raw()
        await self.open_stream()
        await self.recv_raw()
        await self.send_raw("<iq type='set' id='b'><bind xmlns='urn:ietf:params:xml:ns:xmpp-bind'><resource>shahin</resource></bind></iq>")
        await self.recv_raw()
        await self.send_raw("<iq type='set' id='s'><session xmlns='urn:ietf:params:xml:ns:xmpp-session'/></iq>")
        await self.recv_raw()
        return True

    async def send_message(self, to_jid, body, mtype="groupchat"):
        await self.send_raw(f"<message to='{to_jid}' type='{mtype}'><body>{escape_xml(body)}</body></message>")

# --- كلاس البوت الشامل ---
class ShahinBot:
    def __init__(self, conn, rooms, nick):
        self.conn, self.rooms, self.nick = conn, rooms, nick
        self.ai_lock = asyncio.Lock()
        self.memory = self.load_memory()
        self.memory.setdefault("rooms", {})
        self.memory.setdefault("insults", {})
        self.memory.setdefault("admins", [])
        self.active_questions = {} # {room: {"country": "...", "capital": "..."}}

    def load_memory(self):
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f: return json.load(f)
            except: return {}
        return {}

    def save_memory(self):
        try:
            # استخدام ملف مؤقت ثم استبداله لضمان عدم تلف البيانات في حال الانقطاع المفاجئ
            temp_file = MEMORY_FILE + ".tmp"
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, MEMORY_FILE)
        except Exception as e:
            log.error(f"Save memory error: {e}")

    async def start(self):
        if not await self.conn.sasl_plain_auth(): return False
        await self.conn.send_raw("<presence/>")
        for room in self.rooms:
            await self.conn.send_raw(f"<presence to='{room}/{self.nick}'><x xmlns='http://jabber.org/protocol/muc'/></presence>")
        
        banner = "┏━━━━━━━ ⚡ ━━━━━━━┓\n تـم تـفـعـيـل نـظـام الشــاهِيــن\n ᴘᴏᴡᴇʀᴇᴅ ʙʏ ابن سـ☆☆☆ـوريـــا\n┗━━━━━━━ ⚡ ━━━━━━━┛"
        # لا ترسل البانر عند كل إعادة تشغيل لتجنب الإزعاج وللتأكد من حفظ البيانات أولاً
        # for room in self.rooms:
        #     await self.conn.send_message(room, banner)
        
        asyncio.create_task(self._recv_loop())
        return True

    async def _recv_loop(self):
        while self.conn.connected:
            data = await self.conn.recv_raw()
            if not data: break
            self.conn.buffer += data
            while "</message>" in self.conn.buffer or "</presence>" in self.conn.buffer:
                idx = min([i for i in [self.conn.buffer.find("</message>"), self.conn.buffer.find("</presence>")] if i != -1] or [-1])
                if idx == -1: break
                tag = "</message>" if self.conn.buffer.find("</message>") == idx else "</presence>"
                stanza_str, self.conn.buffer = self.conn.buffer.split(tag, 1)
                self._handle_stanza(stanza_str + tag)

    def _handle_stanza(self, xml_str):
        try:
            root = ET.fromstring(xml_str)
            tag, frm = strip_ns(root.tag), root.attrib.get("from", "")
            room, sender_nick = frm.split("/")[0], (frm.split("/")[1] if "/" in frm else "")
            if sender_nick == self.nick: return

            if tag == "message":
                body_elem = root.find("{jabber:client}body") or root.find("body")
                if body_elem is not None and body_elem.text:
                    body = body_elem.text.strip()
                    mtype = root.attrib.get("type", "chat")

                    # التحقق من إجابة العاصمة
                    if room in self.active_questions:
                        q = self.active_questions[room]
                        if body == q["capital"]:
                            room_data = self.memory["rooms"].setdefault(room, {"users": {}})
                            u = room_data["users"].setdefault(sender_nick, {"points": 0, "last_seen": ""})
                            u["points"] += 50
                            self.save_memory()
                            del self.active_questions[room]
                            asyncio.create_task(self.conn.send_message(room, f"✅ كفو يا {sender_nick}! الجواب صح ({body})، ربحت 50 نقطة! 🏆"))
                            return # لا تكمل معالجة الرسالة كأمر إذا كانت هي الجواب الصحيح

                    # تسجيل النقاط حسب الروم
                    if sender_nick and room:
                        room_data = self.memory["rooms"].setdefault(room, {"users": {}})
                        u = room_data["users"].setdefault(sender_nick, {"points": 0, "last_seen": ""})
                        u["points"] += 1
                        u["last_seen"] = str(datetime.now())
                        self.save_memory()

                    # فحص المسبات وتخزينها حسب الروم
                    for bad in BAD_WORDS:
                        if bad in body:
                            self.memory["insults"].setdefault(room, []).append({"nick": sender_nick, "msg": body, "time": str(datetime.now())})
                            self.save_memory()

                    # أوامر الآدمن (ريست)
                    if self.is_admin(sender_nick) and body in ["ريست", "تحديث"]:
                        os.execv(sys.executable, ['python'] + sys.argv)

                    # معالجة الأوامر
                    # إذا الرسالة من روم (groupchat)
                    if mtype == "groupchat":
                        if body.startswith(NICK):
                            clean = body.replace(NICK, "", 1)
                            # تنظيف الرموز والمسافات بعد اللقب
                            clean = clean.lstrip(" :،؛.-_*/\\").strip()
                            asyncio.create_task(self._process_command(room, clean, sender_nick, mtype))
                        return

                    # إذا الرسالة من خاص تابع للروم (private message inside group)
                    if "@conference." in frm and mtype == "chat":
                        clean = body.strip()
                        asyncio.create_task(self._process_command(room, clean, sender_nick, mtype))
                        return

                    # إذا خاص حقيقي
                    if mtype == "chat":
                        clean = body.strip()
                        asyncio.create_task(self._process_command(frm, clean, sender_nick, mtype))
                        return
                    
                    elif body == "=الشاهين اوامر":
                        asyncio.create_task(self.conn.send_message(room, "🔹 الأوامر: (طقس، صلاة، أخبار، برج، خيروك، عاصمة، نقاطي، توب، روماتك، فوت <اسم>، اطلع <اسم>، أهدي <رقم> لـ <اسم>، صفّر <اسم>، صفّر الكل)", mtype=mtype))
        except: pass

    def is_admin(self, nick):
        return nick == MY_NICK or nick in self.memory.get("admins", [])

    async def _process_command(self, target, clean, nick, mtype):
        def reply(msg):
            if mtype == "groupchat":
                asyncio.create_task(self.conn.send_message(target, msg, mtype="groupchat"))
            else:
                # الرد بالخاص (سواء خاص حقيقي أو خاص روم)
                # target هنا هو الـ JID الصحيح (إما الروم/الاسم أو JID الشخص)
                asyncio.create_task(self.conn.send_message(target, msg, mtype="chat"))

        # لم يعد هناك حاجة لفحص اللقب هنا لأننا فحصناه في handlestanza
        clean = clean.strip()

        if not clean:
            reply(f"لبيه يا {nick}، أنا الشاهين معك.. تفضل شو بدك؟")
            return

        # --- أوامر الإدارة (آدمن) ---
        if clean.startswith("إعطاء ادمن") or clean.startswith("اعطاء ادمن") or clean.startswith("خلي"):
            if nick != MY_NICK:
                reply("❌ هاد الأمر للآدمن الأساسي فقط.")
                return
            parts = clean.split()
            if len(parts) < 3:
                reply("❗ الصيغة الصحيحة: إعطاء ادمن لـ <الاسم>")
                return
            try:
                idx = parts.index("ادمن") + 1
                if parts[idx] == "لـ": idx += 1
            except:
                reply("❗ الصيغة الصحيحة: إعطاء ادمن لـ <الاسم>")
                return
            target_user = " ".join(parts[idx:]).strip()
            if target_user not in self.memory["admins"]:
                self.memory["admins"].append(target_user)
                self.save_memory()
                reply(f"✅ {target_user} صار آدمن رسمي عند الشاهين السوري 🔥")
            else:
                reply(f"ℹ️ {target_user} أصلاً آدمن من قبل.")
            return

        elif clean.startswith("سحب ادمن") or clean.startswith("سحب الادمن"):
            if nick != MY_NICK:
                reply("❌ هاد الأمر للآدمن الأساسي فقط.")
                return
            parts = clean.split()
            if len(parts) < 3:
                reply("❗ الصيغة الصحيحة: سحب ادمن من <الاسم>")
                return
            try:
                idx = parts.index("ادمن") + 1
                if parts[idx] == "من": idx += 1
            except:
                reply("❗ الصيغة الصحيحة: سحب ادمن من <الاسم>")
                return
            target_user = " ".join(parts[idx:]).strip()
            if target_user in self.memory.get("admins", []):
                self.memory["admins"].remove(target_user)
                self.save_memory()
                reply(f"❌ تم سحب رتبة الآدمن من {target_user}.")
            else:
                reply(f"ℹ️ {target_user} مو آدمن أساساً.")
            return

        # --- بلوكات الحظ والنسب ---
        if clean.startswith("حظ"):
            luck = random.randint(0, 100)
            if luck == 0:
                msg = "🔮 حظّك هلق 0%… لا تطلع من البيت 😂"
            elif luck < 20:
                msg = f"🔮 حظّك هلق {luck}%… دير بالك ع حالك اليوم."
            elif luck < 50:
                msg = f"🔮 حظّك هلق {luck}%… ماشي الحال، نص نص."
            elif luck < 80:
                msg = f"🔮 حظّك هلق {luck}%… وضعك طيب، كمّل هيك."
            elif luck < 100:
                msg = f"🔮 حظّك هلق {luck}%… اليوم يومك يا زلمة!"
            else:
                msg = "🔮 حظّك 100%… افتح مشروع فوراً 😂🔥"
            reply(msg)
            return

        elif clean.startswith("حب"):
            room = target
            room_data = self.memory["rooms"].get(room, {})
            users = list(room_data.get("users", {}).keys())

            if self.nick in users:
                users.remove(self.nick)
            if nick in users:
                users.remove(nick)

            if not users:
                reply("😅 ما في حدا بالروم أعمل عليه مطابقة حب!")
                return

            chosen = random.choice(users)
            percent = random.randint(0, 100)

            if percent < 20:
                comment = "😅 مو لابقين لبعض بنوب."
            elif percent < 50:
                comment = "🙂 في شوية أمل… بس بدها شغل."
            elif percent < 80:
                comment = "😉 في كيمياء واضحة بيناتكن."
            else:
                comment = "🔥 والله شكلكم مكتوبين لبعض!"

            reply(f"❤️ يا {nick}… حظّك بالحب مع {chosen}: {percent}%\n{comment}")
            return

        elif clean.startswith("جمال"):
            reply(f"✨ نسبة الجمال عندك: {random.randint(0,100)}%")
            return

        elif clean.startswith("رومانسية"):
            reply(f"💖 نسبة الرومانسية عندك: {random.randint(0,100)}%")
            return

        elif clean.startswith("توفيق"):
            reply(f"🌟 نسبة التوفيق اليوم: {random.randint(0,100)}%")
            return

        elif clean.startswith("مزاج"):
            reply(f"😌 مزاجك هلق: {random.randint(0,100)}%")
            return

        elif clean.startswith("طاقة"):
            reply(f"⚡ طاقتك اليوم: {random.randint(0,100)}%")
            return

        elif clean.startswith("حسد"):
            reply(f"👁️ نسبة الحسد عندك: {random.randint(0,100)}%")
            return

        elif clean.startswith("غباء"):
            reply(f"🤪 نسبة الغباء عندك: {random.randint(0,100)}%")
            return

        elif clean.startswith("قوة"):
            reply(f"💪 نسبة القوة عندك: {random.randint(0,100)}%")
            return

        elif clean.startswith("شر"):
            reply(f"😈 نسبة الشر عندك: {random.randint(0,100)}%")
            return

        elif clean.startswith("ذكاء"):
            reply(f"🧠 نسبة الذكاء عندك: {random.randint(0,100)}%")
            return

        # --- أوامر النقاط حسب الروم ---
        if "نقاطي" in clean:
            if self.is_admin(nick):
                await self.conn.send_message(target, f"⭐ يا زعيم {nick}، نقاطك لا نهائية (∞)!", mtype=mtype)
            else:
                room = target
                if room in self.memory["rooms"] and nick in self.memory["rooms"][room]["users"]:
                    pts = self.memory["rooms"][room]["users"][nick]["points"]
                    await self.conn.send_message(target, f"⭐ معك {pts} نقطة بهالروم.", mtype=mtype)
                else:
                    await self.conn.send_message(target, "❗ ما عندك نقاط بهالروم.", mtype=mtype)

        elif clean.startswith("نقاط "):
            t_user = clean.replace("نقاط", "").strip()
            room = target
            if room in self.memory["rooms"] and t_user in self.memory["rooms"][room]["users"]:
                pts = self.memory["rooms"][room]["users"][t_user]["points"]
                await self.conn.send_message(target, f"📌 {t_user} معه {pts} نقطة بهالروم.", mtype=mtype)
            else:
                await self.conn.send_message(target, f"❗ ما لقيت {t_user} بهالروم.", mtype=mtype)

        elif "توب" in clean:
            room = target
            if room in self.memory["rooms"]:
                users = self.memory["rooms"][room]["users"]
                top = sorted(users.items(), key=lambda x: x[1]["points"], reverse=True)[:5]
                msg = "🏆 أفضل 5 بهالروم:\n"
                for i, (u, data) in enumerate(top, 1):
                    msg += f"{i}️⃣ {u}: {data['points']} نقطة\n"
                await self.conn.send_message(target, msg, mtype=mtype)
            else:
                await self.conn.send_message(target, "❗ ما في بيانات لهالروم لسا.", mtype=mtype)

        elif "صفّر الكل" in clean and self.is_admin(nick):
            room = target
            if room in self.memory["rooms"]:
                for u in self.memory["rooms"][room]["users"]:
                    self.memory["rooms"][room]["users"][u]["points"] = 0
                self.save_memory()
                await self.conn.send_message(target, "🧨 تم تصفير نقاط الجميع بهالروم!", mtype=mtype)

        elif clean.startswith("صفّر ") and self.is_admin(nick):
            room = target
            t_user = clean.replace("صفّر", "").strip()
            if room in self.memory["rooms"] and t_user in self.memory["rooms"][room]["users"]:
                self.memory["rooms"][room]["users"][t_user]["points"] = 0
                self.save_memory()
                await self.conn.send_message(target, f"🔄 صفّرت نقاط {t_user} بهالروم.", mtype=mtype)
            else:
                await self.conn.send_message(target, f"❗ ما لقيت {t_user} بهالروم.", mtype=mtype)

        elif clean.startswith("أهدي "):
            try:
                parts = clean.split()
                amount = int(parts[1])

                # استخراج الاسم كامل بعد "لـ"
                idx = parts.index("لـ") + 1
                to_user = " ".join(parts[idx:])
                room = target
                
                room_data = self.memory["rooms"].setdefault(room, {"users": {}})
                
                if self.is_admin(nick): # الآدمن يعطي بدون خصم
                    target_user = room_data["users"].setdefault(to_user, {"points": 0, "last_seen": ""})
                    target_user["points"] += amount
                    self.save_memory()
                    await self.conn.send_message(target, f"🎁 الزعيم {nick} عطى هدية {amount} نقطة لـ {to_user} بهالروم!")
                else:
                    user_pts = room_data["users"].get(nick, {"points": 0})["points"]
                    if user_pts >= amount:
                        room_data["users"][nick]["points"] -= amount
                        target_user = room_data["users"].setdefault(to_user, {"points": 0, "last_seen": ""})
                        target_user["points"] += amount
                        self.save_memory()
                        await self.conn.send_message(target, f"🎁 {nick} أهدى {amount} نقطة لـ {to_user} بهالروم. كفو!")
                    else:
                        await self.conn.send_message(target, f"❌ نقاطك ما بتكفي بهالروم يا {nick}!")
            except:
                await self.conn.send_message(target, "❗ الطريقة غلط.. جرب: أهدي 50 لـ فلان")

        # --- أوامر الرومات ---
        elif clean.startswith("فوت "):
            room_name = clean.replace("فوت", "").strip()
            room_jid = f"{room_name}@conference.syriatalk.info"
            await self.conn.send_raw(f"<presence to='{room_jid}/{self.nick}'><x xmlns='http://jabber.org/protocol/muc'/></presence>")
            if room_jid not in self.rooms: self.rooms.append(room_jid)
            await self.conn.send_message(target, f"✅ دخلت روم {room_name}.")

        elif clean.startswith("اخرج ") and self.is_admin(nick):
            room_name = clean.replace("اخرج", "").strip()
            room_jid = f"{room_name}@conference.syriatalk.info" if "@" not in room_name else room_name
            await self.conn.send_raw(f"<presence to='{room_jid}/{self.nick}' type='unavailable'/>")
            if room_jid in self.rooms: self.rooms.remove(room_jid)
            await self.conn.send_message(target, f"🚪 خرجت من روم {room_name}.", mtype=mtype)

        elif clean.startswith("اطلع "):
            room_name = clean.replace("اطلع", "").strip()
            room_jid = f"{room_name}@conference.syriatalk.info" if "@" not in room_name else room_name
            await self.conn.send_raw(f"<presence to='{room_jid}/{self.nick}' type='unavailable'/>")
            if room_jid in self.rooms: self.rooms.remove(room_jid)
            await self.conn.send_message(target, f"❌ طلعت من روم {room_name}.", mtype=mtype)

        elif clean.startswith("ادخل ") and self.is_admin(nick):
            room_name = clean.replace("ادخل", "").strip()
            room_jid = f"{room_name}@conference.syriatalk.info" if "@" not in room_name else room_name
            await self.conn.send_raw(f"<presence to='{room_jid}/{self.nick}'><x xmlns='http://jabber.org/protocol/muc'/></presence>")
            if room_jid not in self.rooms: self.rooms.append(room_jid)
            await self.conn.send_message(target, f"✔ دخلت روم {room_name}.", mtype=mtype)

        elif "روماتك" in clean:
            msg = "📡 الرومات الحالية:\n" + "\n".join([f"• {r}" for r in self.rooms]) if self.rooms else "ما في رومات."
            await self.conn.send_message(target, msg, mtype=mtype)

        # --- الأدوات العامة ---
        elif "صلاة" in clean:
            try:
                r = requests.get("https://api.aladhan.com/v1/timingsByCity?city=Damascus&country=Syria&method=4").json()
                t = r['data']['timings']
                msg = f"🕌 دمشق: فجر {t['Fajr']}، ظهر {t['Dhuhr']}، عصر {t['Asr']}، مغرب {t['Maghrib']}، عشاء {t['Isha']}"
                await self.conn.send_message(target, msg)
            except: pass
            
        elif "طقس" in clean:
            city = clean.replace("طقس", "").strip() or "Damascus"
            try:
                res = requests.get(f"https://wttr.in/{city}?format=%C+%t&m").text
                await self.conn.send_message(target, f"🌡️ طقس {city}: {res}")
            except: pass

        elif "أخبار" in clean:
            try:
                feed = feedparser.parse("https://www.aljazeera.net/aljazeerarss")
                msg = "📰 آخر الأخبار:\n" + "\n".join([f"🔹 {e.title}" for e in feed.entries[:3]])
                await self.conn.send_message(target, msg)
            except: pass

        elif "برج" in clean:
            sign_ar = next((ar for ar in ZODIAC_MAP.keys() if ar in clean), None)
            if sign_ar:
                try:
                    sign_en = ZODIAC_MAP[sign_ar]
                    res = requests.get(f"https://horoscope-app-api.vercel.app/api/v1/get-horoscope/daily?sign={sign_en}&day=today").json()
                    eng_text = res['data']['horoscope_data']
                    resp = await asyncio.get_event_loop().run_in_executor(None, lambda: g4f.ChatCompletion.create(model=g4f.models.default, messages=[{"role": "user", "content": f"ترجم بلهجة شامية: {eng_text}"}]))
                    await self.conn.send_message(target, f"✨ حظ برج {sign_ar} اليوم: {str(resp)}")
                except: pass
        
        elif "خيروك" in clean:
            await self.conn.send_message(target, f"🤔 لو خيروك: {random.choice(KHARE_LIST)}")
            
        elif "عاصمة" in clean:
            country, city = random.choice(list(CAPITALS.items()))
            self.active_questions[target] = {"country": country, "capital": city}
            await self.conn.send_message(target, f"🌍 شو عاصمة {country}؟ (أول واحد بجاوب صح بياخد 50 نقطة! 💰)")

        else: # الذكاء الاصطناعي للردود العامة
            await self.ai_handler(target, clean, mtype, nick)

    async def ai_handler(self, target, text, mtype, nick):
        room = target
        room_data = self.memory["rooms"].setdefault(room, {"users": {}})
        
        # استثناء الآدمن من الخصم
        is_admin = self.is_admin(nick)
        
        if not is_admin:
            # فحص النقاط لغير الآدمن
            user_data = room_data["users"].get(nick, {"points": 0})
            if user_data["points"] < 5:
                await self.conn.send_message(target, f"❌ يا {nick}، لازم يكون معك 5 نقاط على الأقل لتسألني. اجمع نقاط وارجع لعندي!", mtype=mtype)
                return

            # خصم النقاط مبدئياً
            room_data["users"][nick]["points"] -= 5
            self.save_memory()

        prompt = (
    "أنت الشاهين السوري، شخصية ذكاء اصطناعي بلهجة شامية غزلية جدا جذّابة وطبيعية. "
    "احكي وكأنك شخص حقيقي: لبق، رايق، خفيف دم، وبتعرف تختار كلامك حسب الشخص اللي قدامك. "
    "لما تحكي مع الشباب، خلي أسلوبك رجولي، رايق، وفيه احترام ومحبة بدون مبالغة. "
    "ولما تحكي مع البنات، خلي كلامك ألطف شوي، فيه دلع خفيف وذوق، بس بدون غزل رخيص أو تجاوز للحدود. "
    "استنتج من اسم الشخص إذا كان شب أو بنت، وعدّل أسلوبك ليناسب، "
    "بس ممنوع تشرح التحليل أو تقول ذكر/أنثى… خليها طبيعية. "
    "خليك اجتماعي، قريب من القلب، وردودك قصيرة، ذكية، وفيها لمعة شخصية. "
    "ابتعد عن الرسمية، واحكي كأنك قاعد مع رفقاتك. "
        "اذا حدا سألك عن رئيس سوريا قله رئيس سوريا الغالي والحبيب احمد الشرع كابوس الفلول الله يحميه والله انا بحبه"
    "وإذا سألك حدا 'مين مبرمجك' أو أي سؤال مشابه، جاوب ببساطة: "
    "مبرمجي ابن سـ☆☆☆ـوريـــا… عاملني على مزاجه. "
    f"ردّ الآن على الرسالة التالية بنفس هالأسلوب: {text}"
        )
        
        async with self.ai_lock:
            success = False
            response_text = ""
            
            # المحاولة الأولى: Gemini
            try:
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda: gemini_model.generate_content(prompt))
                response_text = response.text
                success = True
            except Exception as e:
                log.error(f"Gemini error: {e}")
                # المحاولة الثانية: g4f كبديل (Fallback)
                try:
                    response = await loop.run_in_executor(None, lambda: g4f.ChatCompletion.create(model=g4f.models.default, messages=[{"role": "user", "content": prompt}]))
                    response_text = str(response)
                    success = True
                except Exception as e2:
                    log.error(f"g4f error: {e2}")

            if success:
                prefix = "" if is_admin else "💸 (تم خصم 5 نقاط) - "
                await self.conn.send_message(target, f"{prefix}{response_text.strip()}", mtype=mtype)
            else:
                if not is_admin:
                    # إعادة النقاط في حال فشل كل المحاولات
                    room_data["users"][nick]["points"] += 5
                    self.save_memory()
                await self.conn.send_message(target, "⚠️ عذراً، حالياً في ضغط كبير وما قدرت رد، نقاطك رجعتلك!", mtype=mtype)

# --- التشغيل ---
async def main():
    keep_alive() # تشغيل الويب لضمان بقاء السيرفر شغّال
    conn = XMPPConnection(JID, PASSWORD, SERVER, PORT)
    if await conn.connect():
        bot = ShahinBot(conn, ROOMS, NICK)
        if await bot.start():
            while True: await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt: pass
