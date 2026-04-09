#!/usr/bin/env python3
"""
Telegram to WhatsApp Forwarder Bot
100% Pure Python Implementation
"""

import os
import sys
import logging
import requests
import threading
from typing import Optional, Dict, Any
from datetime import datetime
from io import BytesIO

import qrcode
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

from database import db
from whatsapp_bridge import start_bridge_server, WhatsAppClient

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ==================== ENVIRONMENT VARIABLES ====================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WHATSAPP_BRIDGE_PORT = int(os.environ.get("WHATSAPP_BRIDGE_PORT", 3000))
WHATSAPP_BRIDGE_URL = os.environ.get("WHATSAPP_BRIDGE_URL", f"http://localhost:{WHATSAPP_BRIDGE_PORT}")
WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY", "change_this_secret_key")
TARGET_WHATSAPP_JID = os.environ.get("TARGET_WHATSAPP_JID", "")
ADMIN_TELEGRAM_IDS = os.environ.get("ADMIN_TELEGRAM_IDS", "")

# Parse admin IDs
ADMIN_IDS = set()
if ADMIN_TELEGRAM_IDS:
    try:
        ADMIN_IDS = {int(uid.strip()) for uid in ADMIN_TELEGRAM_IDS.split(",") if uid.strip().isdigit()}
    except ValueError:
        pass

# ==================== VALIDATION ====================
def validate_environment() -> bool:
    """Check required environment variables."""
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "TARGET_WHATSAPP_JID": TARGET_WHATSAPP_JID,
        "MONGODB_URI": os.environ.get("MONGODB_URI", "")
    }
    
    missing = [k for k, v in required.items() if not v]
    
    if missing:
        logger.critical(f"❌ Missing environment variables: {', '.join(missing)}")
        return False
    
    logger.info("✅ Environment validated")
    logger.info(f"📱 Target JID: {TARGET_WHATSAPP_JID}")
    return True

# ==================== SECURITY ====================
def is_user_allowed(user_id: int) -> bool:
    return db.is_user_allowed(user_id)

def is_admin(user_id: int) -> bool:
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return True
    user = db.users_collection.find_one({"telegram_id": user_id})
    return user.get("is_admin", False) if user else False

async def security_filter(update: Update) -> bool:
    if not update.effective_user:
        return False
    
    user = update.effective_user
    db.register_user(user.id, user.username, user.first_name, user.last_name)
    
    if not is_user_allowed(user.id):
        logger.warning(f"🚫 Unauthorized: {user.id}")
        return False
    
    return True

# ==================== WHATSAPP BRIDGE CLIENT ====================
class WhatsAppBridgeClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def check_connection(self) -> tuple[bool, str]:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            data = response.json()
            
            if data.get('connected'):
                return True, "Connected"
            elif data.get('qr_available'):
                return False, "QR ready - scan to login"
            else:
                return False, "Disconnected"
        except Exception as e:
            return False, "Bridge unreachable"
    
    def send_text(self, text: str, user_id: int, target_jid: str = None) -> tuple[bool, str]:
        try:
            payload = {
                "text": text,
                "targetJid": target_jid or TARGET_WHATSAPP_JID
            }
            
            response = requests.post(
                f"{self.base_url}/send/text",
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return True, data.get('message_id', '')
            else:
                return False, response.json().get('error', 'Unknown error')
        except Exception as e:
            return False, str(e)
    
    def send_media(self, media_url: str, media_type: str, user_id: int,
                   caption: str = None, filename: str = None,
                   target_jid: str = None) -> tuple[bool, str]:
        try:
            payload = {
                "mediaUrl": media_url,
                "mediaType": media_type,
                "targetJid": target_jid or TARGET_WHATSAPP_JID
            }
            if caption:
                payload["caption"] = caption
            if filename:
                payload["fileName"] = filename
            
            response = requests.post(
                f"{self.base_url}/send/media",
                headers=self.headers,
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                data = response.json()
                return True, data.get('message_id', '')
            else:
                return False, response.json().get('error', 'Unknown error')
        except Exception as e:
            return False, str(e)
    
    def get_qr(self) -> Optional[str]:
        try:
            response = requests.get(f"{self.base_url}/qr", headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json().get('qr')
        except:
            pass
        return None
    
    def get_status(self) -> Dict:
        try:
            response = requests.get(f"{self.base_url}/status", headers=self.headers, timeout=10)
            if response.status_code == 200:
                return response.json()
        except:
            pass
        return {}

# Initialize bridge client
bridge = WhatsAppBridgeClient(WHATSAPP_BRIDGE_URL, WHATSAPP_API_KEY)

# ==================== TELEGRAM HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    db.register_user(user.id, user.username, user.first_name, user.last_name)
    
    connected, status_msg = bridge.check_connection()
    status_text = f"✅ {status_msg}" if connected else f"⚠️ {status_msg}"
    
    keyboard = [
        [InlineKeyboardButton("📊 Status", callback_data="status"),
         InlineKeyboardButton("📱 QR Login", callback_data="qr")],
    ]
    
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin", callback_data="admin")])
    
    welcome = (
        f"🤖 *Telegram to WhatsApp Forwarder*\n\n"
        f"👤 Welcome, {user.first_name}!\n\n"
        f"📱 *Status:* {status_text}\n"
        f"🎯 *Target:* `{TARGET_WHATSAPP_JID}`\n\n"
        f"✨ Send any message to forward!"
    )
    
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    db.update_bot_stats("start_commands")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    connected, status_msg = bridge.check_connection()
    bridge_status = bridge.get_status()
    
    user = db.users_collection.find_one({"telegram_id": user_id})
    msg_count = user.get("message_count", 0) if user else 0
    
    emoji = "🟢" if connected else "🔴"
    
    message = (
        f"📊 *Status*\n\n"
        f"{emoji} WhatsApp: `{status_msg}`\n"
        f"🎯 Target: `{TARGET_WHATSAPP_JID}`\n"
        f"💾 Session: {'✅' if bridge_status.get('session_exists') else '❌'}\n\n"
        f"📨 Your messages: {msg_count}\n"
        f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    
    await update.message.reply_text(message, parse_mode="Markdown")

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only")
        return
    
    processing = await update.message.reply_text("🔍 Getting QR code...")
    
    qr_data = bridge.get_qr()
    
    if qr_data:
        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        
        await processing.delete()
        await update.message.reply_photo(
            photo=bio,
            caption="📱 *Scan with WhatsApp*\n\nLinked Devices → Link a Device",
            parse_mode="Markdown"
        )
    else:
        connected, status = bridge.check_connection()
        if connected:
            await processing.edit_text(f"✅ Already connected!")
        else:
            await processing.edit_text(f"❌ QR not available\nStatus: {status}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    processing = await update.message.reply_text("⏳ Forwarding...")
    
    success, result = bridge.send_text(text, user_id)
    
    if success:
        await processing.edit_text("✅ Forwarded")
        db.log_message(user_id, "text", content=text, status="success", whatsapp_message_id=result)
        db.update_user_stats(user_id)
        db.update_bot_stats("text_messages")
    else:
        await processing.edit_text(f"❌ Failed: {result[:100]}")
        db.log_message(user_id, "text", content=text, status="failed")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading...")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        file_url = photo_file.file_path
        caption = update.message.caption or "📸 Photo"
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(file_url, "image", user_id, caption)
        
        if success:
            await processing.edit_text("✅ Photo forwarded")
            db.log_message(user_id, "photo", caption=caption, file_id=photo_file.file_id,
                          status="success", whatsapp_message_id=result)
            db.update_user_stats(user_id)
            db.update_bot_stats("photo_messages")
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            db.log_message(user_id, "photo", status="failed")
            
    except Exception as e:
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading video...")
    
    try:
        if update.message.video.file_size > 100 * 1024 * 1024:
            await processing.edit_text("❌ Video > 100MB")
            return
        
        video_file = await update.message.video.get_file()
        file_url = video_file.file_path
        caption = update.message.caption or "🎬 Video"
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(file_url, "video", user_id, caption)
        
        if success:
            await processing.edit_text("✅ Video forwarded")
            db.log_message(user_id, "video", caption=caption, file_id=video_file.file_id,
                          status="success", whatsapp_message_id=result)
            db.update_user_stats(user_id)
            db.update_bot_stats("video_messages")
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            
    except Exception as e:
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading audio...")
    
    try:
        audio_file = await update.message.audio.get_file()
        file_url = audio_file.file_path
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(file_url, "audio", user_id)
        
        if success:
            await processing.edit_text("✅ Audio forwarded")
            db.log_message(user_id, "audio", file_id=audio_file.file_id,
                          status="success", whatsapp_message_id=result)
            db.update_user_stats(user_id)
            db.update_bot_stats("audio_messages")
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            
    except Exception as e:
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading document...")
    
    try:
        doc = update.message.document
        
        if doc.file_size > 100 * 1024 * 1024:
            await processing.edit_text("❌ Document > 100MB")
            return
        
        doc_file = await doc.get_file()
        file_url = doc_file.file_path
        filename = doc.file_name or "document"
        caption = update.message.caption or f"📄 {filename}"
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(file_url, "document", user_id, caption, filename)
        
        if success:
            await processing.edit_text("✅ Document forwarded")
            db.log_message(user_id, "document", caption=caption, file_id=doc_file.file_id,
                          status="success", whatsapp_message_id=result)
            db.update_user_stats(user_id)
            db.update_bot_stats("document_messages")
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            
    except Exception as e:
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "status":
        await status_command(update, context)
    elif data == "qr":
        await qr_command(update, context)
    elif data == "admin":
        user_id = update.effective_user.id
        if is_admin(user_id):
            total_users = db.users_collection.count_documents({})
            total_msgs = db.message_logs_collection.count_documents({})
            
            text = f"👑 *Admin Panel*\n\n👥 Users: {total_users}\n📨 Messages: {total_msgs}"
            await query.edit_message_text(text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🆘 *Help*\n\n"
        "/start - Start bot\n"
        "/status - Check status\n"
        "/qr - Get QR code (admin)\n"
        "/help - This help\n\n"
        "✅ Text, Photos, Videos\n"
        "✅ Audio, Documents"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

# ==================== MAIN ====================
def start_bridge_thread():
    """Start WhatsApp Bridge in a separate thread."""
    def run_bridge():
        from whatsapp_bridge import start_bridge_server
        start_bridge_server(WHATSAPP_BRIDGE_PORT)
    
    thread = threading.Thread(target=run_bridge, daemon=True)
    thread.start()
    logger.info(f"🌉 WhatsApp Bridge started on port {WHATSAPP_BRIDGE_PORT}")
    return thread

def main():
    print("=" * 50)
    print("🚀 Telegram to WhatsApp Forwarder (Pure Python)")
    print("=" * 50)
    
    if not validate_environment():
        sys.exit(1)
    
    # Connect to MongoDB
    if not db.connect():
        logger.critical("❌ Cannot connect to MongoDB")
        sys.exit(1)
    
    # Start WhatsApp Bridge in background thread
    start_bridge_thread()
    
    # Wait for bridge to initialize
    import time
    time.sleep(3)
    
    # Check bridge connection
    connected, status = bridge.check_connection()
    logger.info(f"WhatsApp Bridge: {status}")
    
    # Start Telegram Bot
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("qr", qr_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logger.info("🤖 Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
