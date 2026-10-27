"""Mini Paid File Bot v2 + Full Real-Time Mini App Sync (Balance, Orders, Auto-Download & Admin).

User flow (Bot & Mini App Connected):
  1. Buyer can use EITHER the Telegram Bot menu (📁 Premium Files) OR the Mini App (📱 Open Mini App).
  2. When a buyer submits an Add Balance Request (bKash / Nagad / Rocket + Amount + TrxID) in the Mini App:
     - The request appears inside the Mini App's Secret Admin Panel AND is sent to the Admin in the Telegram Bot with [✅ Approve Balance] / [❌ Reject] buttons!
     - Approving in either the Bot or the Mini App credits the buyer's wallet balance.
  3. Admin can add products & files in the Bot (⚙️ Admin Panel -> ➕ Product Add) just like before,
     and those products + photos + files automatically sync to the Mini App Shop!
  4. When a buyer purchases a file with Wallet Balance, the file automatically downloads to their Phone or PC
     and is also sent in the Telegram Bot chat.

TOKEN and ADMIN_ID: from environment variables (TELEGRAM_BOT_TOKEN, ADMIN_ID, WEB_APP_URL).
Database: SQLite file mini_bot.db.
"""

import json
import os
import sqlite3
import threading
import urllib.parse
import urllib.request

from flask import Flask, Response, jsonify, request, send_file
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

DB_PATH = "mini_bot.db"

BTN_PREMIUM = "📁 Premium Files"
BTN_MINIAPP = "📱 Mini App Kholo"
BTN_HOW = "❓ How it works"
BTN_SUPPORT = "🛟 Support"
BTN_BACK = "🔙 Back"
BTN_CANCEL = "❌ Cancel"
BTN_ADMIN = "⚙️ Admin Panel"

BTN_P_ADD = "➕ Product Add"
BTN_P_LIST = "📋 Product List"
BTN_P_DEL = "🗑️ Product Delete"
BTN_P_BKASH = "⚙️ bKash Number Set"
BTN_P_NAGAD = "⚙️ Nagad Number Set"
BTN_P_ROCKET = "⚙️ Rocket Number Set"
BTN_P_SUPPORT = "🛟 Support Username Set"

ST_ADD_NAME = "add_name"
ST_ADD_PHOTO = "add_photo"
ST_ADD_DESC = "add_desc"
ST_ADD_PRICE = "add_price"
ST_ADD_FILE = "add_file"
ST_SET_BKASH = "set_bkash"
ST_SET_NAGAD = "set_nagad"
ST_SET_ROCKET = "set_rocket"
ST_SET_SUPPORT = "set_support"


def _admin_id():
    try:
        return int(str(os.environ.get("ADMIN_ID", "0")).strip())
    except ValueError:
        return 0


def is_admin(user_id):
    admin_id = _admin_id()
    return admin_id != 0 and user_id == admin_id


def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = db()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            photo_id TEXT,
            file_id TEXT,
            description TEXT NOT NULL,
            price INTEGER NOT NULL,
            file_name TEXT DEFAULT ''
        )"""
    )
    # Auto-migrate existing DB if file_name column was not present
    try:
        conn.execute("ALTER TABLE products ADD COLUMN file_name TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            buyer_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER NOT NULL DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS balance_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER NOT NULL,
            buyer_name TEXT NOT NULL,
            method TEXT NOT NULL,
            amount INTEGER NOT NULL,
            trxid TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        )"""
    )
    count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO products (name, photo_id, file_id, description, price, file_name)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                "Sample Premium File",
                None,
                None,
                "Sample product - Admin Panel diye bodle nao.",
                100,
                "sample_premium_file.txt",
            ),
        )
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    conn = db()
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key, value):
    conn = db()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def list_products():
    conn = db()
    rows = conn.execute(
        "SELECT id, name, photo_id, file_id, description, price, COALESCE(file_name, '')"
        " FROM products ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def get_product(product_id):
    conn = db()
    row = conn.execute(
        "SELECT id, name, photo_id, file_id, description, price, COALESCE(file_name, '')"
        " FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    conn.close()
    return row


def get_balance(user_id):
    conn = db()
    row = conn.execute(
        "SELECT balance FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0


def add_balance(user_id, amount):
    conn = db()
    conn.execute(
        "INSERT INTO users (user_id, balance) VALUES (?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET balance = MAX(0, balance + excluded.balance)",
        (user_id, amount),
    )
    conn.commit()
    conn.close()


def set_user_balance(user_id, amount):
    conn = db()
    conn.execute(
        "INSERT INTO users (user_id, balance) VALUES (?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET balance = excluded.balance",
        (user_id, max(0, int(amount))),
    )
    conn.commit()
    conn.close()


def get_order(order_id):
    conn = db()
    row = conn.execute(
        "SELECT id, product_id, buyer_id, buyer_name, status"
        " FROM orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    conn.close()
    return row


def _build_webapp_url():
    raw_url = os.environ.get("WEB_APP_URL", "").strip()
    if not raw_url:
        render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        if render_url:
            raw_url = render_url.rstrip("/") + "/app"
    if not raw_url:
        return ""
    bot_api = os.environ.get("RENDER_EXTERNAL_URL", "").strip() or os.environ.get("BOT_SERVER_URL", "").strip()
    admin_id = _admin_id()
    parsed = urllib.parse.urlparse(raw_url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    if bot_api and "bot_api" not in q:
        q["bot_api"] = bot_api.rstrip("/")
    if admin_id and "admin_id" not in q:
        q["admin_id"] = str(admin_id)
    new_query = urllib.parse.urlencode(q)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def main_menu(user_id):
    web_app_url = _build_webapp_url()
    if web_app_url:
        mini_btn = KeyboardButton(BTN_MINIAPP, web_app=WebAppInfo(url=web_app_url))
    else:
        mini_btn = KeyboardButton(BTN_MINIAPP)
    rows = [
        [mini_btn, KeyboardButton(BTN_PREMIUM)],
        [KeyboardButton(BTN_HOW), KeyboardButton(BTN_SUPPORT)],
    ]
    if is_admin(user_id):
        rows.append([KeyboardButton(BTN_ADMIN)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def admin_menu():
    rows = [
        [KeyboardButton(BTN_P_ADD), KeyboardButton(BTN_P_LIST)],
        [KeyboardButton(BTN_P_DEL)],
        [KeyboardButton(BTN_P_BKASH), KeyboardButton(BTN_P_NAGAD)],
        [KeyboardButton(BTN_P_ROCKET), KeyboardButton(BTN_P_SUPPORT)],
        [KeyboardButton(BTN_BACK)],
    ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def cancel_menu():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(BTN_CANCEL)]], resize_keyboard=True
    )


def payment_numbers_text():
    lines = []
    bkash = get_setting("bkash_number")
    nagad = get_setting("nagad_number")
    rocket = get_setting("rocket_number")
    if bkash:
        lines.append(f"bKash: {bkash}")
    if nagad:
        lines.append(f"Nagad: {nagad}")
    if rocket:
        lines.append(f"Rocket: {rocket}")
    if not lines:
        return "Payment number Admin-er sathe inbox-e jogajog kore nao."
    return "\n".join(lines)


def send_tg_api_message(chat_id, text, reply_markup=None):
    """Thread-safe Telegram Bot API call for Flask HTTP endpoints."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id:
        return False
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        return True
    except Exception as exc:
        print(f"send_tg_api_message error: {exc}")
        return False


def send_tg_api_document(chat_id, file_id, caption=""):
    """Thread-safe Telegram Bot API sendDocument call for Flask HTTP endpoints."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not chat_id or not file_id:
        return False
    payload = {"chat_id": chat_id, "document": file_id, "caption": caption}
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendDocument",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception as exc:
        print(f"send_tg_api_document error: {exc}")
        return False


async def show_main(update, user_id):
    text = (
        "💎 **স্বাগতম Premium Files-এ!** 🎉\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🚀 আপনার প্রয়োজনীয় সবকিছু এখন এক জায়গায়!\n\n"
        "🛍️ **Shop** — পছন্দের ফাইল ও সার্ভিস দেখুন\n"
        "💳 **Add Balance** — সহজেই ব্যালেন্স যোগ করুন\n"
        "📦 **My Orders** — অর্ডারের তথ্য দেখুন\n"
        "📥 **Download** — কেনা ফাইল অটো ডাউনলোড করুন\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "✨ **সহজ, দ্রুত ও সুবিধাজনক অভিজ্ঞতা — সবকিছু এক Mini App ও Bot-এ!**\n\n"
        "👇 শুরু করতে নিচের বাটনে ক্লিক করুন।"
    )
    if update.message:
        await update.message.reply_text(
            text, reply_markup=main_menu(user_id), parse_mode="Markdown"
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user_id = update.effective_user.id
    await show_main(update, user_id)
    web_app_url = _build_webapp_url()
    if web_app_url and update.message:
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🚀 Open Mini App (VIP Store)", web_app=WebAppInfo(url=web_app_url)
                    )
                ]
            ]
        )
        await update.message.reply_text(
            "📱 Mini App খুলতে নিচের বাটনে চাপো:", reply_markup=keyboard
        )


async def handle_webapp_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.web_app_data is None:
        return
    try:
        data = json.loads(update.message.web_app_data.data)
    except (ValueError, TypeError):
        return
    if isinstance(data, dict):
        if data.get("action") and not data.get("type"):
            data["type"] = data["action"]
        if data.get("type") == "buy_product":
            data["type"] = "buy_balance"
        if data.get("product_name") and not data.get("product"):
            data["product"] = data["product_name"]

    if str(data.get("type", "")) == "add_product":
        user = update.effective_user
        if not is_admin(user.id):
            await update.message.reply_text("তুমি অ্যাডমিন না।")
            return
        pname = str(data.get("name", "")).strip()
        try:
            pprice = int(data.get("price", 0))
        except (ValueError, TypeError):
            pprice = 0
        pdesc = str(data.get("desc", "")).strip()
        if not pname or pprice <= 0:
            await update.message.reply_text("Product name + price লাগবে।")
            return
        conn = db()
        conn.execute(
            "INSERT INTO products (name, photo_id, file_id, description, price, file_name)"
            " VALUES (?, NULL, NULL, ?, ?, '')",
            (pname, pdesc, pprice),
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"✅ Mini App থেকে Product Add হয়েছে: {pname} - {pprice} টাকা"
        )
        return

    if str(data.get("type", "")) == "add_money_request":
        user = update.effective_user
        try:
            amount = int(data.get("amount", 0))
        except (ValueError, TypeError):
            amount = 0
        method = str(data.get("method", "")).strip()
        trxid = str(data.get("trxid", "")).strip()
        if amount <= 0 or not trxid:
            return
        conn = db()
        existing = conn.execute(
            "SELECT id FROM balance_requests WHERE trxid = ? AND status != 'rejected' LIMIT 1",
            (trxid,),
        ).fetchone()
        if existing:
            conn.close()
            return
        cur = conn.execute(
            "INSERT INTO balance_requests (buyer_id, buyer_name, method, amount, trxid, status)"
            " VALUES (?, ?, ?, ?, ?, 'pending')",
            (user.id, user.first_name or "", method, amount, trxid),
        )
        req_id = cur.lastrowid
        conn.commit()
        conn.close()
        await update.message.reply_text(
            "✅ Add Balance Request পেয়েছি! (Pending)\n\n"
            f"মেথড: {method} | পরিমাণ: {amount} টাকা | TrxID: {trxid}\n"
            "অ্যাডমিন যাচাই করে অনুমোদন দিলে ব্যালেন্স যোগ হবে।"
        )
        admin_id = _admin_id()
        if admin_id:
            try:
                keyboard = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Approve Balance", callback_data=f"approve_bal_{req_id}"
                            ),
                            InlineKeyboardButton(
                                "❌ Reject", callback_data=f"reject_bal_{req_id}"
                            ),
                        ]
                    ]
                )
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "💳 Mini App Add Balance Request!\n\n"
                        f"মেথড: {method}\nপরিমাণ: {amount} টাকা\nTrxID: {trxid}\n"
                        f"👤 ক্রেতা: {user.first_name} (ID: {user.id})\n"
                        f"🧾 Request ID: {req_id}"
                    ),
                    reply_markup=keyboard,
                )
            except Exception as exc:
                print(f"Admin notify failed: {exc}")
        return

    name = str(data.get("product", "")).strip()
    try:
        price = int(data.get("price", 0))
    except (ValueError, TypeError):
        price = 0
    if not name:
        return
    product_id = int(data.get("product_id", 0) or 0)
    if not product_id:
        for pid, pname, _p, _f, _d, _pr, _fn in list_products():
            if pname == name:
                product_id = pid
                break
    user = update.effective_user
    if str(data.get("type", "")) == "buy_balance":
        real_price = price
        for pid, pname, _p, _f, _d, _pr, _fn in list_products():
            if pid == product_id or pname == name:
                real_price = _pr
                break
        if get_balance(user.id) >= real_price:
            add_balance(user.id, -real_price)

    conn = db()
    cur = conn.execute(
        "INSERT INTO orders (product_id, buyer_id, buyer_name, status)"
        " VALUES (?, ?, ?, 'approved')",
        (product_id, user.id, user.first_name or ""),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()

    if product_id:
        product = get_product(product_id)
        if product and product[3]:
            _pid, _name, _photo, file_id, _desc, _price, _fname = product
            try:
                await context.bot.send_document(
                    chat_id=user.id,
                    document=file_id,
                    caption=f"📦 {name} — আপনার কেনা ফাইল পাঠানো হয়েছে!",
                )
                await update.message.reply_text(
                    "✅ সাথে সাথে ফাইল পাঠানো হয়েছে! উপরের ফাইলটি সেভ করে নাও।"
                )
            except Exception as exc:
                print(f"Instant file failed: {exc}")
            return

    await update.message.reply_text(
        "✅ Mini App অর্ডার সম্পন্ন হয়েছে!\n\n"
        f"📦 প্রোডাক্ট: {name}\n"
        f"💰 দাম: {price} টাকা"
    )


async def show_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = list_products()
    if not products:
        await update.message.reply_text(
            "কোনো প্রোডাক্ট নেই। অ্যাডমিন Product Add করলে এখানে দেখা যাবে।",
            reply_markup=main_menu(update.effective_user.id),
        )
        return
    lines = ["📁 Premium Files:\n"]
    for pid, name, _photo, _file, _desc, price, _fname in products:
        lines.append(f"{pid}. {name} - {price} টাকা")
    lines.append("\nএকটি প্রোডাক্টের নাম বা নম্বর লিখে পাঠাও।")
    await update.message.reply_text(
        "\n".join(lines), reply_markup=main_menu(update.effective_user.id)
    )


async def show_product_detail(update, product):
    pid, name, photo_id, _file, desc, price, _fname = product
    text = f"📦 {name}\n\n💰 দাম: {price} টাকা\n📝 {desc}"
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🛒 Buy", callback_data=f"buy_{pid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_main")],
        ]
    )
    if photo_id:
        await update.message.reply_photo(
            photo=photo_id, caption=text, reply_markup=keyboard
        )
    else:
        await update.message.reply_text(text, reply_markup=keyboard)


async def show_how(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "❓ How it works\n\n"
        "1. Mini App বা Premium Files থেকে প্রোডাক্ট বেছে নাও।\n"
        "2. Mini App-এ Add Balance করে কিনলে সাথে সাথে ফাইল অটো ডাউনলোড হবে!\n"
        "3. বটে সরাসরি Buy চাপলে অর্ডার অ্যাডমিনের কাছে যাবে এবং Approve করলে ফাইল পাঠাবে।"
    )
    await update.message.reply_text(
        text, reply_markup=main_menu(update.effective_user.id)
    )


async def show_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = get_setting("support_username")
    if username:
        text = f"🛟 Support: @{username}"
    else:
        text = "🛟 Support-এর জন্য অ্যাডমিনের সাথে যোগাযোগ করো।"
    await update.message.reply_text(
        text, reply_markup=main_menu(update.effective_user.id)
    )


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    text = (
        "⚙️ Admin Panel\n"
        "🛍️ Manage your shop (Bot + Mini App Connected) - choose an option below:\n\n"
        f"💳 Payment numbers:\n{payment_numbers_text()}"
    )
    await update.message.reply_text(text, reply_markup=admin_menu())


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_admin(update.effective_user.id):
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text(
            "তুমি অ্যাডমিন না.",
            reply_markup=main_menu(update.effective_user.id),
        )


async def handle_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "back_main":
        await query.edit_message_text("মেনু থেকে আবার বেছে নাও।")
        return
    if not query.data.startswith("buy_"):
        return
    try:
        product_id = int(query.data.replace("buy_", "", 1))
    except ValueError:
        return
    product = get_product(product_id)
    if product is None:
        await query.edit_message_text("প্রোডাক্ট পাওয়া যায়নি।")
        return
    _pid, name, _photo, file_id, _desc, price, _fname = product
    user = query.from_user

    # If user already has enough wallet balance in DB, deduct and deliver immediately
    if get_balance(user.id) >= price and file_id:
        add_balance(user.id, -price)
        conn = db()
        conn.execute(
            "INSERT INTO orders (product_id, buyer_id, buyer_name, status)"
            " VALUES (?, ?, ?, 'approved')",
            (product_id, user.id, user.first_name or ""),
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(
            f"✅ ওয়ালেট ব্যালেন্স থেকে {price} টাকা কেটে ফাইল পাঠানো হচ্ছে..."
        )
        try:
            await context.bot.send_document(
                chat_id=user.id,
                document=file_id,
                caption=f"✅ ইনস্ট্যান্ট ডেলিভারি!\n📦 {name}\n💰 {price} টাকা\nধন্যবাদ!",
            )
        except Exception as exc:
            print(f"Instant bot buy file send failed: {exc}")
        return

    conn = db()
    cur = conn.execute(
        "INSERT INTO orders (product_id, buyer_id, buyer_name, status)"
        " VALUES (?, ?, ?, 'pending')",
        (product_id, user.id, user.first_name or ""),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    text = (
        "✅ অর্ডার পেয়েছি! (Pending)\n\n"
        f"📦 প্রোডাক্ট: {name}\n"
        f"💰 দাম: {price} টাকা\n\n"
        "পেমেন্টের জন্য নিচের নাম্বারে পাঠাও, তারপর অ্যাডমিনকে জানাও।\n"
        f"{payment_numbers_text()}\n\n"
        "অ্যাডমিন Approve করলে বট অটো ফাইল পাঠাবে। ধন্যবাদ!"
    )
    await query.edit_message_text(text)
    admin_id = _admin_id()
    if admin_id:
        try:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Approve", callback_data=f"approve_{order_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject", callback_data=f"reject_{order_id}"
                        ),
                    ]
                ]
            )
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🛒 নতুন অর্ডার!\n\n"
                    f"📦 প্রোডাক্ট: {name}\n"
                    f"💰 দাম: {price} টাকা\n"
                    f"👤 ক্রেতা: {user.first_name} (ID: {user.id})\n"
                    f"🧾 Order ID: {order_id}\n\n"
                    "পেমেন্ট পেলে Approve চাপো, না পেলে Reject চাপো।"
                ),
                reply_markup=keyboard,
            )
        except Exception as exc:
            print(f"Admin notify failed: {exc}")


async def handle_approve_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("তুমি অ্যাডমিন না।")
        return
    if query.data.startswith("approve_bal_") or query.data.startswith("reject_bal_"):
        is_approve_bal = query.data.startswith("approve_bal_")
        try:
            req_id = int(query.data.rsplit("_", 1)[1])
        except (ValueError, IndexError):
            return
        conn = db()
        row = conn.execute(
            "SELECT buyer_id, amount, method, trxid, status FROM balance_requests WHERE id = ?",
            (req_id,),
        ).fetchone()
        if row is None:
            conn.close()
            await query.edit_message_text("Request পাওয়া যায়নি।")
            return
        buyer_id, amount, method, trxid, status = row
        if status != "pending":
            conn.close()
            await query.edit_message_text(f"এই Request already {status}।")
            return
        conn.execute(
            "UPDATE balance_requests SET status = ? WHERE id = ?",
            ("approved" if is_approve_bal else "rejected", req_id),
        )
        conn.commit()
        conn.close()
        if is_approve_bal:
            add_balance(buyer_id, amount)
            await query.edit_message_text(
                f"✅ Balance Request #{req_id} Approved — {amount} টাকা যোগ হয়েছে (TrxID: {trxid})।"
            )
            try:
                await context.bot.send_message(
                    chat_id=buyer_id,
                    text=(
                        f"✅ তোমার ওয়ালেট ব্যালেন্সে {amount} টাকা যোগ হয়েছে!\n"
                        f"💳 বর্তমান Balance: {get_balance(buyer_id)} টাকা\n"
                        "এখন Mini App বা Bot থেকে যেকোনো ফাইল কিনতে পারো।"
                    ),
                )
            except Exception as exc:
                print(f"Buyer notify failed: {exc}")
        else:
            await query.edit_message_text(f"❌ Balance Request #{req_id} Rejected।")
            try:
                await context.bot.send_message(
                    chat_id=buyer_id,
                    text="❌ তোমার Add Balance Request Reject হয়েছে। অ্যাডমিনের সাথে যোগাযোগ করো।",
                )
            except Exception as exc:
                print(f"Buyer notify failed: {exc}")
        return

    is_approve = query.data.startswith("approve_")
    try:
        order_id = int(query.data.split("_", 1)[1])
    except (ValueError, IndexError):
        return
    order = get_order(order_id)
    if order is None:
        await query.edit_message_text("অর্ডার পাওয়া যায়নি।")
        return
    _oid, product_id, buyer_id, buyer_name, status = order
    if status != "pending":
        await query.edit_message_text(f"এই অর্ডার already {status}।")
        return
    product = get_product(product_id)
    if product is None:
        conn = db()
        conn.execute(
            "UPDATE orders SET status = 'manual' WHERE id = ?", (order_id,)
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(
            f"⚠️ Order {order_id}: প্রোডাক্ট বট DB-তে পাওয়া যায়নি। বায়ারকে নিজে ফাইল পাঠাও।"
        )
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text="✅ তোমার অর্ডার Approve হয়েছে। অ্যাডমিন নিজে ফাইল পাঠাবে।",
            )
        except Exception as exc:
            print(f"Buyer notify failed: {exc}")
        return
    _pid, name, _photo, file_id, _desc, price, _fname = product
    conn = db()
    if not is_approve:
        conn.execute(
            "UPDATE orders SET status = 'rejected' WHERE id = ?", (order_id,)
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(f"❌ Order {order_id} Reject করা হয়েছে।")
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    f"❌ তোমার অর্ডার Reject হয়েছে।\n📦 {name}\n"
                    "অ্যাডমিনের সাথে যোগাযোগ করো।"
                ),
            )
        except Exception as exc:
            print(f"Buyer notify failed: {exc}")
        return
    if not file_id:
        conn.execute(
            "UPDATE orders SET status = 'manual' WHERE id = ?", (order_id,)
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(
            f"⚠️ Order {order_id} Approve করা হয়েছে, কিন্তু এই প্রোডাক্টে ফাইল নেই। বায়ারকে নিজে ফাইল পাঠাও।"
        )
        try:
            await context.bot.send_message(
                chat_id=buyer_id,
                text=(
                    "✅ তোমার অর্ডার Approve হয়েছে।\n"
                    f"📦 {name}\n"
                    "অ্যাডমিন নিজে ফাইল পাঠাবে।"
                ),
            )
        except Exception as exc:
            print(f"Buyer notify failed: {exc}")
        return
    conn.execute(
        "UPDATE orders SET status = 'approved' WHERE id = ?", (order_id,)
    )
    conn.commit()
    conn.close()
    try:
        await context.bot.send_document(
            chat_id=buyer_id,
            document=file_id,
            caption=f"✅ Order approved!\n📦 {name}\n💰 {price} টাকা\nধন্যবাদ!",
        )
    except Exception as exc:
        print(f"File send failed: {exc}")
        await query.edit_message_text(
            f"⚠️ File পাঠাতে সমস্যা হয়েছে: {exc}"
        )
        return
    await query.edit_message_text(
        f"✅ Order {order_id} Approve করা হয়েছে, ফাইল পাঠানো হয়েছে।"
    )


async def handle_delete_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("তুমি অ্যাডমিন না।")
        return
    if not query.data.startswith("del_"):
        return
    try:
        product_id = int(query.data.replace("del_", "", 1))
    except ValueError:
        return
    conn = db()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"✅ প্রোডাক্ট {product_id} ডিলিট করা হয়েছে।")
    await update.effective_message.reply_text(
        "⚙️ Admin Panel", reply_markup=admin_menu()
    )


async def admin_state_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    text = (update.message.text or "").strip()

    if text == BTN_CANCEL:
        context.user_data.clear()
        await update.message.reply_text(
            "❌ বাতিল করা হয়েছে।", reply_markup=admin_menu()
        )
        return True

    if state == ST_ADD_NAME:
        context.user_data["new_name"] = text
        context.user_data["state"] = ST_ADD_PHOTO
        await update.message.reply_text(
            "📸 প্রোডাক্টের ছবি পাঠাও, অথবা skip লিখো।",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_DESC:
        context.user_data["new_desc"] = text
        context.user_data["state"] = ST_ADD_PRICE
        await update.message.reply_text(
            "💰 দাম লিখো (শুধু সংখ্যা, যেমন: 100)।",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_PRICE:
        try:
            price = int(text)
            if price <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "দাম শুধু সংখ্যায় লিখো, যেমন: 100।",
                reply_markup=cancel_menu(),
            )
            return True
        context.user_data["new_price"] = price
        context.user_data["state"] = ST_ADD_FILE
        await update.message.reply_text(
            "📄 প্রোডাক্টের আসল ফাইল পাঠাও (document হিসেবে), অথবা skip লিখো।\n"
            "💡 ফাইল দিলে কাস্টমার কেনার সাথে সাথে অটো ডাউনলোড হয়ে যাবে!",
            reply_markup=cancel_menu(),
        )
        return True

    if state == ST_ADD_FILE and text.lower() == "skip":
        conn = db()
        conn.execute(
            "INSERT INTO products (name, photo_id, file_id, description, price, file_name)"
            " VALUES (?, ?, ?, ?, ?, '')",
            (
                context.user_data.get("new_name", ""),
                context.user_data.get("new_photo"),
                None,
                context.user_data.get("new_desc", ""),
                context.user_data.get("new_price", 0),
            ),
        )
        conn.commit()
        conn.close()
        context.user_data.clear()
        await update.message.reply_text(
            "✅ প্রোডাক্ট অ্যাড করা হয়েছে (ফাইল ছাড়া)।",
            reply_markup=admin_menu(),
        )
        return True

    if state == ST_SET_BKASH:
        set_setting("bkash_number", text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ bKash number set করা হয়েছে।", reply_markup=admin_menu()
        )
        return True

    if state == ST_SET_NAGAD:
        set_setting("nagad_number", text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Nagad number set করা হয়েছে।", reply_markup=admin_menu()
        )
        return True

    if state == ST_SET_ROCKET:
        set_setting("rocket_number", text)
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Rocket number set করা হয়েছে।", reply_markup=admin_menu()
        )
        return True

    if state == ST_SET_SUPPORT:
        username = text.lstrip("@").strip()
        set_setting("support_username", username)
        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Support username set: @{username}",
            reply_markup=admin_menu(),
        )
        return True

    return False


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != ST_ADD_PHOTO:
        return
    if not update.message.photo:
        return
    context.user_data["new_photo"] = update.message.photo[-1].file_id
    context.user_data["state"] = ST_ADD_DESC
    await update.message.reply_text(
        "📝 প্রোডাক্টের বিবরণ লিখো।", reply_markup=cancel_menu()
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != ST_ADD_FILE:
        return
    if not update.message.document:
        return
    file_id = update.message.document.file_id
    file_name = update.message.document.file_name or "premium_file.zip"
    conn = db()
    conn.execute(
        "INSERT INTO products (name, photo_id, file_id, description, price, file_name)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            context.user_data.get("new_name", ""),
            context.user_data.get("new_photo"),
            file_id,
            context.user_data.get("new_desc", ""),
            context.user_data.get("new_price", 0),
            file_name,
        ),
    )
    conn.commit()
    conn.close()
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ প্রোডাক্ট অ্যাড করা হয়েছে (ফাইলসহ: {file_name})!\n"
        "এটি বটে এবং Mini App-এ অটোমেটিক যুক্ত হয়ে গেছে।",
        reply_markup=admin_menu(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.message.text is None:
        return
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if context.user_data.get("state"):
        if text.lower() == "skip" and context.user_data.get("state") == ST_ADD_PHOTO:
            context.user_data["new_photo"] = None
            context.user_data["state"] = ST_ADD_DESC
            await update.message.reply_text(
                "📝 প্রোডাক্টের বিবরণ লিখো।", reply_markup=cancel_menu()
            )
            return
        handled = await admin_state_handler(update, context)
        if handled:
            return

    if text == BTN_BACK:
        context.user_data.clear()
        await show_main(update, user_id)
        return

    if text == BTN_MINIAPP:
        web_app_url = _build_webapp_url()
        if web_app_url:
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("📱 Open Mini App", web_app=WebAppInfo(url=web_app_url))]]
            )
            await update.message.reply_text("📱 Mini App খুলতে Open চাপো:", reply_markup=kb)
        else:
            await update.message.reply_text(
                "Mini App URL (WEB_APP_URL) এখনো দেওয়া হয়নি।",
                reply_markup=main_menu(user_id),
            )
        return

    if text == BTN_PREMIUM:
        await show_products(update, context)
        return

    if text == BTN_HOW:
        await show_how(update, context)
        return

    if text == BTN_SUPPORT:
        await show_support(update, context)
        return

    if text == BTN_ADMIN:
        if not is_admin(user_id):
            await update.message.reply_text(
                "তুমি অ্যাডমিন না।", reply_markup=main_menu(user_id)
            )
            return
        await show_admin_panel(update, context)
        return

    if is_admin(user_id):
        if text == BTN_P_ADD:
            context.user_data.clear()
            context.user_data["state"] = ST_ADD_NAME
            await update.message.reply_text(
                "➕ প্রোডাক্টের নাম লিখো।", reply_markup=cancel_menu()
            )
            return
        if text == BTN_P_LIST:
            products = list_products()
            if not products:
                msg = "কোনো প্রোডাক্ট নেই।"
            else:
                msg = "📋 Product List:\n" + "\n".join(
                    f"{pid}. {name} - {price} টাকা ({fname or 'No file'})"
                    for pid, name, _p, _f, _d, price, fname in products
                )
            await update.message.reply_text(msg, reply_markup=admin_menu())
            return
        if text == BTN_P_DEL:
            products = list_products()
            if not products:
                await update.message.reply_text(
                    "🗑️ ডিলিট করার মতো প্রোডাক্ট নেই।",
                    reply_markup=admin_menu(),
                )
                return
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"🗑️ {name}", callback_data=f"del_{pid}"
                        )
                    ]
                    for pid, name, _p, _f, _d, _pr, _fn in products
                ]
            )
            await update.message.reply_text(
                "ডিলিট করতে প্রোডাক্ট বেছে নাও:", reply_markup=keyboard
            )
            return
        if text == BTN_P_BKASH:
            context.user_data.clear()
            context.user_data["state"] = ST_SET_BKASH
            await update.message.reply_text(
                "bKash number লিখো।", reply_markup=cancel_menu()
            )
            return
        if text == BTN_P_NAGAD:
            context.user_data.clear()
            context.user_data["state"] = ST_SET_NAGAD
            await update.message.reply_text(
                "Nagad number লিখো।", reply_markup=cancel_menu()
            )
            return
        if text == BTN_P_ROCKET:
            context.user_data.clear()
            context.user_data["state"] = ST_SET_ROCKET
            await update.message.reply_text(
                "Rocket number লিখো।", reply_markup=cancel_menu()
            )
            return
        if text == BTN_P_SUPPORT:
            context.user_data.clear()
            context.user_data["state"] = ST_SET_SUPPORT
            await update.message.reply_text(
                "Support username লিখো (@ ছাড়া)।", reply_markup=cancel_menu()
            )
            return

    products = list_products()
    for pid, name, photo_id, file_id, desc, price, fname in products:
        if text == name:
            await show_product_detail(
                update, (pid, name, photo_id, file_id, desc, price, fname)
            )
            return
    if text.isdigit():
        product = get_product(int(text))
        if product:
            await show_product_detail(update, product)
            return

    await update.message.reply_text(
        "মেনু থেকে বেছে নাও, অথবা Premium Files থেকে প্রোডাক্টের নাম লিখো।",
        reply_markup=main_menu(user_id),
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Bot error: {context.error}")


# ==================== FLASK API & MINI APP SERVER ====================
app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/")
def home():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "premium_files_mini_app.html")
    if os.path.exists(html_path) and request.args.get("app") == "1":
        return send_file(html_path, mimetype="text/html")
    return "OK"


@app.route("/app")
def serve_mini_app():
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "premium_files_mini_app.html")
    if os.path.exists(html_path):
        return send_file(html_path, mimetype="text/html")
    return "premium_files_mini_app.html ফাইলটি bot.py এর সাথে একই ফোল্ডারে রাখুন।", 404


@app.route("/ping")
def ping():
    return "OK"


@app.route("/api/balance")
def api_balance():
    try:
        uid = int(request.args.get("user_id", "0"))
    except ValueError:
        uid = 0
    if not uid:
        return jsonify({"error": "user_id lagbe"}), 400
    conn = db()
    rows = conn.execute(
        "SELECT id, product_id, status FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 30",
        (uid,),
    ).fetchall()
    reqs = conn.execute(
        "SELECT id, method, amount, trxid, status FROM balance_requests WHERE buyer_id = ? ORDER BY id DESC LIMIT 30",
        (uid,),
    ).fetchall()
    conn.close()
    return jsonify(
        {
            "balance": get_balance(uid),
            "orders": [{"id": r[0], "product_id": r[1], "status": r[2]} for r in rows],
            "balance_requests": [
                {"id": r[0], "method": r[1], "amount": r[2], "trxid": r[3], "status": r[4]}
                for r in reqs
            ],
        }
    )


@app.route("/api/state", methods=["GET"])
def api_state():
    """Returns full synchronized state for Mini App: products, user balance, requests, payment numbers."""
    try:
        uid = int(request.args.get("user_id", "0"))
    except ValueError:
        uid = 0

    conn = db()
    prod_rows = conn.execute(
        "SELECT id, name, photo_id, file_id, description, price, COALESCE(file_name, '')"
        " FROM products ORDER BY id DESC"
    ).fetchall()
    user_orders = []
    user_reqs = []
    if uid:
        user_orders = conn.execute(
            "SELECT id, product_id, status FROM orders WHERE buyer_id = ? ORDER BY id DESC LIMIT 40",
            (uid,),
        ).fetchall()
        user_reqs = conn.execute(
            "SELECT id, buyer_id, buyer_name, method, amount, trxid, status FROM balance_requests"
            " WHERE buyer_id = ? ORDER BY id DESC LIMIT 40",
            (uid,),
        ).fetchall()

    all_pending_reqs = conn.execute(
        "SELECT id, buyer_id, buyer_name, method, amount, trxid, status FROM balance_requests"
        " ORDER BY id DESC LIMIT 50"
    ).fetchall()
    conn.close()

    products_out = []
    for pid, name, photo_id, file_id, desc, price, fname in prod_rows:
        products_out.append(
            {
                "id": pid,
                "name": name,
                "description": desc,
                "price": price,
                "has_photo": bool(photo_id),
                "has_file": bool(file_id),
                "file_name": fname or f"{name.replace(' ', '_')}.zip",
                "photo_url": f"/photo/{pid}" if photo_id else "",
                "download_url": f"/download/{pid}",
            }
        )

    return jsonify(
        {
            "ok": True,
            "balance": get_balance(uid) if uid else 0,
            "products": products_out,
            "settings": {
                "bkash_number": get_setting("bkash_number"),
                "nagad_number": get_setting("nagad_number"),
                "rocket_number": get_setting("rocket_number"),
                "support_username": get_setting("support_username"),
                "admin_id": str(_admin_id() or ""),
            },
            "orders": [
                {"id": r[0], "product_id": r[1], "status": r[2]} for r in user_orders
            ],
            "user_requests": [
                {
                    "id": r[0],
                    "buyer_id": r[1],
                    "buyer_name": r[2],
                    "method": r[3],
                    "amount": r[4],
                    "trxid": r[5],
                    "status": r[6],
                }
                for r in user_reqs
            ],
            "admin_requests": [
                {
                    "id": r[0],
                    "buyer_id": r[1],
                    "buyer_name": r[2],
                    "method": r[3],
                    "amount": r[4],
                    "trxid": r[5],
                    "status": r[6],
                }
                for r in all_pending_reqs
            ],
        }
    )


@app.route("/api/add_balance", methods=["POST", "OPTIONS"])
def api_add_balance():
    """Receives Add Balance request from Mini App AND sends instant notification to Bot + Admin."""
    if request.method == "OPTIONS":
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    try:
        uid = int(data.get("user_id", 0) or 0)
        amount = int(data.get("amount", 0) or 0)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid amount or user_id"}), 400

    buyer_name = str(data.get("buyer_name") or data.get("username") or "Buyer").strip()
    method = str(data.get("method", "bkash")).strip()
    trxid = str(data.get("trxid", "")).strip().upper()
    if amount <= 0 or not trxid:
        return jsonify({"ok": False, "error": "Amount and TrxID required"}), 400

    conn = db()
    existing = conn.execute(
        "SELECT id, status FROM balance_requests WHERE trxid = ? AND status != 'rejected' LIMIT 1",
        (trxid,),
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({"ok": True, "request_id": existing[0], "status": existing[1], "duplicate": True})

    cur = conn.execute(
        "INSERT INTO balance_requests (buyer_id, buyer_name, method, amount, trxid, status)"
        " VALUES (?, ?, ?, ?, ?, 'pending')",
        (uid, buyer_name, method, amount, trxid),
    )
    req_id = cur.lastrowid
    conn.commit()
    conn.close()

    # 1. Notify Buyer in Telegram Bot chat
    if uid:
        send_tg_api_message(
            uid,
            "✅ আপনার Add Balance Request পাওয়া গেছে! (Pending)\n\n"
            f"💳 মেথড: {method.upper()}\n"
            f"💰 পরিমাণ: ৳{amount} টাকা\n"
            f"🧾 TrxID: {trxid}\n\n"
            "অ্যাডমিন যাচাই করে Approve করলে আপনার ওয়ালেটে ব্যালেন্স যোগ হয়ে যাবে।",
        )

    # 2. Notify Admin in Telegram Bot chat with Approve / Reject inline buttons
    admin_id = _admin_id()
    if admin_id:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve Balance", "callback_data": f"approve_bal_{req_id}"},
                    {"text": "❌ Reject", "callback_data": f"reject_bal_{req_id}"},
                ]
            ]
        }
        send_tg_api_message(
            admin_id,
            "💳 নতুন Add Balance Request (Mini App + Bot)!\n\n"
            f"🏦 মেথড: {method.upper()}\n"
            f"💰 পরিমাণ: ৳{amount} টাকা\n"
            f"🔑 TrxID: {trxid}\n"
            f"👤 ক্রেতা: {buyer_name} (ID: {uid})\n"
            f"🧾 Request ID: #{req_id}\n\n"
            "নিচের বাটনে ক্লিক করে অথবা Mini App Admin Panel থেকে Approve করুন:",
            reply_markup=reply_markup,
        )

    return jsonify({"ok": True, "request_id": req_id, "status": "pending"})


@app.route("/api/admin/balance_action", methods=["POST", "OPTIONS"])
def api_admin_balance_action():
    """Allows approving/rejecting balance requests or adjusting balance from Mini App Admin Panel."""
    if request.method == "OPTIONS":
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip()

    if action in ("approve", "reject"):
        try:
            req_id = int(data.get("request_id", 0) or 0)
        except (ValueError, TypeError):
            req_id = 0
        trxid = str(data.get("trxid", "")).strip().upper()

        conn = db()
        row = None
        if req_id:
            row = conn.execute(
                "SELECT id, buyer_id, amount, method, trxid, status FROM balance_requests WHERE id = ?",
                (req_id,),
            ).fetchone()
        if not row and trxid:
            row = conn.execute(
                "SELECT id, buyer_id, amount, method, trxid, status FROM balance_requests WHERE trxid = ? ORDER BY id DESC LIMIT 1",
                (trxid,),
            ).fetchone()
        if not row:
            conn.close()
            return jsonify({"ok": False, "error": "Request not found"}), 404

        rid, buyer_id, amount, method, r_trxid, status = row
        if status == "pending":
            new_status = "approved" if action == "approve" else "rejected"
            conn.execute(
                "UPDATE balance_requests SET status = ? WHERE id = ?",
                (new_status, rid),
            )
            conn.commit()
            conn.close()
            if action == "approve":
                add_balance(buyer_id, amount)
                if buyer_id:
                    send_tg_api_message(
                        buyer_id,
                        f"✅ আপনার ওয়ালেটে ৳{amount} টাকা যোগ হয়েছে! (TrxID: {r_trxid})\n"
                        f"💳 নতুন ব্যালেন্স: ৳{get_balance(buyer_id)} টাকা",
                    )
            else:
                if buyer_id:
                    send_tg_api_message(
                        buyer_id,
                        f"❌ আপনার ৳{amount} টাকার Add Balance Request (TrxID: {r_trxid}) বাতিল করা হয়েছে।",
                    )
        else:
            conn.close()
        return jsonify({"ok": True, "balance": get_balance(buyer_id)})

    if action == "adjust_balance":
        try:
            uid = int(data.get("user_id", 0) or 0)
            delta = int(data.get("delta", 0) or 0)
        except (ValueError, TypeError):
            return jsonify({"ok": False}), 400
        if data.get("set_zero"):
            set_user_balance(uid, 0)
        elif delta:
            add_balance(uid, delta)
        return jsonify({"ok": True, "balance": get_balance(uid)})

    return jsonify({"ok": False}), 400


@app.route("/api/buy", methods=["POST", "OPTIONS"])
def api_buy():
    """Handles instant wallet purchase from Mini App, approves order for auto-download & sends file in Bot chat."""
    if request.method == "OPTIONS":
        return jsonify({"ok": True})
    data = request.get_json(silent=True) or {}
    try:
        uid = int(data.get("user_id", 0) or 0)
        product_id = int(data.get("product_id", 0) or 0)
        price = int(data.get("price", 0) or 0)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Invalid parameters"}), 400

    buyer_name = str(data.get("buyer_name") or data.get("username") or "Buyer").strip()
    product_name = str(data.get("product_name", "Premium File")).strip()

    product = get_product(product_id) if product_id else None
    if not product and product_name:
        for p in list_products():
            if p[1] == product_name:
                product = p
                product_id = p[0]
                break

    real_price = product[5] if product else price
    if uid and get_balance(uid) >= real_price:
        add_balance(uid, -real_price)

    conn = db()
    cur = conn.execute(
        "INSERT INTO orders (product_id, buyer_id, buyer_name, status)"
        " VALUES (?, ?, ?, 'approved')",
        (product_id, uid, buyer_name),
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()

    # Also send the file directly in Telegram Bot chat if product has a Telegram file_id
    if product and product[3] and uid:
        send_tg_api_document(
            uid,
            product[3],
            caption=f"✅ ক্রয় সফল হয়েছে!\n📦 {product[1]}\n💰 ৳{real_price} টাকা\nধন্যবাদ!",
        )

    admin_id = _admin_id()
    if admin_id:
        send_tg_api_message(
            admin_id,
            "🎉 Mini App ফাইল বিক্রয় সম্পন্ন!\n\n"
            f"📦 প্রোডাক্ট: {product[1] if product else product_name}\n"
            f"💰 দাম: ৳{real_price} টাকা\n"
            f"👤 ক্রেতা: {buyer_name} (ID: {uid})\n"
            f"🧾 Order ID: #{order_id} (Auto-Delivered)",
        )

    return jsonify(
        {
            "ok": True,
            "order_id": order_id,
            "balance": get_balance(uid) if uid else 0,
            "download_url": f"/download/{product_id}?user_id={uid}&order_id={order_id}" if product_id else "",
        }
    )


@app.route("/photo/<int:product_id>")
def product_photo(product_id):
    """Streams product cover photo from Telegram so products added in Bot show their image in Mini App."""
    product = get_product(product_id)
    if not product or not product[2]:
        return "No photo", 404
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return "Token missing", 500
    try:
        photo_id = product[2]
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(photo_id)}",
            timeout=10,
        ) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        path = info["result"]["file_path"]
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{path}", timeout=15
        ) as resp:
            data = resp.read()
        return Response(data, mimetype="image/jpeg")
    except Exception as exc:
        print(f"Photo stream failed: {exc}")
        return "Photo failed", 500


@app.route("/download/<int:product_id>")
def download_product(product_id):
    """Streams the purchased file directly to the buyer's Phone or PC with its original filename."""
    try:
        uid = int(request.args.get("user_id", "0"))
    except ValueError:
        uid = 0
    product = get_product(product_id)
    if not product or not product[3]:
        return "File nei", 404

    conn = db()
    ok = conn.execute(
        "SELECT 1 FROM orders WHERE product_id = ? AND (buyer_id = ? OR ? = 0) AND status = 'approved' LIMIT 1",
        (product_id, uid, uid),
    ).fetchone()
    # If auto-download triggered right at purchase moment before /api/buy completed, auto-record approved order
    if not ok and request.args.get("auto") == "1":
        conn.execute(
            "INSERT INTO orders (product_id, buyer_id, buyer_name, status) VALUES (?, ?, 'MiniApp Buyer', 'approved')",
            (product_id, uid),
        )
        conn.commit()
        ok = (1,)
    conn.close()

    if not ok:
        return "Approved order nei", 403

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return "Token nei", 500
    try:
        file_id = product[3]
        saved_fname = (product[6] or "").strip()
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(file_id)}",
            timeout=10,
        ) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        path = info["result"]["file_path"]
        with urllib.request.urlopen(
            f"https://api.telegram.org/file/bot{token}/{path}", timeout=30
        ) as resp:
            data = resp.read()

        if saved_fname:
            filename = saved_fname.replace("/", "_").replace('"', "")
        else:
            ext = os.path.splitext(path)[1] or ".zip"
            base_name = (product[1] or "premium_file").replace("/", "_").replace('"', "")
            filename = base_name if base_name.endswith(ext) else f"{base_name}{ext}"

        return Response(
            data,
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Access-Control-Expose-Headers": "Content-Disposition",
            },
        )
    except Exception as exc:
        print(f"Download failed: {exc}")
        return "Download failed", 500


def run_flask():
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN environment variable is not set.")
        return
    if _admin_id() == 0:
        print("WARNING: ADMIN_ID is not set. Admin Panel will not work.")
    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.WEB_APP_DATA, handle_webapp_data)
    )
    application.add_handler(CallbackQueryHandler(handle_buy, pattern="^buy_"))
    application.add_handler(
        CallbackQueryHandler(handle_buy, pattern="^back_main$")
    )
    application.add_handler(
        CallbackQueryHandler(handle_approve_reject, pattern="^approve_")
    )
    application.add_handler(
        CallbackQueryHandler(handle_approve_reject, pattern="^reject_")
    )
    application.add_handler(
        CallbackQueryHandler(handle_delete_pick, pattern="^del_")
    )
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_error_handler(error_handler)
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()