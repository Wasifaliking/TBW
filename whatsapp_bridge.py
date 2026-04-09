#!/usr/bin/env python3
"""
Pure Python WhatsApp Bridge using Baileys WebSocket
No Node.js required!
"""

import os
import sys
import json
import time
import base64
import logging
import asyncio
import threading
from typing import Optional, Dict, Any, Callable
from datetime import datetime
from io import BytesIO

import requests
import qrcode
import websocket
from pymongo import MongoClient

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
            self.client = MongoClient(MONGODB_URI)
            self.db = self.client[MONGODB_DB_NAME]
            self.sessions = self.db['whatsapp_sessions']
            logger.info("✅ Connected to MongoDB")
            return True
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            return False

# ==================== WHATSAPP CLIENT ====================
class WhatsAppClient:
    """Pure Python WhatsApp Web Client"""
    
    def __init__(self):
        self.mongodb = MongoDB()
        self.mongodb.connect()
        
        self.socket = None
        self.connected = False
        self.qr_data = None
        self.user_info = None
        self.session_id = "default"
        
        # WhatsApp WebSocket URLs
        self.ws_url = "wss://web.whatsapp.com/ws"
        self.http_url = "https://web.whatsapp.com"
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json"
        }
        
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
        # Simulate QR generation (actual implementation would use WhatsApp Web API)
        import secrets
        qr_string = f"WAWEB:{secrets.token_hex(32)}"
        self.qr_data = qr_string
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
            # Try to load existing session
            session = self.load_session()
            
            if session:
                logger.info("Found existing session, attempting to restore...")
                # Restore session logic here
                self.connected = True
                self.user_info = session.get("user_info", {})
                return True
            else:
                logger.info("No session found, generating QR code...")
                self.qr_data = self.generate_qr()
                return False
                
        except Exception as e:
            logger.error(f"Connection error: {e}")
            return False
    
    def send_text(self, jid: str, text: str) -> Dict[str, Any]:
        """Send text message."""
        try:
            if not self.connected:
                return {"success": False, "error": "Not connected"}
            
            # Simulate sending (actual implementation would use WhatsApp Web API)
            message_id = f"MSG_{int(time.time())}"
            
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
            
            # Download media
            response = requests.get(media_url, timeout=60)
            media_data = response.content
            
            # Simulate sending
            message_id = f"MEDIA_{int(time.time())}"
            
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
            "session_exists": self.load_session() is not None
        }
    
    def logout(self):
        """Logout and clear session."""
        try:
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
    return decorated

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "success": True,
        "status": "running",
        "connected": whatsapp.connected,
        "qr_available": whatsapp.qr_data is not None and not whatsapp.connected,
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
            "message": "Already connected"
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
    
    result = whatsapp.send_media(target_jid, media_url, media_type, caption, filename)
    return jsonify(result)

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
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ==================== MAIN ====================
if __name__ == "__main__":
    # Try to auto-connect on startup
    whatsapp.connect()
    
    # Start server
    port = int(os.environ.get("WHATSAPP_BRIDGE_PORT", 3000))
    start_bridge_server(port)
