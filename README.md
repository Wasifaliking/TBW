# 📱 Telegram to WhatsApp Forwarder with MongoDB

[![Deploy to Heroku](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy)

A professional Telegram bot that forwards messages to WhatsApp using **Baileys** library with **MongoDB** for persistent session storage and data management.

## ✨ Features

- 🔄 **All Media Types**: Text, Photos, Videos, Audio, Documents
- 💾 **MongoDB Session Storage**: WhatsApp sessions persist across restarts
- 📊 **Message Logging**: All forwarded messages logged to MongoDB
- 👥 **User Management**: Track users, message counts, and permissions
- 📱 **Target JID Support**: Send to any WhatsApp JID
- 🔒 **Admin Panel**: Manage users and view statistics
- 🚀 **Heroku Ready**: One-click deployment

## 📦 MongoDB Collections

| Collection | Purpose |
|------------|---------|
| `whatsapp_sessions` | Stores Baileys authentication credentials |
| `telegram_users` | Telegram user profiles and permissions |
| `message_logs` | Complete history of forwarded messages |
| `bot_statistics` | Daily usage statistics |
| `telegram_sessions` | User session data |
| `bot_config` | Bot configuration settings |

## 🚀 Deployment

### MongoDB Atlas Setup (Free)

1. Go to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Create a free cluster
3. Create a database user and get connection string
4. Add your IP to network access (or use `0.0.0.0/0` for Heroku)

### Heroku Deployment

1. Click "Deploy to Heroku" button
2. Fill in environment variables
3. Deploy and scale worker: `heroku ps:scale worker=1`

## 📝 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |
| `MONGODB_URI` | ✅ | MongoDB connection string |
| `TARGET_WHATSAPP_JID` | ✅ | Target JID (e.g., 92300xxxxx@s.whatsapp.net) |
| `WHATSAPP_API_KEY` | ✅ | API key for bridge security |
| `ADMIN_TELEGRAM_IDS` | ❌ | Comma-separated admin IDs |

## 📱 Getting JID

JID format: `[country_code][number]@s.whatsapp.net`

Example:
- Pakistan number `03001234567` → `923001234567@s.whatsapp.net`

## 🔧 Commands

| Command | Description |
|---------|-------------|
| `/start` | Start bot |
| `/status` | Check connection and stats |
| `/qr` | Get WhatsApp login QR (admin) |
| `/admin` | Admin panel |
| `/help` | Help information |

## 📊 Database Structure

### whatsapp_sessions
```json
{
  "sessionId": "default",
  "creds": { ... },
  "keys": { ... },
  "updatedAt": "2024-01-01T00:00:00Z"
}
