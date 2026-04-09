#!/usr/bin/env python3
"""
Telegram to WhatsApp Forwarder Bot with MongoDB Integration
"""

import os
import sys
import json
import logging
import requests
from typing import Optional, Dict, Any
from datetime import datetime
from io import BytesIO
import qrcode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)

from database import db

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ==================== ENVIRONMENT VARIABLES ====================
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
WHATSAPP_BRIDGE_URL: str = os.environ.get("WHATSAPP_BRIDGE_URL", "http://localhost:3000")
WHATSAPP_API_KEY: str = os.environ.get("WHATSAPP_API_KEY", "change_this_secret_key")
TARGET_WHATSAPP_JID: str = os.environ.get("TARGET_WHATSAPP_JID", "")
ADMIN_TELEGRAM_IDS: str = os.environ.get("ADMIN_TELEGRAM_IDS", "")

# Parse admin IDs
ADMIN_IDS: set = set()
if ADMIN_TELEGRAM_IDS:
    try:
        ADMIN_IDS = {int(uid.strip()) for uid in ADMIN_TELEGRAM_IDS.split(",") if uid.strip().isdigit()}
    except ValueError:
        logger.warning("Invalid ADMIN_TELEGRAM_IDS format")

# ==================== VALIDATION ====================
def validate_environment() -> bool:
    """Check required environment variables."""
    required_vars = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "WHATSAPP_BRIDGE_URL": WHATSAPP_BRIDGE_URL,
        "WHATSAPP_API_KEY": WHATSAPP_API_KEY,
        "TARGET_WHATSAPP_JID": TARGET_WHATSAPP_JID,
        "MONGODB_URI": os.environ.get("MONGODB_URI", "")
    }
    
    missing = [key for key, value in required_vars.items() if not value]
    
    if missing:
        logger.critical(f"❌ Missing environment variables: {', '.join(missing)}")
        return False
    
    logger.info("✅ Environment variables validated")
    logger.info(f"📱 Target JID: {TARGET_WHATSAPP_JID}")
    logger.info(f"👑 Admin IDs: {ADMIN_IDS if ADMIN_IDS else 'None'}")
    
    return True

# ==================== SECURITY ====================
def is_user_allowed(user_id: int) -> bool:
    """Check if user is allowed (from MongoDB)."""
    return db.is_user_allowed(user_id)

def is_admin(user_id: int) -> bool:
    """Check if user is admin."""
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return True
    
    user = db.get_user(user_id)
    return user.get("is_admin", False) if user else False

async def security_filter(update: Update) -> bool:
    """Filter for user authorization."""
    if not update.effective_user:
        return False
    
    user_id = update.effective_user.id
    
    # Register/update user in MongoDB
    db.register_user(
        telegram_id=user_id,
        username=update.effective_user.username,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name
    )
    
    if not is_user_allowed(user_id):
        logger.warning(f"🚫 Unauthorized access: User {user_id}")
        return False
    
    return True

# ==================== WHATSAPP BRIDGE CLIENT ====================
class WhatsAppBridgeClient:
    """HTTP client for Baileys WhatsApp Bridge."""
    
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def check_connection(self) -> tuple[bool, str]:
        """Check WhatsApp connection status."""
        try:
            response = requests.get(
                f"{self.base_url}/health",
                timeout=10
            )
            data = response.json()
            
            if data.get('connected'):
                return True, data.get('user', 'Connected')
            else:
                status = data.get('status', 'unknown')
                qr_available = data.get('qrAvailable', False)
                
                if qr_available:
                    return False, "QR ready - scan to login"
                else:
                    return False, f"Status: {status}"
                    
        except Exception as e:
            logger.error(f"Bridge check failed: {e}")
            return False, f"Bridge unreachable"
    
    def send_text(self, text: str, telegram_user_id: int, target_jid: Optional[str] = None) -> tuple[bool, str]:
        """Send text message via bridge."""
        try:
            payload = {
                "text": text,
                "targetJid": target_jid or TARGET_WHATSAPP_JID,
                "telegramUserId": telegram_user_id
            }
            
            response = requests.post(
                f"{self.base_url}/send/text",
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return True, data.get('messageId', '')
            else:
                error = response.json().get('error', 'Unknown error')
                return False, error
                
        except Exception as e:
            return False, str(e)
    
    def send_media(self, media_url: str, media_type: str, 
                   telegram_user_id: int,
                   caption: Optional[str] = None, 
                   file_name: Optional[str] = None,
                   target_jid: Optional[str] = None) -> tuple[bool, str]:
        """Send media via bridge."""
        try:
            payload = {
                "mediaUrl": media_url,
                "mediaType": media_type,
                "targetJid": target_jid or TARGET_WHATSAPP_JID,
                "telegramUserId": telegram_user_id
            }
            
            if caption:
                payload["caption"] = caption
            if file_name:
                payload["fileName"] = file_name
            
            response = requests.post(
                f"{self.base_url}/send/media",
                headers=self.headers,
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                data = response.json()
                return True, data.get('messageId', '')
            else:
                error = response.json().get('error', 'Unknown error')
                return False, error
                
        except Exception as e:
            return False, str(e)
    
    def get_qr_code(self) -> Optional[str]:
        """Get QR code for WhatsApp login."""
        try:
            response = requests.get(
                f"{self.base_url}/qr",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get('qr')
        except Exception as e:
            logger.error(f"Failed to get QR: {e}")
        
        return None
    
    def get_status(self) -> Dict[str, Any]:
        """Get detailed bridge status."""
        try:
            response = requests.get(
                f"{self.base_url}/status",
                headers=self.headers,
                timeout=10
            )
            
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Failed to get status: {e}")
        
        return {}

# Initialize bridge client
bridge = WhatsAppBridgeClient(WHATSAPP_BRIDGE_URL, WHATSAPP_API_KEY)

# ==================== TELEGRAM HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    
    # Register user
    db.register_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name
    )
    
    connected, status_msg = bridge.check_connection()
    
    if connected:
        status_text = f"✅ Connected as: {status_msg}"
    else:
        status_text = f"⚠️ {status_msg}"
    
    keyboard = [
        [InlineKeyboardButton("📊 Status", callback_data="status"),
         InlineKeyboardButton("📱 QR Login", callback_data="qr")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
    
    if is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome = (
        f"🤖 *Telegram to WhatsApp Forwarder*\n\n"
        f"👤 Welcome, {user.first_name}!\n\n"
        f"📱 *WhatsApp Status:* {status_text}\n"
        f"🎯 *Target:* `{TARGET_WHATSAPP_JID}`\n\n"
        f"✨ Send any message to forward to WhatsApp!"
    )
    
    await update.message.reply_text(
        welcome,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    db.update_bot_stats("start_commands", 1)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status command."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    
    connected, status_msg = bridge.check_connection()
    bridge_status = bridge.get_status()
    
    # Get user stats
    user = db.get_user(user_id)
    message_count = user.get("message_count", 0) if user else 0
    
    # Get bot stats
    stats = db.get_message_stats(telegram_user_id=user_id, days=7)
    
    if connected:
        emoji = "🟢"
        status_text = "Connected"
    else:
        emoji = "🔴"
        status_text = "Disconnected"
    
    message = (
        f"📊 *Bot Status*\n\n"
        f"{emoji} WhatsApp: `{status_text}`\n"
        f"📱 Details: {status_msg}\n"
        f"🎯 Target JID: `{TARGET_WHATSAPP_JID}`\n"
        f"💾 Session: {'✅ Saved' if bridge_status.get('sessionExists') else '❌ None'}\n\n"
        f"*Your Stats (7 days):*\n"
        f"📨 Messages sent: {stats.get('total_messages', 0)}\n"
        f"✅ Successful: {stats.get('successful', 0)}\n"
        f"❌ Failed: {stats.get('failed', 0)}\n"
        f"📊 Total all-time: {message_count}\n\n"
        f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    
    keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data="status")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /qr command."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Admin only command")
        return
    
    processing = await update.message.reply_text("🔍 Checking QR code...")
    
    qr_data = bridge.get_qr_code()
    
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
            caption=(
                "📱 *WhatsApp QR Code*\n\n"
                "1️⃣ Open WhatsApp > Linked Devices\n"
                "2️⃣ Tap 'Link a Device'\n"
                "3️⃣ Scan this QR code\n\n"
                "✅ Session will be saved to MongoDB"
            ),
            parse_mode="Markdown"
        )
        
        db.update_bot_stats("qr_generated", 1)
    else:
        connected, status = bridge.check_connection()
        if connected:
            await processing.edit_text(f"✅ Already connected!\nUser: {status}")
        else:
            await processing.edit_text(f"❌ QR not available.\nStatus: {status}")

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /admin command."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Admin only")
        return
    
    # Get all stats
    total_users = db.users_collection.count_documents({})
    allowed_users = db.users_collection.count_documents({"is_allowed": True})
    total_messages = db.message_logs_collection.count_documents({})
    
    stats = db.get_message_stats(days=30)
    bot_stats = db.get_bot_stats(days=30)
    
    bridge_status = bridge.get_status()
    
    message = (
        f"👑 *Admin Panel*\n\n"
        f"*Users:*\n"
        f"👥 Total: {total_users}\n"
        f"✅ Allowed: {allowed_users}\n"
        f"⛔ Blocked: {total_users - allowed_users}\n\n"
        f"*Messages (30 days):*\n"
        f"📨 Total: {stats.get('total_messages', 0)}\n"
        f"✅ Success: {stats.get('successful', 0)}\n"
        f"❌ Failed: {stats.get('failed', 0)}\n\n"
        f"*WhatsApp:*\n"
        f"Status: {'🟢 Connected' if bridge_status.get('connected') else '🔴 Disconnected'}\n"
        f"User: {bridge_status.get('user', {}).get('name', 'N/A')}\n"
        f"Session: {'✅ Saved' if bridge_status.get('sessionExists') else '❌ None'}\n\n"
        f"*Database:*\n"
        f"💾 MongoDB: ✅ Connected\n"
        f"📊 DB: {db.db_name}"
    )
    
    keyboard = [
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
         InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        message,
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward text to WhatsApp."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    text = update.message.text
    
    processing = await update.message.reply_text("⏳ Forwarding...")
    
    success, result = bridge.send_text(text, user_id)
    
    if success:
        await processing.edit_text("✅ Forwarded successfully")
        db.log_message(user_id, "text", content=text, status="success", 
                      whatsapp_message_id=result)
        db.update_user_stats(user_id)
        db.update_bot_stats("text_messages", 1)
    else:
        await processing.edit_text(f"❌ Failed: {result[:100]}")
        db.log_message(user_id, "text", content=text, status="failed")
        db.update_bot_stats("failed_messages", 1)

async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward photo."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading...")
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        file_url = photo_file.file_path
        caption = update.message.caption or "📸 Photo"
        file_size = photo_file.file_size
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(
            file_url, "image", user_id, caption
        )
        
        if success:
            await processing.edit_text("✅ Photo forwarded")
            db.log_message(user_id, "photo", caption=caption, file_id=photo_file.file_id,
                          status="success", whatsapp_message_id=result, file_size=file_size)
            db.update_user_stats(user_id)
            db.update_bot_stats("photo_messages", 1)
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            db.log_message(user_id, "photo", caption=caption, status="failed")
            
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")
        db.log_message(user_id, "photo", status="failed")

async def handle_video_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward video."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading video...")
    
    try:
        if update.message.video.file_size > 100 * 1024 * 1024:
            await processing.edit_text("❌ Video > 100MB limit")
            return
        
        video_file = await update.message.video.get_file()
        file_url = video_file.file_path
        caption = update.message.caption or "🎬 Video"
        file_size = update.message.video.file_size
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(
            file_url, "video", user_id, caption
        )
        
        if success:
            await processing.edit_text("✅ Video forwarded")
            db.log_message(user_id, "video", caption=caption, file_id=video_file.file_id,
                          status="success", whatsapp_message_id=result, file_size=file_size)
            db.update_user_stats(user_id)
            db.update_bot_stats("video_messages", 1)
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            db.log_message(user_id, "video", status="failed")
            
    except Exception as e:
        logger.error(f"Video error: {e}")
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def handle_audio_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward audio."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading audio...")
    
    try:
        audio_file = await update.message.audio.get_file()
        file_url = audio_file.file_path
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(
            file_url, "audio", user_id
        )
        
        if success:
            await processing.edit_text("✅ Audio forwarded")
            db.log_message(user_id, "audio", file_id=audio_file.file_id,
                          status="success", whatsapp_message_id=result)
            db.update_user_stats(user_id)
            db.update_bot_stats("audio_messages", 1)
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            db.log_message(user_id, "audio", status="failed")
            
    except Exception as e:
        logger.error(f"Audio error: {e}")
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def handle_document_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forward document."""
    if not await security_filter(update):
        await update.message.reply_text("⛔ Unauthorized")
        return
    
    user_id = update.effective_user.id
    processing = await update.message.reply_text("📥 Downloading document...")
    
    try:
        document = update.message.document
        
        if document.file_size > 100 * 1024 * 1024:
            await processing.edit_text("❌ Document > 100MB limit")
            return
        
        doc_file = await document.get_file()
        file_url = doc_file.file_path
        file_name = document.file_name or "document"
        caption = update.message.caption or f"📄 {file_name}"
        
        await processing.edit_text("📤 Sending...")
        
        success, result = bridge.send_media(
            file_url, "document", user_id, caption, file_name
        )
        
        if success:
            await processing.edit_text("✅ Document forwarded")
            db.log_message(user_id, "document", caption=caption, file_id=doc_file.file_id,
                          status="success", whatsapp_message_id=result, 
                          file_size=document.file_size)
            db.update_user_stats(user_id)
            db.update_bot_stats("document_messages", 1)
        else:
            await processing.edit_text(f"❌ Failed: {result[:100]}")
            db.log_message(user_id, "document", status="failed")
            
    except Exception as e:
        logger.error(f"Document error: {e}")
        await processing.edit_text(f"❌ Error: {str(e)[:100]}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    if data == "status":
        await status_command(update, context)
    elif data == "qr":
        await qr_command(update, context)
    elif data == "help":
        await help_command(update, context)
    elif data == "admin":
        await admin_command(update, context)
    elif data == "admin_users":
        if is_admin(user_id):
            users = list(db.users_collection.find().sort("last_active", -1).limit(10))
            text = "*Recent Users:*\n\n"
            for u in users:
                text += f"• {u.get('first_name', 'N/A')} (@{u.get('username', 'N/A')})\n"
                text += f"  ID: `{u['telegram_id']}` | Msgs: {u.get('message_count', 0)}\n\n"
            await query.edit_message_text(text, parse_mode="Markdown")
    elif data == "admin_stats":
        if is_admin(user_id):
            stats = db.get_message_stats(days=30)
            text = f"*30 Day Stats:*\n\n"
            text += f"Total: {stats.get('total_messages', 0)}\n"
            text += f"Success: {stats.get('successful', 0)}\n"
            text += f"Failed: {stats.get('failed', 0)}\n"
            text += f"Rate: {stats.get('success_rate', 0):.1f}%\n\n"
            text += "*By Type:*\n"
            for t in stats.get('by_type', []):
                text += f"• {t['_id']}: {t['count']}\n"
            await query.edit_message_text(text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "🆘 *Help*\n\n"
        "*Commands:*\n"
        "/start - Start bot\n"
        "/status - Check status\n"
        "/qr - Get QR code (admin)\n"
        "/admin - Admin panel\n"
        "/help - This help\n\n"
        "*Supported:*\n"
        "✅ Text, Photos, Videos\n"
        "✅ Audio, Documents\n\n"
        "*Limits:*\n"
        "• Max file: 100MB\n"
        "• Text: 4096 chars"
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors."""
    logger.error(f"Error: {context.error}")

# ==================== MAIN ====================
def main() -> None:
    """Start the bot."""
    print("=" * 50)
    print("🚀 Telegram to WhatsApp Forwarder with MongoDB")
    print("=" * 50)
    
    if not validate_environment():
        sys.exit(1)
    
    # Connect to MongoDB
    if not db.connect():
        logger.critical("❌ Cannot start without MongoDB")
        sys.exit(1)
    
    # Check bridge connection
    connected, status = bridge.check_connection()
    if connected:
        logger.info(f"✅ WhatsApp Bridge: Connected as {status}")
    else:
        logger.warning(f"⚠️ WhatsApp Bridge: {status}")
    
    # Create application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("qr", qr_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("help", help_command))
    
    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video_message))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_message))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    # Start
    logger.info("🤖 Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
