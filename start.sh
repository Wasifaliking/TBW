#!/bin/bash

# Start WhatsApp Bridge with MongoDB
node whatsapp_client.js &

# Wait for bridge to initialize
sleep 5

# Start Telegram Bot
python main.py
