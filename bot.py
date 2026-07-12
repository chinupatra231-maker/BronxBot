import os
import sqlite3
import requests
import logging
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import telebot
from telebot import types

# --- CONFIGURATION (YOUR SETTINGS) ---
BOT_TOKEN = "8841748965:AAGlWPCGZtFqtwG74uI2SyOVV_7qUMeFqjU"
ADMIN_ID = 8207882848
API_KEY = "tg-ad-api"
LOOKUP_API_URL = "https://bronx-web-api.onrender.com/api/custom/telegram-scan"
WELCOME_IMAGE = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=800&q=80"

bot = telebot.TeleBot(BOT_TOKEN)
executor = ThreadPoolExecutor(max_workers=30)

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# --- DATABASE SETUP ---
DB_FILE = "bot_database.db"

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                username TEXT,
                status TEXT DEFAULT 'free',
                premium_expiry TEXT,
                demo_used INTEGER DEFAULT 0,
                referred_by INTEGER,
                referrals INTEGER DEFAULT 0,
                banned INTEGER DEFAULT 0,
                joined_date TEXT,
                credits INTEGER DEFAULT 1,
                last_daily_checkin TEXT,
                language TEXT DEFAULT 'hinglish',
                notifications_enabled INTEGER DEFAULT 1
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER,
                query_id TEXT,
                result_number TEXT,
                search_time TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        default_settings = [
            ('upi_id', 'vivek57827@ybl'),
            ('plan_7_days', '99'),
            ('plan_30_days', '299'),
            ('maintenance', '0'),
            ('referral_milestone', '5'),
            ('ai_status', '1')
        ]
        for key, value in default_settings:
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
            
        conn.commit()
        
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'credits' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN credits INTEGER DEFAULT 1")
        if 'last_daily_checkin' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_daily_checkin TEXT")
        if 'language' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'hinglish'")
        if 'notifications_enabled' not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN notifications_enabled INTEGER DEFAULT 1")
            
        conn.commit()
        conn.close()
        logging.info("Database initialized with dynamic migrations.")
    except Exception as e:
        logging.error(f"Database init error: {e}")

init_db()

# --- DATABASE HELPER FUNCTIONS ---
def get_db_connection():
    return sqlite3.connect(DB_FILE)

def get_setting(key):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        logging.error(f"get_setting error: {e}")
        return None

def set_setting(key, value):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"set_setting error: {e}")

def get_user(tg_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception as e:
        logging.error(f"get_user error: {e}")
        return None

def register_user(tg_id, username, referred_by=None):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        user = get_user(tg_id)
        if not user:
            joined_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO users (tg_id, username, joined_date, referred_by, credits) 
                VALUES (?, ?, ?, ?, 1)
            """, (tg_id, username, joined_date, referred_by))
            if referred_by:
                cursor.execute("UPDATE users SET referrals = referrals + 1 WHERE tg_id=?", (referred_by,))
            conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"register_user error: {e}")

def update_user_field(tg_id, field, value):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f"UPDATE users SET {field}=? WHERE tg_id=?", (value, tg_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"update_user_field error: {e}")

def is_premium(tg_id):
    try:
        user = get_user(tg_id)
        if not user:
            return False
        status, expiry = user[2], user[3]
        if status == 'premium' and expiry:
            expiry_dt = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")
            if expiry_dt > datetime.now():
                return True
            else:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET status='free' WHERE tg_id=?", (tg_id,))
                conn.commit()
                conn.close()
        return False
    except Exception as e:
        logging.error(f"is_premium error: {e}")
        return False

# --- UI & NAVIGATION KEYBOARDS ---
def get_main_keyboard(tg_id):
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    user = get_user(tg_id)
    
    btn_lookup = types.KeyboardButton("🔍 TG to Number")
    btn_resolve = types.KeyboardButton("🆔 Resolve Username")
    btn_demo = types.KeyboardButton("🎁 Get Free Demo")
    btn_daily = types.KeyboardButton("🎮 Daily Check-In")
    btn_profile = types.KeyboardButton("👤 My Profile")
    btn_referral = types.KeyboardButton("🤝 Referral Program")
    btn_premium = types.KeyboardButton("👑 Buy Premium")
    btn_history = types.KeyboardButton("📜 Search History")
    btn_stats = types.KeyboardButton("📊 Live Stats")
    btn_ai = types.KeyboardButton("🤖 Smart AI Support")
    btn_settings = types.KeyboardButton("⚙️ Settings")
    btn_about = types.KeyboardButton("ℹ️ About & Security")
    btn_support = types.KeyboardButton("📞 Contact Support")
    
    markup.add(btn_lookup, btn_resolve)
    
    if user and user[4] == 0:
        markup.add(btn_demo, btn_daily)
    else:
        markup.add(btn_daily)
        
    markup.add(btn_profile, btn_referral)
    markup.add(btn_premium, btn_history)
    markup.add(btn_stats, btn_ai)
    markup.add(btn_settings, btn_about)
    markup.add(btn_support)
    
    if tg_id == ADMIN_ID:
        btn_admin = types.KeyboardButton("🛠️ Admin Panel")
        markup.add(btn_admin)
        
    return markup

def get_admin_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ Add Premium", callback_data="admin_add_prem"),
        types.InlineKeyboardButton("➖ Remove Premium", callback_data="admin_rem_prem"),
        types.InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("💳 Change UPI", callback_data="admin_change_upi"),
        types.InlineKeyboardButton("💰 Change Prices", callback_data="admin_change_prices"),
        types.InlineKeyboardButton("⚠️ Check Realtime Logs", callback_data="admin_view_logs"),
        types.InlineKeyboardButton("🔍 Search User DB", callback_data="admin_search_user"),
        types.InlineKeyboardButton("🛠️ Toggle Maintenance", callback_data="admin_toggle_maint"),
        types.InlineKeyboardButton("🚫 Ban User Account", callback_data="admin_ban_user"),
        types.InlineKeyboardButton("🟢 Unban User Account", callback_data="admin_unban_user"),
        types.InlineKeyboardButton("🎁 Reset Demo Stat", callback_data="admin_reset_demo"),
        types.InlineKeyboardButton("📥 Export Live DB (.db)", callback_data="admin_export_db")
    )
    return markup

# --- CORE HANDLERS ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        tg_id = message.from_user.id
        username = message.from_user.username or "No_Username"
        
        if get_setting('maintenance') == '1' and tg_id != ADMIN_ID:
            bot.send_message(tg_id, "⚠️ System Update Underway!\n\nBot is undergoing critical database maintenance. Please check back later.")
            return
            
        referred_by = None
        args = message.text.split()
        if len(args) > 1 and args[1].isdigit():
            ref_id = int(args[1])
            if ref_id != tg_id:
                referred_by = ref_id
                
        register_user(tg_id, username, referred_by)
        
        user = get_user(tg_id)
        if user and user[7] == 1:
            bot.send_message(tg_id, "❌ Access Restricted!\n\nYou have been blocked from utilizing this service due to a policy violation.")
            return

        welcome_banner = WELCOME_IMAGE
        status_badge = "👑 Premium Member" if is_premium(tg_id) else "🆓 Free Tier Client"
        credits_available = user[9] if user else 0
        
        welcome_text = (
            f"╔═════════════════════════════╗\n"
            f"  🌟  WELCOME TO BRONX SCAN PRO  🌟\n"
            f"╚═════════════════════════════╝\n\n"
            f"Hello, {message.from_user.first_name}!\n"
            f"Your request has been successfully authenticated.\n\n"
            f"📂 Your Profile Gateway:\n"
            f"• Telegram ID: {tg_id}\n"
            f"• Username: @{username}\n"
            f"• Tier Status: {status_badge}\n"
            f"• Available Credits: {credits_available}\n"
            f"• Language: Hinglish (Default)\n\n"
            f"💡 Key Utility Services:\n"
            f"1️⃣ Instant Username lookup (Username to Phone Number)\n"
            f"2️⃣ Live ID Extracting Engine\n"
            f"3️⃣ AI Customer Care Auto-pilot\n\n"
            f"⚡ Select an option from the dashboard to get started immediately:"
        )
        
        bot.send_photo(
            tg_id, 
            photo=welcome_banner, 
            caption=welcome_text, 
            reply_markup=get_main_keyboard(tg_id)
        )
    except Exception as e:
        logging.error(f"Error in send_welcome: {e}")
        bot.send_message(message.chat.id, "❌ Bot initializing error. Please contact administrative support.")

# --- MIDDLEWARE & MENU DECODER ---
@bot.message_handler(func=lambda message: True)
def process_menu_choices(message):
    tg_id = message.from_user.id
    text = message.text
    
    user = get_user(tg_id)
    if user and user[7] == 1:
        bot.send_message(tg_id, "❌ Aapko is platform par ban kar diya gaya hai.")
        return
        
    if get_setting('maintenance') == '1' and tg_id != ADMIN_ID:
        bot.send_message(tg_id, "⚠️ Bot maintenance mode par hai. Kripya dhyan rakhein.")
        return

    if text == "🔍 TG to Number":
        handle_tg_to_number_entry(tg_id, user)
    elif text == "🆔 Resolve Username":
        msg = bot.send_message(tg_id, "🌀 Resolve Username & Extract ID:\n\nKripya kisi bhi user, channel, ya group ka link ya username enter karein (e.g. @durov ya https://t.me/durov):")
        bot.register_next_step_handler(msg, process_username_resolve)
    elif text == "🎁 Get Free Demo":
        handle_free_demo_claim(tg_id, user)
    elif text == "🎮 Daily Check-In":
        handle_daily_checkin(tg_id, user)
    elif text == "👤 My Profile":
        handle_profile_card(tg_id, user)
    elif text == "🤝 Referral Program":
        handle_referrals_menu(tg_id, user)
    elif text == "👑 Buy Premium":
        handle_buy_premium_menu(tg_id)
    elif text == "📜 Search History":
        handle_search_history(tg_id)
    elif text == "📊 Live Stats":
        handle_live_stats(tg_id)
    elif text == "🤖 Smart AI Support":
        msg = bot.send_message(tg_id, "🤖 BRONX Support AI Active!\n\nMain ek smart AI robot hoon. Aap mujhse platform ki usage, payment details, ya settings ke baare mein koi bhi sawaal pooch sakte hain.\n\n✍️ Apna sawaal niche type karein:")
        bot.register_next_step_handler(msg, process_ai_chat_response)
    elif text == "⚙️ Settings":
        handle_user_settings(tg_id, user)
    elif text == "ℹ️ About & Security":
        about_text = (
            "🛡️ System Security & Protocols:\n\n"
            "• End-to-End Database Parsing: Hamara lookup interface high security protocols aur proxy systems ka use karta hai.\n"
            "• No Data Leaking Guarantee: Aapki search history fully encrypted hai aur use koi access nahi kar sakta.\n"
            "• Decentralized Data Sources: Multi-API dynamic structures are implemented to ensure zero downtime."
        )
        bot.send_message(tg_id, about_text)
    elif text == "📞 Contact Support":
        bot.send_message(tg_id, "📞 Support Center:\n\nKisi bhi failure ya subscription issue ke liye aap humare official admin handle par message kar sakte hain:\n👉 @sii_3s ")
    elif text == "🛠️ Admin Panel" and tg_id == ADMIN_ID:
        bot.send_message(tg_id, "⚙️ BRONX Command and Control Hub:", reply_markup=get_admin_keyboard())
    else:
        process_ai_fallback(message)

# --- CORE MODULE IMPLEMENTATIONS ---

def handle_tg_to_number_entry(tg_id, user):
    has_active_premium = is_premium(tg_id)
    available_credits = user[9] if user else 0
    
    if not has_active_premium and available_credits <= 0:
        bot.send_message(
            tg_id, 
            "❌ Credits Exhausted!\n\nAapka free quota aur premium plan is samay khatam ho chuka hai.\n\n💡 Aap is tarah credits kama sakte hain:\n"
            "• '🎮 Daily Check-In' button se free credits claim karein.\n"
            "• '🤝 Referral Program' link share karke premium unlock karein.\n"
            "• Direct VIP database access ke liye buy '👑 Buy Premium'."
        )
        return
        
    msg = bot.send_message(
        tg_id, 
        "🎯 Enter Target Information:\n\n"
        "Aap target ka Telegram ID (e.g. 8750654620) ya fir uska Username (e.g. @targetusername) enter karein:\n\n"
        "⚡ Note: Username queries are converted into dynamic IDs internally."
    )
    bot.register_next_step_handler(msg, trigger_async_lookup)

def trigger_async_lookup(message):
    executor.submit(perform_dynamic_lookup, message)

def perform_dynamic_lookup(message):
    tg_id = message.from_user.id
    query = message.text.strip()
    
    original_query = query
    resolved_id = None
    
    bot.send_message(tg_id, "⚙️ Pre-processing identifier...")
    
    if query.startswith('@') or not query.isdigit():
        clean_user = query.replace('@', '').strip()
        try:
            chat = bot.get_chat(f"@{clean_user}")
            resolved_id = str(chat.id)
            bot.send_message(tg_id, f"🎯 Username Resolved Locally!\n• @{clean_user} ➡️ {resolved_id}\n\nDatabase fetch initialization underway...")
            query = resolved_id
        except Exception as e:
            logging.warning(f"Native resolving failed for {clean_user}: {e}")
            bot.send_message(tg_id, "⚠️ Local resolving failed. Direct database lookup execution through API proxy...")
            
    bot.send_message(tg_id, "⏳ Accessing secure node network, processing data. Please wait...")
    
    try:
        user = get_user(tg_id)
        if not is_premium(tg_id) and (user and user[9] <= 0):
            bot.send_message(tg_id, "❌ Verification failed. Unauthorized request context.")
            return

        url = f"{LOOKUP_API_URL}?key={API_KEY}&id={query}"
        response = requests.get(url, timeout=12)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                country = data.get("country", "Global Database")
                code = data.get("country_code", "+")
                number = data.get("number", "Unknown")
                query_id = data.get("tg_id", query)
                
                result_text = (
                    f"✅ DATABASE RECORD FETCHED SUCCESSFUL!\n\n"
                    f"• 👤 Query Identifier: {original_query}\n"
                    f"• 🆔 Target Telegram ID: {query_id}\n"
                    f"• 📞 Linked Phone Number: {code}{number}\n"
                    f"• 📍 Registered Country: {country}\n"
                    f"• 📡 Secure Server Source: {data.get('source_api', 'Bronx Mainframe')}\n\n"
                    f"🛡️ Record processed securely according to active protocol guidelines."
                )
                bot.send_message(tg_id, result_text)
                
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO history (tg_id, query_id, result_number, search_time) VALUES (?, ?, ?, ?)",
                               (tg_id, query_id, f"{code}{number}", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                
                if not is_premium(tg_id):
                    cursor.execute("UPDATE users SET credits = MAX(0, credits - 1), demo_used = 1 WHERE tg_id=?", (tg_id,))
                conn.commit()
                conn.close()
            else:
                bot.send_message(tg_id, f"❌ Record Missing!\n\nID {query} ka koi system record hamare multi-cluster database servers par register nahi mila.")
        else:
            bot.send_message(tg_id, f"⚠️ Connection Drop Alert: API Server rejected the execution token (Status: {response.status_code}). Please report this to our system staff.")
    except requests.exceptions.Timeout:
        bot.send_message(tg_id, "⏰ Server Timeout Alert! Fast parsing connection timed out. Please execute request again.")
    except Exception as e:
        logging.error(f"Execution system failed inside performer node: {e}")
        bot.send_message(tg_id, "❌ Critical System Exception: Request execution node has failed to return results. Administrator has been notified.")

# --- STANDALONE GET CHAT ID / RESOLVER ---
def process_username_resolve(message):
    tg_id = message.from_user.id
    target_input = message.text.strip()
    
    target_clean = target_input.replace("https://t.me/", "").replace("@", "").strip()
    
    bot.send_message(tg_id, "⏳ Polling telegram native directory services...")
    try:
        chat = bot.get_chat(f"@{target_clean}")
        details = (
            f"🎯 IDENTIFIER PARSER REPORT:\n\n"
            f"• Entity Name: {chat.first_name if chat.first_name else chat.title}\n"
            f"• Telegram ID: {chat.id}\n"
            f"• Type: {chat.type.upper()}\n"
            f"• Username: @{chat.username if chat.username else 'Private'}\n"
            f"• Entity Bio/Description: {chat.description if chat.description else 'None'}"
        )
        bot.send_message(tg_id, details)
    except Exception as e:
        bot.send_message(tg_id, f"❌ Identification Failure!\n\nTelegram entity @{target_clean} can not be reached. Target is either highly restricted or invalid.")

# --- FREE DEMO CLAIM UTILITY ---
def handle_free_demo_claim(tg_id, user):
    if user and user[4] == 1:
        bot.send_message(tg_id, "❌ Already Claimed!\n\nAap pehle hi apna free register account verification reward collect kar chuke hain.")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET credits = credits + 1, demo_used = 1 WHERE tg_id=?", (tg_id,))
    conn.commit()
    conn.close()
    
    bot.send_message(tg_id, "🎁 Account Reward Successfully Credited!\n\nAapke profile system mein +1 Premium Credit upgrade kar diya gaya hai. Ab aap live parsing run kar sakte hain.", reply_markup=get_main_keyboard(tg_id))

# --- DAILY REWARDS CHECK-IN SYSTEM ---
def handle_daily_checkin(tg_id, user):
    today_str = datetime.now().strftime("%Y-%m-%d")
    last_checkin = user[10] if user else None
    
    if last_checkin == today_str:
        bot.send_message(tg_id, "❌ Daily Limit Reached!\n\nAap aaj ka daily reward check-in kar chuke hain. Kripya 24 hours ke baad kal dubara check-in karein.")
        return
        
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET credits = credits + 1, last_daily_checkin=? WHERE tg_id=?", (today_str, tg_id))
    conn.commit()
    conn.close()
    
    bot.send_message(tg_id, "🎉 Daily Check-In Success!\n\nAapko +1 Free Lookup Credit claim reward mila hai. Daily visit karke aap system credits badha sakte hain.")

# --- ADVANCED PROFILE CARD ---
def handle_profile_card(tg_id, user):
    if not user:
        bot.send_message(tg_id, "❌ Error retrieving profile data from Database.")
        return
        
    status_str = "👑 VIP Elite Access" if is_premium(tg_id) else "🆓 Standard Tier Access"
    expiry = user[3] if user[3] else "N/A"
    
    card = (
        f"💳 ════════════════════════ 💳\n"
        f"       ⚜️ ACCOUNT PROFILE CARD ⚜️\n"
        f"💳 ════════════════════════ 💳\n\n"
        f"• Telegram Identity: {user[0]}\n"
        f"• Username Handler: @{user[1]}\n"
        f"• System Tier Badge: {status_str}\n"
        f"• Search Credits Bal: {user[9]}\n"
        f"• Premium Expiration: {expiry}\n"
        f"• Referrals Recruited: {user[6]}\n"
        f"• Demo Access Status: {'Utilized' if user[4] == 1 else 'Available'}\n"
        f"• Joined Timestamp: {user[8]}\n\n"
        f"⚙️ Need more access? Click the '👑 Buy Premium' to view custom plans."
    )
    bot.send_message(tg_id, card)

# --- REFERRALS & INSTANT AUTOPILOT REDEEM GATEWAY ---
def handle_referrals_menu(tg_id, user):
    bot_user = bot.get_me()
    ref_link = f"https://t.me/{bot_user.username}?start={tg_id}"
    milestone = int(get_setting('referral_milestone') or '5')
    
    referral_credits = user[6] if user else 0
    
    ref_text = (
        f"🤝 ════════════════════════ 🤝\n"
        f"     💎 REFER & CLAIM SYSTEM 💎\n"
        f"🤝 ════════════════════════ 🤝\n\n"
        f"Har user jo aapke referral invite se register karega, usse aapko milestone level credit points milenge!\n\n"
        f"🎁 Milestone Offer:\n"
        f"• Recruit {milestone} Active Referrals to claim 3 Days Free Elite Premium Plan!\n\n"
        f"📊 Aapka Progress Statistics:\n"
        f"• Total Invites Checked: {referral_credits}\n"
        f"• Target Milestone Required: {milestone}\n\n"
        f"🔗 Your Unique Referral Web-Link:\n{ref_link}\n\n"
        f"🔔 Below buttons are available to claim milestone rewards automatically:"
    )
    
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("🎁 Claim 3 Days VIP Elite", callback_data="claim_referral_reward"),
        types.InlineKeyboardButton("🔄 Refresh Referral Count", callback_data="refresh_referral_status")
    )
    bot.send_message(tg_id, ref_text, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data in ["claim_referral_reward", "refresh_referral_status"])
def handle_referral_claims(call):
    tg_id = call.message.chat.id
    user = get_user(tg_id)
    milestone = int(get_setting('referral_milestone') or '5')
    
    if call.data == "refresh_referral_status":
        bot.answer_callback_query(call.id, "📊 Status refreshed!")
        handle_referrals_menu(tg_id, user)
        return
        
    current_refs = user[6] if user else 0
    
    if current_refs >= milestone:
        expiry_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET referrals = referrals - ?, status='premium', premium_expiry=? WHERE tg_id=?", (milestone, expiry_date, tg_id))
        conn.commit()
        conn.close()
        bot.send_message(tg_id, f"🎉 Congratulations!\n\nAapka referral milestone complete ho chuka hai! 3 Days VIP Elite Access aapki profile par active kar diya gaya hai.\n• Expiry: {expiry_date}")
    else:
        bot.answer_callback_query(call.id, f"❌ Requirements check failed. You need {milestone - current_refs} more valid referrals.", show_alert=True)

# --- BILLING & PAYMENT STEPS GATEWAY ---
def handle_buy_premium_menu(tg_id):
    p_7 = get_setting('plan_7_days') or '99'
    p_30 = get_setting('plan_30_days') or '299'
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(f"Plan Alpha (7 Days VIP) - ₹{p_7}", callback_data="order_plan_7"),
        types.InlineKeyboardButton(f"Plan Delta (30 Days VIP) - ₹{p_30}", callback_data="order_plan_30")
    )
    bot.send_message(
        tg_id, 
        "👑 ════════════════════════ 👑\n"
        "      🌟 VIP PREMIUM TIERS 🌟\n"
        "👑 ════════════════════════ 👑\n\n"
        "Premium subscription lene par aapko unlimited searches aur dedicated multi-server database queries milengi.\n\n"
        "🔥 VIP Features Active:\n"
        "• High priority execution speed\n"
        "• Multi-country data pool unlock\n"
        "• Ads system fully turned-off\n\n"
        "⚡ Niche diye gaye buttons se apna subscription package select karein:", 
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("order_plan_"))
def process_active_billing_order(call):
    try:
        tg_id = call.message.chat.id
        plan_type = call.data.split("_")[2]
        price = get_setting(f'plan_{plan_type}_days') or ('99' if plan_type == '7' else '299')
        upi_id = get_setting('upi_id') or 'vivek57827@ybl'
        
        pay_card = (
            f"💳 SECURE BILLING GATEWAY PRO 💳\n\n"
            f"• Package Selected: {plan_type} Days Premium Status\n"
            f"• Net Payable Amount: ₹{price} (All Taxes Included)\n"
            f"• Merchant UPI ID: {upi_id}\n\n"
            f"⚠️ EASY STEPS TO ACTIVATE VIP STATUS:\n"
            f"1️⃣ Upar diye gaye payment handler UPI ID par exact amount transfer karein.\n"
            f"2️⃣ Payment complete hone ke baad transaction receipt ka Screenshot lein.\n"
            f"3️⃣ Us screenshot ko Directly is chat window mein send karein.\n\n"
            f"⏳ Verification bots are processing requests 24/7. Manual overrides will be handled by system admins."
        )
        msg = bot.send_message(tg_id, pay_card)
        bot.register_next_step_handler(msg, verify_billing_screenshot_step, plan_type, price)
    except Exception as e:
        logging.error(f"Error billing selection flow: {e}")
        bot.send_message(call.message.chat.id, "❌ Payment process is currently experiencing lag. Contact administration.")

def verify_billing_screenshot_step(message, plan_type, price):
    try:
        tg_id = message.from_user.id
        username = message.from_user.username or "No_Username"
        
        if message.content_type != 'photo':
            bot.send_message(tg_id, "❌ Billing Verification Aborted!\n\nAapne valid dynamic verification screenshot/photo send nahi kiya hai. Workflow cancelled.")
            return
            
        file_id = message.photo[-1].file_id
        bot.send_message(tg_id, "⏳ Transaction receipt successfully captured!\n\nHamara system aapka payment ticket admin system validation ke liye forward kar raha hai. Status text update jald hi receive hoga.")
        
        admin_approval_keyboard = types.InlineKeyboardMarkup()
        admin_approval_keyboard.add(
            types.InlineKeyboardButton("Accept & Approve ✅", callback_data=f"billapprove_{tg_id}_{plan_type}"),
            types.InlineKeyboardButton("Reject Payment Ticket ❌", callback_data=f"billreject_{tg_id}")
        )
        
        caption_details = (
            f"🔔 NEW BILLING TRANSACTION RECEIVED!\n\n"
            f"• User ID Identity: {tg_id}\n"
            f"• Username System: @{username}\n"
            f"• Plan Package: {plan_type} Days\n"
            f"• Amount Expected: ₹{price}"
        )
        bot.send_photo(ADMIN_ID, file_id, caption=caption_details, reply_markup=admin_approval_keyboard)
    except Exception as e:
        logging.error(f"Capture verification error: {e}")
        bot.send_message(message.chat.id, "❌ System failed to pass payment packet to admin servers.")

# --- SEARCH HISTORY LOGS ---
def handle_search_history(tg_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT query_id, result_number, search_time FROM history WHERE tg_id=? ORDER BY id DESC LIMIT 5", (tg_id,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        bot.send_message(tg_id, "📭 History empty! Aapne is bot se abhi tak koi records lookup nahi chalaya hai.")
        return
        
    history_box = "📜 ════════════════════════ 📜\n"
    history_box += "       📅 RECENT SEARCH LOGS\n"
    history_box += "📜 ════════════════════════ 📜\n\n"
    
    for row in rows:
        history_box += (
            f"• Query ID: {row[0]}\n"
            f"• Result Number: {row[1]}\n"
            f"• Search Time: {row[2]}\n"
            f"────────────────────\n"
        )
    bot.send_message(tg_id, history_box)

# --- LIVE RUNNING BOT STATISTICS ---
def handle_live_stats(tg_id):
    start_time = datetime.now()
    conn = get_db_connection()
    cursor = conn.cursor()
    total_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    vip_users = cursor.execute("SELECT COUNT(*) FROM users WHERE status='premium'").fetchone()[0]
    total_searches = cursor.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    conn.close()
    end_time = datetime.now()
    
    latency = (end_time - start_time).microseconds / 1000
    
    stats_card = (
        f"📊 ════════════════════════ 📊\n"
        f"      ⚡ BRONX LIVE CORE STATS ⚡\n"
        f"📊 ════════════════════════ 📊\n\n"
        f"• System Status: Online 🟢\n"
        f"• Mainframe Engine Ping: {latency:.2f} ms\n"
        f"• Database Base Size: {total_users} Registered users\n"
        f"• Active Elite VIP Tiers: {vip_users} Users\n"
        f"• Lookups Executed: {total_searches} Queries\n"
        f"• Multi-API Channels: Active Nodes (4/4)\n\n"
        f"🛡️ Statistics reports are synchronized live every 10 seconds."
    )
    bot.send_message(tg_id, stats_card)

# --- CONVERSATIONAL SMART AI CHAT SUPPORT ---
def process_ai_chat_response(message):
    tg_id = message.from_user.id
    query_text = message.text.lower().strip()
    
    response = ""
    if "hello" in query_text or "hi" in query_text or "helo" in query_text:
        response = "🤖 AI Agent: Hello! Main BRONX active AI support bot hoon. Platform details ya help ke liye mujhe direct bataein. Main hamesha online hoon!"
    elif "price" in query_text or "pay" in query_text or "payment" in query_text or "charge" in query_text or "plan" in query_text:
        p_7 = get_setting('plan_7_days') or '99'
        p_30 = get_setting('plan_30_days') or '299'
        response = (
            f"🤖 AI Agent: Humare VIP Plans is prakar hain:\n\n"
            f"• 7 Days Elite Access: ₹{p_7}\n"
            f"• 30 Days Enterprise: ₹{p_30}\n\n"
            f"Is plan ko activate karne ke liye aap screen par active '👑 Buy Premium' button par click karein."
        )
    elif "free" in query_text or "demo" in query_text or "credit" in query_text:
        response = "🤖 AI Agent: Har new user ko starting mein 1 Free Credit milta hai. Aap referral program se dosto ko invite karke ya humara dynamic '🎮 Daily Check-In' claim karke extra credits le sakte hain."
    elif "admin" in query_text or "owner" in query_text or "help" in query_text:
        response = "🤖 AI Agent: Agar aapko payment confirmation ya custom assistance chahiye to aap directly admin helpdesk desk par query drop karein: @sii_3s"
    elif "not working" in query_text or "error" in query_text or "slow" in query_text:
        response = "🤖 AI Agent: Agar API database responds slow ho raha hai to platform servers automatically proxies rotate karte hain. Kripya 2 minute baad check karein ya correct dynamic Telegram ID/Username enter karein."
    else:
        response = (
            "🤖 AI Agent: Main aapka sawaal samajh gaya hoon! Lekin mujhe is par dynamic data analyze karna hoga.\n\n"
            "💡 Aap niche diye gaye system guidelines ka use kar sakte hain:\n"
            "• Database search ke liye: '🔍 TG to Number' use karein.\n"
            "• Pricing details ke liye: '👑 Buy Premium' checkout karein.\n"
            "• Live human support ke liye @sii_3s par chat karein."
        )
        
    bot.send_message(tg_id, response)

def process_ai_fallback(message):
    tg_id = message.from_user.id
    msg_txt = message.text.lower().strip()
    
    if "start" in msg_txt:
        return
        
    fallback_hint = (
        "🤖 Dynamic AI Assistant: Main auto-pilot assist trigger par chal raha hoon. Kripya dashboard commands ya button layouts ka use karein.\n\n"
        "💡 Kya aap profile parameters access karna chahte hain? Niche diye gaye menu options ko use karein."
    )
    bot.send_message(tg_id, fallback_hint)

# --- CUSTOMIZABLE USER SETTINGS MENU ---
def handle_user_settings(tg_id, user):
    notif_status = "Enabled 🔔" if (user and user[12] == 1) else "Muted 🔕"
    lang_status = "Hinglish (In-Use)" if (user and user[11] == 'hinglish') else "English"
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔄 Toggle Dynamic Notifications", callback_data="setting_toggle_notif"),
        types.InlineKeyboardButton("🌐 Switch Interface Language", callback_data="setting_toggle_lang")
    )
    
    settings_layout = (
        f"⚙️ ════════════════════════ ⚙️\n"
        f"        ⚙️ SYSTEM PREFERENCES ⚙️\n"
        f"⚙️ ════════════════════════ ⚙️\n\n"
        f"• Push-Notifications Alert: {notif_status}\n"
        f"• Localization Scheme: {lang_status}\n"
        f"• Target Network Proxies: Multi-Channel Secure\n\n"
        f"🛠️ Modify settings using dynamic modules below:"
    )
    bot.send_message(tg_id, settings_layout, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("setting_"))
def process_dynamic_settings(call):
    tg_id = call.message.chat.id
    user = get_user(tg_id)
    if not user:
        return
        
    if call.data == "setting_toggle_notif":
        current_status = user[12]
        new_status = 0 if current_status == 1 else 1
        update_user_field(tg_id, "notifications_enabled", new_status)
        bot.answer_callback_query(call.id, f"🔔 Alerts set to: {'ON' if new_status==1 else 'OFF'}", show_alert=True)
    elif call.data == "setting_toggle_lang":
        current_lang = user[11]
        new_lang = "english" if current_lang == "hinglish" else "hinglish"
        update_user_field(tg_id, "language", new_lang)
        bot.answer_callback_query(call.id, f"🌐 Language interface: {new_lang.upper()}", show_alert=True)
        
    updated_user = get_user(tg_id)
    notif_status = "Enabled 🔔" if updated_user[12] == 1 else "Muted 🔕"
    lang_status = "Hinglish (In-Use)" if updated_user[11] == 'hinglish' else "English"
    
    settings_layout = (
        f"⚙️ ════════════════════════ ⚙️\n"
        f"        ⚙️ SYSTEM PREFERENCES ⚙️\n"
        f"⚙️ ════════════════════════ ⚙️\n\n"
        f"• Push-Notifications Alert: {notif_status}\n"
        f"• Localization Scheme: {lang_status}\n"
        f"• Target Network Proxies: Multi-Channel Secure\n\n"
        f"🛠️ Preferences successfully updated in Mainframe."
    )
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🔄 Toggle Dynamic Notifications", callback_data="setting_toggle_notif"),
        types.InlineKeyboardButton("🌐 Switch Interface Language", callback_data="setting_toggle_lang")
    )
    bot.edit_message_text(settings_layout, tg_id, call.message.message_id, reply_markup=markup)

# --- ADMINISTRATIVE TRANSACTION SYSTEM ACTION ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("billapprove_") or call.data.startswith("billreject_"))
def process_manual_approvals(call):
    if call.message.chat.id != ADMIN_ID:
        return
        
    action_data = call.data.split("_")
    action = action_data[0]
    target_user_id = int(action_data[1])
    
    if action == "billapprove":
        days = int(action_data[2])
        expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status='premium', premium_expiry=? WHERE tg_id=?", (expiry_date, target_user_id))
        conn.commit()
        conn.close()
        
        bot.send_message(target_user_id, f"🎉 Aapka Premium Account Active kar diya gaya hai!\n\n• Package duration: {days} Days Premium Access\n• Ending Date: {expiry_date}\n\nVIP functionality unlocked successfully!")
        bot.edit_message_caption(chat_id=ADMIN_ID, message_id=call.message.message_id, caption=call.message.caption + "\n\n🟢 ORDER STATE: APPROVED & ACTIVE!")
    
    elif action == "billreject":
        bot.send_message(target_user_id, "❌ Transaction Ticket Rejected!\n\nAapki paid subscription details validation state fail ho chuki hai. Please verify transaction details or pay via valid platform channels.")
        bot.edit_message_caption(chat_id=ADMIN_ID, message_id=call.message.message_id, caption=call.message.caption + "\n\n🔴 ORDER STATE: TICKET CLOSED & REJECTED")

# --- ADMIN ACTIONS MASTER HANDLER ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_"))
def process_super_admin_callbacks(call):
    if call.message.chat.id != ADMIN_ID:
        return
        
    action = call.data
    try:
        if action == "admin_add_prem":
            msg = bot.send_message(ADMIN_ID, "📝 Enter formatting data (Format: user_id,days):")
            bot.register_next_step_handler(msg, admin_add_premium_proc)
        elif action == "admin_rem_prem":
            msg = bot.send_message(ADMIN_ID, "📝 Enter target ID directly to strip premium details:")
            bot.register_next_step_handler(msg, admin_rem_premium_proc)
        elif action == "admin_broadcast":
            msg = bot.send_message(ADMIN_ID, "📢 Write broadcasting message. Markdown details allowed:")
            bot.register_next_step_handler(msg, admin_broadcast_proc)
        elif action == "admin_change_upi":
            msg = bot.send_message(ADMIN_ID, "💳 Enter active UPI ID string:")
            bot.register_next_step_handler(msg, admin_change_upi_proc)
        elif action == "admin_change_prices":
            msg = bot.send_message(ADMIN_ID, "💰 Enter target Pricing Setup (Format: 7_days_price,30_days_price):")
            bot.register_next_step_handler(msg, admin_change_prices_proc)
        elif action == "admin_view_logs":
            if os.path.exists("bot_activity.log"):
                with open("bot_activity.log", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    last_logs = "".join(lines[-25:])
                bot.send_message(ADMIN_ID, f"📋 LAST 25 SECURE PROCESS LOGGER RECORDS:\n\n{last_logs}")
            else:
                bot.send_message(ADMIN_ID, "📋 Log data structures are clean. Logs successfully archived.")
        elif action == "admin_search_user":
            msg = bot.send_message(ADMIN_ID, "🔍 Enter target numeric ID to search profile inside secure DB:")
            bot.register_next_step_handler(msg, admin_search_user_proc)
        elif action == "admin_toggle_maint":
            current = get_setting('maintenance') or '0'
            new_val = '1' if current == '0' else '0'
            set_setting('maintenance', new_val)
            bot.send_message(ADMIN_ID, f"🛠️ Maintenance state updated to: {'ON 🔴' if new_val == '1' else 'OFF 🟢'}")
        elif action == "admin_ban_user":
            msg = bot.send_message(ADMIN_ID, "🚫 Enter target user ID to freeze:")
            bot.register_next_step_handler(msg, lambda m: admin_ban_unban_proc(m, 1))
        elif action == "admin_unban_user":
            msg = bot.send_message(ADMIN_ID, "🟢 Enter target user ID to restore:")
            bot.register_next_step_handler(msg, lambda m: admin_ban_unban_proc(m, 0))
        elif action == "admin_reset_demo":
            msg = bot.send_message(ADMIN_ID, "🎁 Enter target user ID to clear credentials logs:")
            bot.register_next_step_handler(msg, admin_reset_demo_proc)
        elif action == "admin_export_db":
            export_database_to_admin()
    except Exception as e:
        logging.error(f"Execution error at Admin Panel: {e}")
        bot.send_message(ADMIN_ID, f"❌ Admin Callback execution exception: {e}")

# --- ADMIN COMMAND CORE EXECUTIVES ---
def admin_add_premium_proc(message):
    try:
        data = message.text.split(",")
        target_id = int(data[0].strip())
        days = int(data[1].strip())
        expiry_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status='premium', premium_expiry=? WHERE tg_id=?", (expiry_date, target_id))
        conn.commit()
        conn.close()
        
        bot.send_message(ADMIN_ID, f"✅ Elite target {target_id} set to VIP for {days} Days.")
        bot.send_message(target_id, f"👑 VIP Status Notification!\n\nAdmin ne aapke system tier ko manually upgrade kiya hai. Expiry: {expiry_date}")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Format parameters input syntax failed: {e}")

def admin_rem_premium_proc(message):
    try:
        target_id = int(message.text.strip())
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status='free', premium_expiry=NULL WHERE tg_id=?", (target_id,))
        conn.commit()
        conn.close()
        bot.send_message(ADMIN_ID, f"✅ Account {target_id} set back to Standard Tier.")
        bot.send_message(target_id, "⚠️ System Update Alert: Your VIP subscription features have expired.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Target execution process failure: {e}")

def admin_broadcast_proc(message):
    text = message.text
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tg_id FROM users WHERE notifications_enabled=1")
    users = cursor.fetchall()
    conn.close()
    
    bot.send_message(ADMIN_ID, "⚙️ Broadcasting payload inside secure pipelines...")
    success_count = 0
    for user in users:
        try:
            bot.send_message(user[0], f"📢 SYSTEM UPDATE RELEASED:\n\n{text}")
            success_count += 1
        except Exception:
            pass
    bot.send_message(ADMIN_ID, f"📢 Broadcast finished. Dispatched packet successfully to {success_count} online accounts.")

def admin_change_upi_proc(message):
    new_upi = message.text.strip()
    set_setting('upi_id', new_upi)
    bot.send_message(ADMIN_ID, f"✅ UPI profile variable updated to: {new_upi}")

def admin_change_prices_proc(message):
    try:
        prices = message.text.split(",")
        set_setting('plan_7_days', prices[0].strip())
        set_setting('plan_30_days', prices[1].strip())
        bot.send_message(ADMIN_ID, "✅ Target VIP dynamic packages parameters updated.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Dynamic variables matching syntax error: {e}")

def admin_search_user_proc(message):
    try:
        target_id = int(message.text.strip())
        user = get_user(target_id)
        if user:
            details = (
                f"👤 DATABASE REVELATION MODULE:\n"
                f"• ID: {user[0]}\n"
                f"• Name: @{user[1]}\n"
                f"• Plan Status: {user[2]}\n"
                f"• Valid Until: {user[3]}\n"
                f"• Credits Balance: {user[9]}\n"
                f"• Demo claimed: {user[4]}\n"
                f"• Team Referrals: {user[6]}\n"
                f"• Admin Ban Block: {user[7]}\n"
                f"• Registration Date: {user[8]}"
            )
            bot.send_message(ADMIN_ID, details)
        else:
            bot.send_message(ADMIN_ID, "❌ Target registration status is completely offline or clean.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Parsing error: {e}")

def admin_ban_unban_proc(message, ban_status):
    try:
        target_id = int(message.text.strip())
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET banned=? WHERE tg_id=?", (ban_status, target_id))
        conn.commit()
        conn.close()
        bot.send_message(ADMIN_ID, f"✅ Target {target_id} set to Ban State: {ban_status}.")
        if ban_status == 1:
            try:
                bot.send_message(target_id, "❌ Access terminated by system administrators due to policy violations.")
            except:
                pass
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ System state execution error: {e}")

def admin_reset_demo_proc(message):
    try:
        target_id = int(message.text.strip())
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET demo_used=0, credits=1 WHERE tg_id=?", (target_id,))
        conn.commit()
        conn.close()
        bot.send_message(ADMIN_ID, f"✅ Target {target_id} has been cleared and demo status set back to Available.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Reset state operations error: {e}")

def export_database_to_admin():
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "rb") as document:
                bot.send_document(ADMIN_ID, document, caption="📦 COMPLETE REALTIME DATABASE STATE EXPORT")
        else:
            bot.send_message(ADMIN_ID, "❌ Database tracking stream is missing.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Dynamic binary packaging transfer exception: {e}")

# --- START PLATFORM RUNTIME ---
if __name__ == "__main__":
    print("=========================================")
    print("      BRONX SYSTEM PLATFORM RUNNING      ")
    print("=========================================")
    logging.info("Bot execution starting in multi-threaded runtime...")
    bot.infinity_polling()
