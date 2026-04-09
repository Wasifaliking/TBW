"""
MongoDB Database Helper for Telegram Bot
"""

import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pymongo import MongoClient, ASCENDING, DESCENDING

logger = logging.getLogger(__name__)

class MongoDB:
    """MongoDB connection and operations handler."""
    
    def __init__(self):
        self.client: Optional[MongoClient] = None
        self.db = None
        self.uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        self.db_name = os.environ.get("MONGODB_DB_NAME", "whatsapp_bot")
        
        self.users_collection = None
        self.message_logs_collection = None
        self.bot_stats_collection = None
        
    def connect(self) -> bool:
        """Establish connection to MongoDB."""
        try:
            self.client = MongoClient(self.uri, serverSelectionTimeoutMS=5000)
            self.client.admin.command('ping')
            self.db = self.client[self.db_name]
            
            self.users_collection = self.db['telegram_users']
            self.message_logs_collection = self.db['message_logs']
            self.bot_stats_collection = self.db['bot_statistics']
            
            self._create_indexes()
            logger.info(f"✅ Connected to MongoDB: {self.db_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            return False
    
    def _create_indexes(self):
        """Create necessary indexes."""
        try:
            self.users_collection.create_index([("telegram_id", ASCENDING)], unique=True)
            self.message_logs_collection.create_index([("timestamp", DESCENDING)])
            logger.debug("Indexes created")
        except Exception as e:
            logger.warning(f"Index warning: {e}")
    
    def register_user(self, telegram_id: int, username: str = None, 
                      first_name: str = None, last_name: str = None) -> bool:
        """Register or update a Telegram user."""
        try:
            existing = self.users_collection.find_one({"telegram_id": telegram_id})
            
            if existing:
                update_data = {
                    "$set": {
                        "last_active": datetime.utcnow(),
                        "updated_at": datetime.utcnow()
                    }
                }
                if username is not None:
                    update_data["$set"]["username"] = username
                if first_name is not None:
                    update_data["$set"]["first_name"] = first_name
                if last_name is not None:
                    update_data["$set"]["last_name"] = last_name
                    
                self.users_collection.update_one(
                    {"telegram_id": telegram_id},
                    update_data
                )
            else:
                user_data = {
                    "telegram_id": telegram_id,
                    "is_allowed": True,
                    "is_admin": False,
                    "message_count": 0,
                    "created_at": datetime.utcnow(),
                    "last_active": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
                if username:
                    user_data["username"] = username
                if first_name:
                    user_data["first_name"] = first_name
                if last_name:
                    user_data["last_name"] = last_name
                    
                self.users_collection.insert_one(user_data)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to register user: {e}")
            return False
    
    def is_user_allowed(self, telegram_id: int) -> bool:
        """Check if user is allowed."""
        user = self.users_collection.find_one({"telegram_id": telegram_id})
        if not user:
            self.register_user(telegram_id)
            return True
        return user.get("is_allowed", True)
    
    def log_message(self, telegram_user_id: int, message_type: str, 
                    content: str = None, file_id: str = None,
                    status: str = "success", whatsapp_message_id: str = None,
                    file_size: int = None) -> bool:
        """Log a forwarded message."""
        try:
            log_entry = {
                "telegram_user_id": telegram_user_id,
                "message_type": message_type,
                "content": content[:500] if content else None,
                "file_id": file_id,
                "status": status,
                "whatsapp_message_id": whatsapp_message_id,
                "file_size": file_size,
                "timestamp": datetime.utcnow()
            }
            self.message_logs_collection.insert_one(log_entry)
            return True
        except Exception as e:
            logger.error(f"Failed to log message: {e}")
            return False
    
    def update_user_stats(self, telegram_id: int):
        """Update user message count."""
        try:
            self.users_collection.update_one(
                {"telegram_id": telegram_id},
                {
                    "$inc": {"message_count": 1},
                    "$set": {"last_active": datetime.utcnow()}
                }
            )
        except Exception as e:
            logger.error(f"Failed to update stats: {e}")
    
    def update_bot_stats(self, stat_type: str, value: int = 1):
        """Update bot statistics."""
        try:
            self.bot_stats_collection.update_one(
                {"stat_type": stat_type, "date": datetime.utcnow().strftime("%Y-%m-%d")},
                {"$inc": {"value": value}, "$set": {"updated_at": datetime.utcnow()}},
                upsert=True
            )
        except Exception as e:
            logger.error(f"Failed to update bot stats: {e}")
    
    def close(self):
        """Close connection."""
        if self.client:
            self.client.close()

# Singleton instance
db = MongoDB()
