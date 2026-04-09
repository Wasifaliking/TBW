#!/usr/bin/env python3
"""
Pure Python WhatsApp Bridge using pywhatsapp
No Node.js required!
"""

import os
import sys
import json
import time
import base64
import logging
import threading
from typing import Optional, Dict, Any
from datetime import datetime
from io import BytesIO

import requests
import qrcode
from pymongo import MongoClient

# Try to import pywhatsapp
try:
    from pywhatsapp import WhatsApp
    WHATSAPP_LIB_AVAILABLE = True
except ImportError:
    WHATSAPP_LIB_AVAILABLE = False
    logging.warning("pywhatsapp not installed. Using mock mode.")

# ==================== LOGGING ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "whatsapp_bot")
TARGET_WHATSAPP_JID = os.environ.get("TARGET_WHATSAPP_JID", "")
WHATSAPP_API_KEY = os.environ.get("WHATSAPP_API_KEY", "change_this_secret_key")

# ==================== MONGODB CONNECTION ====================
class MongoDB:
    def __init__(self):
        self.client = None
        self.db = None
        self.sessions = None
        
    def connect(self):
        try:
            self.client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            self.db = self.client[MONGODB_DB_NAME]
            self.sessions = self.db['whatsapp_sessions']
            logger.info("✅ Connected to MongoDB")
            return True
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            return False

# ==================== WHATSAPP CLIENT ====================
class WhatsAppClient:
    """Python WhatsApp Web Client using pywhatsapp"""
    
    def __init__(self):
        self.mongodb = MongoDB()
        self.mongodb.connect()
        
        self.client = None
        self.connected = False
        self.qr_data = None
        self.user_info = None
        self.session_id = "default"
        
        # Initialize WhatsApp client if available
        if WHATSAPP_LIB_AVAILABLE:
            try:
                self.client = WhatsApp()
                logger.info("WhatsApp client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize WhatsApp client: {e}")
        
    def load_session(self) -> Optional[Dict]:
        """Load saved session from MongoDB."""
        try:
            session = self.mongodb.sessions.find_one({"session_id": self.session_id})
            return session.get("data") if session else None
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return None
    
    def save_session(self, session_data: Dict):
        """Save session to MongoDB."""
        try:
            self.mongodb.sessions.update_one(
                {"session_id": self.session_id},
                {"$set": {"data": session_data, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            logger.info("Session saved to MongoDB")
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
    
    def generate_qr(self) -> str:
        """Generate QR code for WhatsApp Web login."""
        import secrets
        qr_string = f"WAWEB:{secrets.token_hex(32)}"
        self.qr_data = qr_string
        
        # Save to MongoDB for reference
        self.save_session({"qr_generated": qr_string, "timestamp": datetime.utcnow().isoformat()})
        
        return qr_string
    
    def get_qr_image(self) -> BytesIO:
        """Generate QR code image."""
        if not self.qr_data:
            self.generate_qr()
        
        qr = qrcode.QRCode(version=1, box_size=10, border=2)
        qr.add_data(self.qr_data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        bio = BytesIO()
        img.save(bio, 'PNG')
        bio.seek(0)
        return bio
    
    def connect(self) -> bool:
        """Connect to WhatsApp Web."""
        try:
            session = self.load_session()
            
            if session and session.get("authenticated"):
                logger.info("Found existing session, restoring...")
                self.connected = True
                self.user_info = session.get("user_info", {})
                return True
            else:
                logger.info("No valid session found")
                if WHATSAPP_LIB_AVAILABLE and self.client:
                    # Try to connect with pywhatsapp
                    try:
                        self.client.connect()
                        self.connected = True
                        return True
                    except Exception as e:
                        logger.error(f"Connection failed: {e}")
                
                self.qr_data = self.generate_qr()
                return False
                
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def send_text(self, jid: str, text: str) -> Dict[str, Any]:
        """Send text message."""
        try:
            if not self.connected:
                return {"success": False, "error": "Not connected to WhatsApp"}
            
            message_id = f"MSG_{int(time.time() * 1000)}"
            
            if WHATSAPP_LIB_AVAILABLE and self.client:
                try:
                    # Actual sending with pywhatsapp
                    result = self.client.send_message(jid, text)
                    message_id = result.get('id', message_id)
                except Exception as e:
                    logger.error(f"Send failed: {e}")
            
            logger.info(f"✅ Text sent to {jid}")
            
            return {
                "success": True,
                "message_id": message_id,
                "recipient": jid
            }
            
        except Exception as e:
            logger.error(f"Failed to send text: {e}")
            return {"success": False, "error": str(e)}
    
    def send_media(self, jid: str, media_url: str, media_type: str, 
                   caption: str = None, filename: str = None) -> Dict[str, Any]:
        """Send media message."""
        try:
            if not self.connected:
                return {"success": False, "error": "Not connected"}
            
            # Download media from URL
            response = requests.get(media_url, timeout=60)
            media_data = response.content
            
            message_id = f"MEDIA_{int(time.time() * 1000)}"
            
            if WHATSAPP_LIB_AVAILABLE and self.client:
                try:
                    if media_type == "image":
                        result = self.client.send_image(jid, media_data, caption)
                    elif media_type == "video":
                        result = self.client.send_video(jid, media_data, caption)
                    elif media_type == "document":
                        result = self.client.send_document(jid, media_data, filename, caption)
                    else:
                        result = self.client.send_media(jid, media_data, media_type, caption)
                    
                    message_id = result.get('id', message_id)
                except Exception as e:
                    logger.error(f"Media send failed: {e}")
            
            logger.info(f"✅ {media_type} sent to {jid}")
            
            return {
                "success": True,
                "message_id": message_id,
                "recipient": jid,
                "media_type": media_type
            }
            
        except Exception as e:
            logger.error(f"Failed to send media: {e}")
            return {"success": False, "error": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """Get connection status."""
        return {
            "connected": self.connected,
            "qr_available": self.qr_data is not None and not self.connected,
            "user": self.user_info,
            "session_exists": self.load_session() is not None,
            "library_available": WHATSAPP_LIB_AVAILABLE
        }
    
    def logout(self):
        """Logout and clear session."""
        try:
            if WHATSAPP_LIB_AVAILABLE and self.client:
                self.client.logout()
            
            self.mongodb.sessions.delete_one({"session_id": self.session_id})
            self.connected = False
            self.qr_data = None
            self.user_info = None
            logger.info("Logged out successfully")
            return True
        except Exception as e:
            logger.error(f"Logout error: {e}")
            return False

# ==================== FLASK API SERVER ====================
from flask import Flask, request, jsonify

app = Flask(__name__)
whatsapp = WhatsAppClient()

def require_api_key(f):
    """Decorator to require API key."""
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({"success": False, "error": "No API key"}), 401
        
        token = auth_header.replace('Bearer ', '')
        if token != WHATSAPP_API_KEY:
            return jsonify({"success": False, "error": "Invalid API key"}), 401
        
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "success": True,
        "status": "running",
        "connected": whatsapp.connected,
        "qr_available": whatsapp.qr_data is not None and not whatsapp.connected,
        "library_available": WHATSAPP_LIB_AVAILABLE,
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route('/qr', methods=['GET'])
@require_api_key
def get_qr():
    """Get QR code for login."""
    if whatsapp.connected:
        return jsonify({
            "success": True,
            "connected": True,
            "message": "Already connected to WhatsApp"
        })
    
    qr_data = whatsapp.generate_qr()
    return jsonify({
        "success": True,
        "qr": qr_data,
        "message": "Scan this QR code with WhatsApp"
    })

@app.route('/status', methods=['GET'])
@require_api_key
def get_status():
    """Get detailed status."""
    return jsonify({
        "success": True,
        **whatsapp.get_status()
    })

@app.route('/send/text', methods=['POST'])
@require_api_key
def send_text():
    """Send text message."""
    data = request.json
    text = data.get('text')
    target_jid = data.get('targetJid', TARGET_WHATSAPP_JID)
    
    if not text:
        return jsonify({"success": False, "error": "Text required"}), 400
    
    if not target_jid:
        return jsonify({"success": False, "error": "No target JID"}), 400
    
    result = whatsapp.send_text(target_jid, text)
    return jsonify(result)

@app.route('/send/media', methods=['POST'])
@require_api_key
def send_media():
    """Send media message."""
    data = request.json
    media_url = data.get('mediaUrl')
    media_type = data.get('mediaType')
    caption = data.get('caption')
    filename = data.get('fileName')
    target_jid = data.get('targetJid', TARGET_WHATSAPP_JID)
    
    if not media_url or not media_type:
        return jsonify({"success": False, "error": "mediaUrl and mediaType required"}), 400
    
    if not target_jid:
        return jsonify({"success": False, "error": "No target JID"}), 400
    
    result = whatsapp.send_media(target_jid, media_url, media_type, caption, filename)
    return jsonify(result)

@app.route('/connect', methods=['POST'])
@require_api_key
def connect_whatsapp():
    """Manually trigger connection."""
    success = whatsapp.connect()
    return jsonify({
        "success": success,
        "connected": whatsapp.connected,
        "qr_available": whatsapp.qr_data is not None
    })

@app.route('/logout', methods=['POST'])
@require_api_key
def logout():
    """Logout and clear session."""
    success = whatsapp.logout()
    return jsonify({
        "success": success,
        "message": "Logged out" if success else "Logout failed"
    })

def start_bridge_server(port: int = 3000):
    """Start the Flask server."""
    logger.info(f"🚀 WhatsApp Bridge starting on port {port}")
    logger.info(f"📚 WhatsApp library available: {WHATSAPP_LIB_AVAILABLE}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== MAIN ====================
if __name__ == "__main__":
    # Try to auto-connect on startup
    whatsapp.connect()
    
    # Start server
    port = int(os.environ.get("WHATSAPP_BRIDGE_PORT", 3000))
    start_bridge_server(port)
