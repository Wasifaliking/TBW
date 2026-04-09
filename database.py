"""
MongoDB Database Helper for Telegram Bot
Manages user data, message logs, and bot configuration
"""

import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

logger = logging.getLogger(__name__)

class MongoDB:
    """MongoDB connection and operations handler."""
    
    def __init__(self):
        self.client: Optional[MongoClient] = None
        self.db = None
        self.uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
        self.db_name = os.environ.get("MONGODB_DB_NAME", "whatsapp_bot")
        
        # Collections
        self.users_collection = None
        self.message_logs_collection = None
        self.bot_stats_collection = None
        self.telegram_sessions_collection = None
        
    def connect(self) -> bool:
        """Establish connection to MongoDB."""
        try:
            self.client = MongoClient(
                self.uri,
                maxPoolSize=10,
                minPoolSize=2,
                connectTimeoutMS=10000,
                serverSelectionTimeoutMS=5000,
                socketTimeoutMS=45000
            )
            
            # Test connection
            self.client.admin.command('ping')
            
            self.db = self.client[self.db_name]
            
            # Initialize collections
            self.users_collection = self.db['telegram_users']
            self.message_logs_collection = self.db['message_logs']
            self.bot_stats_collection = self.db['bot_statistics']
            self.telegram_sessions_collection = self.db['telegram_sessions']
            
            # Create indexes
            self._create_indexes()
            
            logger.info(f"✅ Connected to MongoDB: {self.db_name}")
            return True
            
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            return False
    
    def _create_indexes(self):
        """Create necessary indexes for collections."""
        try:
            # Users collection indexes
            self.users_collection.create_index([("telegram_id", ASCENDING)], unique=True)
            self.users_collection.create_index([("username", ASCENDING)])
            self.users_collection.create_index([("created_at", DESCENDING)])
            
            # Message logs indexes
            self.message_logs_collection.create_index([("timestamp", DESCENDING)])
            self.message_logs_collection.create_index([("telegram_user_id", ASCENDING)])
            self.message_logs_collection.create_index([("message_type", ASCENDING)])
            self.message_logs_collection.create_index([("status", ASCENDING)])
            
            # Telegram sessions indexes
            self.telegram_sessions_collection.create_index([("user_id", ASCENDING)], unique=True)
            self.telegram_sessions_collection.create_index([("updated_at", DESCENDING)])
            
            logger.debug("Indexes created successfully")
            
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")
    
    # ==================== USER OPERATIONS ====================
    
    def register_user(self, telegram_id: int, username: str = None, 
                      first_name: str = None, last_name: str = None) -> bool:
        """Register or update a Telegram user."""
        try:
            user_data = {
                "telegram_id": telegram_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "is_allowed": True,  # Default allowed
                "is_admin": False,
                "message_count": 0,
                "created_at": datetime.utcnow(),
                "last_active": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            
            self.users_collection.update_one(
                {"telegram_id": telegram_id},
                {
                    "$set": {
                        "username": username,
                        "first_name": first_name,
                        "last_name": last_name,
                        "last_active": datetime.utcnow(),
                        "updated_at": datetime.utcnow()
                    },
                    "$setOnInsert": user_data
                },
                upsert=True
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to register user: {e}")
            return False
    
    def get_user(self, telegram_id: int) -> Optional[Dict]:
        """Get user by Telegram ID."""
        try:
            return self.users_collection.find_one({"telegram_id": telegram_id})
        except Exception as e:
            logger.error(f"Failed to get user: {e}")
            return None
    
    def is_user_allowed(self, telegram_id: int) -> bool:
        """Check if user is allowed to use bot."""
        user = self.get_user(telegram_id)
        if not user:
            # Auto-register new user
            self.register_user(telegram_id)
            return True
        
        return user.get("is_allowed", True)
    
    def update_user_stats(self, telegram_id: int, increment_messages: int = 1):
        """Update user message count and last active time."""
        try:
            self.users_collection.update_one(
                {"telegram_id": telegram_id},
                {
                    "$inc": {"message_count": increment_messages},
                    "$set": {
                        "last_active": datetime.utcnow(),
                        "updated_at": datetime.utcnow()
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to update user stats: {e}")
    
    def get_all_allowed_users(self) -> List[int]:
        """Get list of all allowed user IDs."""
        try:
            users = self.users_collection.find(
                {"is_allowed": True},
                {"telegram_id": 1}
            )
            return [user["telegram_id"] for user in users]
        except Exception as e:
            logger.error(f"Failed to get allowed users: {e}")
            return []
    
    def set_user_allowed_status(self, telegram_id: int, is_allowed: bool) -> bool:
        """Set user allowed status."""
        try:
            result = self.users_collection.update_one(
                {"telegram_id": telegram_id},
                {
                    "$set": {
                        "is_allowed": is_allowed,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Failed to update user status: {e}")
            return False
    
    # ==================== MESSAGE LOGGING ====================
    
    def log_message(self, telegram_user_id: int, message_type: str, 
                    content: str = None, file_id: str = None, 
                    caption: str = None, status: str = "sent",
                    whatsapp_message_id: str = None,
                    file_size: int = None) -> bool:
        """Log a forwarded message to MongoDB."""
        try:
            log_entry = {
                "telegram_user_id": telegram_user_id,
                "message_type": message_type,
                "content": content[:500] if content else None,
                "file_id": file_id,
                "caption": caption[:500] if caption else None,
                "file_size": file_size,
                "status": status,
                "whatsapp_message_id": whatsapp_message_id,
                "timestamp": datetime.utcnow()
            }
            
            self.message_logs_collection.insert_one(log_entry)
            return True
            
        except Exception as e:
            logger.error(f"Failed to log message: {e}")
            return False
    
    def get_message_stats(self, telegram_user_id: int = None, 
                          days: int = 7) -> Dict[str, Any]:
        """Get message statistics."""
        try:
            from datetime import timedelta
            
            start_date = datetime.utcnow() - timedelta(days=days)
            
            match_filter = {"timestamp": {"$gte": start_date}}
            if telegram_user_id:
                match_filter["telegram_user_id"] = telegram_user_id
            
            pipeline = [
                {"$match": match_filter},
                {"$group": {
                    "_id": "$message_type",
                    "count": {"$sum": 1},
                    "successful": {
                        "$sum": {"$cond": [{"$eq": ["$status", "success"]}, 1, 0]}
                    },
                    "failed": {
                        "$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}
                    }
                }},
                {"$sort": {"count": -1}}
            ]
            
            stats = list(self.message_logs_collection.aggregate(pipeline))
            
            total_messages = sum(s["count"] for s in stats)
            total_success = sum(s["successful"] for s in stats)
            total_failed = sum(s["failed"] for s in stats)
            
            return {
                "period_days": days,
                "total_messages": total_messages,
                "successful": total_success,
                "failed": total_failed,
                "success_rate": (total_success / total_messages * 100) if total_messages > 0 else 0,
                "by_type": stats
            }
            
        except Exception as e:
            logger.error(f"Failed to get message stats: {e}")
            return {}
    
    # ==================== BOT STATISTICS ====================
    
    def update_bot_stats(self, stat_type: str, value: Any = 1):
        """Update bot statistics."""
        try:
            self.bot_stats_collection.update_one(
                {"stat_type": stat_type, "date": datetime.utcnow().strftime("%Y-%m-%d")},
                {
                    "$inc": {"value": value},
                    "$set": {"updated_at": datetime.utcnow()}
                },
                upsert=True
            )
        except Exception as e:
            logger.error(f"Failed to update bot stats: {e}")
    
    def get_bot_stats(self, days: int = 7) -> Dict[str, Any]:
        """Get bot statistics for the last N days."""
        try:
            from datetime import timedelta
            
            start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            stats = {}
            cursor = self.bot_stats_collection.find({"date": {"$gte": start_date}})
            
            for stat in cursor:
                stat_type = stat["stat_type"]
                if stat_type not in stats:
                    stats[stat_type] = 0
                stats[stat_type] += stat.get("value", 0)
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get bot stats: {e}")
            return {}
    
    # ==================== SESSION MANAGEMENT ====================
    
    def save_telegram_session(self, user_id: int, session_data: Dict[str, Any]) -> bool:
        """Save Telegram user session data."""
        try:
            self.telegram_sessions_collection.update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "session_data": session_data,
                        "updated_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
            return False
    
    def get_telegram_session(self, user_id: int) -> Optional[Dict]:
        """Get Telegram user session data."""
        try:
            session = self.telegram_sessions_collection.find_one({"user_id": user_id})
            return session.get("session_data") if session else None
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return None
    
    # ==================== CLEANUP ====================
    
    def close(self):
        """Close MongoDB connection."""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")


# Singleton instance
db = MongoDB()
