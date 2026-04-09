#!/usr/bin/env node
/**
 * WhatsApp Bridge Service with MongoDB Session Storage
 * Uses Baileys library with MongoDB for persistent session management
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const { MongoClient, ObjectId } = require('mongodb');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion, makeInMemoryStore, makeCacheableSignalKeyStore, delay } = require('@whiskeysockets/baileys');
const pino = require('pino');
const qrcode = require('qrcode-terminal');

// ==================== CONFIGURATION ====================
const PORT = process.env.WHATSAPP_BRIDGE_PORT || 3000;
const MONGODB_URI = process.env.MONGODB_URI || 'mongodb://localhost:27017';
const MONGODB_DB_NAME = process.env.MONGODB_DB_NAME || 'whatsapp_bot';
const API_KEY = process.env.WHATSAPP_API_KEY || 'change_this_secret_key';
const TARGET_JID = process.env.TARGET_WHATSAPP_JID || '';

// ==================== GLOBAL STATE ====================
let sock = null;
let connectionStatus = 'disconnected';
let qrCodeData = null;
let mongoClient = null;
let db = null;
let sessionsCollection = null;
let messagesCollection = null;
let botConfigCollection = null;

const app = express();
app.use(express.json({ limit: '100mb' }));

// ==================== LOGGER ====================
const logger = pino({
    transport: {
        target: 'pino-pretty',
        options: {
            colorize: true,
            ignore: 'pid,hostname',
            translateTime: 'SYS:standard'
        }
    }
});

// ==================== MONGODB CONNECTION ====================
async function connectToMongoDB() {
    try {
        mongoClient = new MongoClient(MONGODB_URI, {
            maxPoolSize: 10,
            minPoolSize: 2,
            connectTimeoutMS: 10000,
            socketTimeoutMS: 45000
        });
        
        await mongoClient.connect();
        db = mongoClient.db(MONGODB_DB_NAME);
        
        // Initialize collections
        sessionsCollection = db.collection('whatsapp_sessions');
        messagesCollection = db.collection('message_logs');
        botConfigCollection = db.collection('bot_config');
        
        // Create indexes
        await sessionsCollection.createIndex({ "sessionId": 1 }, { unique: true });
        await sessionsCollection.createIndex({ "updatedAt": -1 });
        await messagesCollection.createIndex({ "timestamp": -1 });
        await messagesCollection.createIndex({ "messageId": 1 });
        await messagesCollection.createIndex({ "sender": 1 });
        await messagesCollection.createIndex({ "recipient": 1 });
        
        logger.info('✅ Connected to MongoDB successfully');
        logger.info(`📊 Database: ${MONGODB_DB_NAME}`);
        
        // Initialize bot config if not exists
        await initializeBotConfig();
        
        return true;
    } catch (error) {
        logger.error(`❌ MongoDB connection failed: ${error.message}`);
        return false;
    }
}

async function initializeBotConfig() {
    const config = await botConfigCollection.findOne({ configKey: 'main' });
    if (!config) {
        await botConfigCollection.insertOne({
            configKey: 'main',
            targetJid: TARGET_JID,
            allowedUsers: [],
            createdAt: new Date(),
            updatedAt: new Date()
        });
        logger.info('📝 Bot configuration initialized in MongoDB');
    }
}

// ==================== MONGODB SESSION STORE ====================
class MongoDBAuthState {
    constructor(sessionId = 'default') {
        this.sessionId = sessionId;
    }

    async saveCreds(creds) {
        try {
            await sessionsCollection.updateOne(
                { sessionId: this.sessionId },
                { 
                    $set: { 
                        creds: creds,
                        updatedAt: new Date()
                    }
                },
                { upsert: true }
            );
            logger.debug('Credentials saved to MongoDB');
        } catch (error) {
            logger.error(`Failed to save credentials: ${error.message}`);
        }
    }

    async loadCreds() {
        try {
            const session = await sessionsCollection.findOne({ sessionId: this.sessionId });
            return session?.creds || null;
        } catch (error) {
            logger.error(`Failed to load credentials: ${error.message}`);
            return null;
        }
    }

    async saveKeys(keys) {
        try {
            await sessionsCollection.updateOne(
                { sessionId: this.sessionId },
                { 
                    $set: { 
                        keys: keys,
                        updatedAt: new Date()
                    }
                },
                { upsert: true }
            );
            logger.debug('Keys saved to MongoDB');
        } catch (error) {
            logger.error(`Failed to save keys: ${error.message}`);
        }
    }

    async loadKeys() {
        try {
            const session = await sessionsCollection.findOne({ sessionId: this.sessionId });
            return session?.keys || null;
        } catch (error) {
            logger.error(`Failed to load keys: ${error.message}`);
            return null;
        }
    }
}

// ==================== AUTHENTICATION MIDDLEWARE ====================
function authenticate(req, res, next) {
    const authHeader = req.headers['authorization'];
    const token = authHeader && authHeader.split(' ')[1];
    
    if (!token || token !== API_KEY) {
        return res.status(401).json({ 
            success: false, 
            error: 'Unauthorized: Invalid API Key' 
        });
    }
    next();
}

// ==================== MESSAGE LOGGING ====================
async function logMessage(messageData) {
    try {
        await messagesCollection.insertOne({
            ...messageData,
            timestamp: new Date()
        });
    } catch (error) {
        logger.error(`Failed to log message: ${error.message}`);
    }
}

// ==================== WHATSAPP CONNECTION ====================
async function connectToWhatsApp() {
    try {
        const authState = new MongoDBAuthState('default');
        
        // Load existing credentials
        const creds = await authState.loadCreds();
        const keys = await authState.loadKeys();
        
        const { version, isLatest } = await fetchLatestBaileysVersion();
        logger.info(`Using Baileys version: ${version.join('.')}, Latest: ${isLatest}`);

        // Create state object
        const state = {
            creds: creds || undefined,
            keys: {
                get: async (type, ids) => {
                    if (!keys) return {};
                    const data = {};
                    await Promise.all(
                        ids.map(async (id) => {
                            let value = keys[`${type}-${id}`];
                            if (type === 'app-state-sync-key' && value) {
                                value = require('@whiskeysockets/baileys').proto.Message.AppStateSyncKeyData.fromObject(value);
                            }
                            data[id] = value;
                        })
                    );
                    return data;
                },
                set: async (data) => {
                    if (!keys) keys = {};
                    for (const key in data) {
                        let value = data[key];
                        if (value && typeof value === 'object' && value.constructor.name === 'AppStateSyncKeyData') {
                            value = value.toJSON();
                        }
                        keys[key] = value;
                    }
                    await authState.saveKeys(keys);
                }
            }
        };

        sock = makeWASocket({
            version,
            auth: state,
            logger: pino({ level: 'silent' }),
            printQRInTerminal: false,
            browser: ['Ubuntu', 'Chrome', '20.0.04'],
            markOnlineOnConnect: true,
            syncFullHistory: false,
            getMessage: async (key) => {
                // Fetch message from MongoDB if needed
                const msg = await messagesCollection.findOne({ 
                    'key.id': key.id,
                    'key.remoteJid': key.remoteJid
                });
                return msg?.message || undefined;
            }
        });

        // Save credentials when updated
        sock.ev.on('creds.update', async (newCreds) => {
            await authState.saveCreds(newCreds);
        });

        // Handle connection updates
        sock.ev.on('connection.update', async (update) => {
            const { connection, lastDisconnect, qr } = update;
            
            if (qr) {
                qrCodeData = qr;
                connectionStatus = 'qr_ready';
                logger.info('📱 QR Code received! Scan with WhatsApp to login.');
                
                // Display QR in terminal
                qrcode.generate(qr, { small: true });
                console.log('\n📲 Scan this QR Code with WhatsApp (Linked Devices)');
                console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n');
                
                // Save QR generation time
                await botConfigCollection.updateOne(
                    { configKey: 'main' },
                    { 
                        $set: { 
                            'qrGeneratedAt': new Date(),
                            'connectionStatus': 'qr_ready'
                        }
                    },
                    { upsert: true }
                );
            }

            if (connection === 'open') {
                connectionStatus = 'connected';
                qrCodeData = null;
                logger.info('✅ WhatsApp Connected Successfully!');
                logger.info(`👤 Logged in as: ${sock.user?.name || 'Unknown'}`);
                logger.info(`📞 Phone: ${sock.user?.id?.split(':')[0] || 'N/A'}`);
                
                // Save connection info to MongoDB
                await botConfigCollection.updateOne(
                    { configKey: 'main' },
                    { 
                        $set: { 
                            'connectedAt': new Date(),
                            'connectionStatus': 'connected',
                            'userName': sock.user?.name,
                            'userJid': sock.user?.id
                        }
                    },
                    { upsert: true }
                );
                
                if (TARGET_JID) {
                    logger.info(`🎯 Target JID configured: ${TARGET_JID}`);
                }
            }

            if (connection === 'close') {
                connectionStatus = 'disconnected';
                const statusCode = lastDisconnect?.error?.output?.statusCode;
                const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
                
                logger.warn(`WhatsApp connection closed. Reconnecting: ${shouldReconnect}`);
                
                await botConfigCollection.updateOne(
                    { configKey: 'main' },
                    { 
                        $set: { 
                            'disconnectedAt': new Date(),
                            'connectionStatus': 'disconnected',
                            'disconnectReason': statusCode
                        }
                    }
                );
                
                if (shouldReconnect) {
                    setTimeout(() => connectToWhatsApp(), 5000);
                } else {
                    logger.error('❌ Logged out! Session invalidated.');
                    connectionStatus = 'logged_out';
                    
                    // Clear session from MongoDB
                    await sessionsCollection.deleteOne({ sessionId: 'default' });
                }
            }
        });

        // Handle incoming messages
        sock.ev.on('messages.upsert', async (m) => {
            const msg = m.messages[0];
            if (!msg.key.fromMe && m.type === 'notify') {
                logger.info(`📨 Message from ${msg.key.remoteJid}: ${msg.message?.conversation || '[Media]'}`);
                
                // Log incoming message to MongoDB
                await logMessage({
                    messageId: msg.key.id,
                    direction: 'incoming',
                    sender: msg.key.remoteJid,
                    recipient: sock.user?.id,
                    messageType: Object.keys(msg.message || {})[0] || 'unknown',
                    content: msg.message?.conversation || null,
                    message: msg.message,
                    timestamp: new Date(parseInt(msg.messageTimestamp) * 1000 || Date.now())
                });
            }
        });

    } catch (error) {
        logger.error(`Connection error: ${error.message}`);
        connectionStatus = 'error';
        setTimeout(() => connectToWhatsApp(), 10000);
    }
}

// ==================== API ENDPOINTS ====================

// Health Check
app.get('/health', (req, res) => {
    res.json({
        success: true,
        status: connectionStatus,
        connected: connectionStatus === 'connected',
        user: sock?.user?.name || null,
        targetJid: TARGET_JID || 'Not configured',
        qrAvailable: !!qrCodeData,
        mongodb: !!db,
        timestamp: new Date().toISOString()
    });
});

// Get QR Code
app.get('/qr', authenticate, async (req, res) => {
    if (connectionStatus === 'qr_ready' && qrCodeData) {
        res.json({
            success: true,
            qr: qrCodeData,
            message: 'Scan this QR code with WhatsApp (Linked Devices)'
        });
    } else if (connectionStatus === 'connected') {
        res.json({
            success: true,
            connected: true,
            message: 'Already connected to WhatsApp'
        });
    } else {
        res.status(503).json({
            success: false,
            status: connectionStatus,
            message: 'QR code not available'
        });
    }
});

// Send Text Message
app.post('/send/text', authenticate, async (req, res) => {
    if (!sock || connectionStatus !== 'connected') {
        return res.status(503).json({
            success: false,
            error: 'WhatsApp not connected',
            status: connectionStatus
        });
    }

    const { text, targetJid, telegramUserId } = req.body;
    const recipient = targetJid || TARGET_JID;

    if (!recipient) {
        return res.status(400).json({
            success: false,
            error: 'No target JID provided'
        });
    }

    if (!text) {
        return res.status(400).json({
            success: false,
            error: 'Text message is required'
        });
    }

    try {
        const result = await sock.sendMessage(recipient, {
            text: text.substring(0, 4096)
        });

        logger.info(`✅ Text sent to ${recipient}`);
        
        // Log outgoing message
        await logMessage({
            messageId: result.key.id,
            direction: 'outgoing',
            sender: sock.user?.id,
            recipient: recipient,
            messageType: 'text',
            content: text,
            telegramUserId: telegramUserId,
            timestamp: new Date()
        });
        
        res.json({
            success: true,
            messageId: result.key.id,
            recipient: recipient
        });
    } catch (error) {
        logger.error(`Failed to send text: ${error.message}`);
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

// Send Media
app.post('/send/media', authenticate, async (req, res) => {
    if (!sock || connectionStatus !== 'connected') {
        return res.status(503).json({
            success: false,
            error: 'WhatsApp not connected',
            status: connectionStatus
        });
    }

    const { mediaUrl, mediaType, caption, fileName, targetJid, telegramUserId } = req.body;
    const recipient = targetJid || TARGET_JID;

    if (!recipient) {
        return res.status(400).json({
            success: false,
            error: 'No target JID provided'
        });
    }

    if (!mediaUrl || !mediaType) {
        return res.status(400).json({
            success: false,
            error: 'mediaUrl and mediaType are required'
        });
    }

    try {
        const axios = require('axios');
        const response = await axios.get(mediaUrl, {
            responseType: 'arraybuffer',
            timeout: 120000
        });

        const buffer = Buffer.from(response.data);
        let messageContent = {};
        let mimeType = '';
        
        switch (mediaType.toLowerCase()) {
            case 'image':
                messageContent = {
                    image: buffer,
                    caption: caption || ''
                };
                mimeType = 'image/jpeg';
                break;
                
            case 'video':
                messageContent = {
                    video: buffer,
                    caption: caption || '',
                    mimetype: 'video/mp4'
                };
                mimeType = 'video/mp4';
                break;
                
            case 'audio':
                messageContent = {
                    audio: buffer,
                    mimetype: 'audio/mpeg',
                    ptt: false
                };
                mimeType = 'audio/mpeg';
                break;
                
            case 'document':
                messageContent = {
                    document: buffer,
                    fileName: fileName || 'document',
                    mimetype: 'application/octet-stream',
                    caption: caption || ''
                };
                mimeType = 'application/octet-stream';
                break;
                
            default:
                return res.status(400).json({
                    success: false,
                    error: `Unsupported media type: ${mediaType}`
                });
        }

        const result = await sock.sendMessage(recipient, messageContent);
        
        logger.info(`✅ ${mediaType} sent to ${recipient}`);
        
        // Log outgoing media
        await logMessage({
            messageId: result.key.id,
            direction: 'outgoing',
            sender: sock.user?.id,
            recipient: recipient,
            messageType: mediaType,
            caption: caption,
            fileName: fileName,
            mimeType: mimeType,
            telegramUserId: telegramUserId,
            timestamp: new Date()
        });
        
        res.json({
            success: true,
            messageId: result.key.id,
            recipient: recipient,
            mediaType: mediaType
        });
        
    } catch (error) {
        logger.error(`Failed to send ${mediaType}: ${error.message}`);
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

// Get Connection Status
app.get('/status', authenticate, async (req, res) => {
    let config = null;
    let sessionInfo = null;
    
    if (db) {
        config = await botConfigCollection.findOne({ configKey: 'main' });
        sessionInfo = await sessionsCollection.findOne({ sessionId: 'default' });
    }
    
    res.json({
        success: true,
        connected: connectionStatus === 'connected',
        status: connectionStatus,
        user: sock?.user || null,
        targetJid: TARGET_JID,
        config: config,
        sessionExists: !!sessionInfo,
        sessionUpdated: sessionInfo?.updatedAt,
        timestamp: new Date().toISOString()
    });
});

// Get Message History
app.get('/messages', authenticate, async (req, res) => {
    try {
        const limit = parseInt(req.query.limit) || 50;
        const skip = parseInt(req.query.skip) || 0;
        const direction = req.query.direction; // 'incoming' or 'outgoing'
        const messageType = req.query.messageType;
        
        let query = {};
        if (direction) query.direction = direction;
        if (messageType) query.messageType = messageType;
        
        const messages = await messagesCollection
            .find(query)
            .sort({ timestamp: -1 })
            .skip(skip)
            .limit(limit)
            .toArray();
        
        const total = await messagesCollection.countDocuments(query);
        
        res.json({
            success: true,
            messages: messages,
            pagination: {
                limit,
                skip,
                total,
                hasMore: skip + limit < total
            }
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

// Update Target JID
app.post('/config/target', authenticate, async (req, res) => {
    const { targetJid } = req.body;
    
    if (!targetJid) {
        return res.status(400).json({
            success: false,
            error: 'targetJid is required'
        });
    }
    
    try {
        await botConfigCollection.updateOne(
            { configKey: 'main' },
            { 
                $set: { 
                    targetJid: targetJid,
                    updatedAt: new Date()
                }
            },
            { upsert: true }
        );
        
        // Update environment variable in memory
        process.env.TARGET_WHATSAPP_JID = targetJid;
        
        res.json({
            success: true,
            targetJid: targetJid,
            message: 'Target JID updated successfully'
        });
    } catch (error) {
        res.status(500).json({
            success: false,
            error: error.message
        });
    }
});

// Logout and Clear Session
app.post('/logout', authenticate, async (req, res) => {
    if (sock) {
        try {
            await sock.logout();
            connectionStatus = 'logged_out';
            
            // Clear session from MongoDB
            await sessionsCollection.deleteOne({ sessionId: 'default' });
            
            // Update config
            await botConfigCollection.updateOne(
                { configKey: 'main' },
                { 
                    $set: { 
                        'loggedOutAt': new Date(),
                        'connectionStatus': 'logged_out'
                    }
                }
            );
            
            res.json({
                success: true,
                message: 'Logged out and session cleared'
            });
        } catch (error) {
            res.status(500).json({
                success: false,
                error: error.message
            });
        }
    } else {
        res.status(400).json({
            success: false,
            error: 'No active connection'
        });
    }
});

// ==================== START SERVER ====================
app.listen(PORT, async () => {
    logger.info(`🚀 WhatsApp Bridge API running on port ${PORT}`);
    
    // Connect to MongoDB first
    const mongoConnected = await connectToMongoDB();
    
    if (mongoConnected) {
        logger.info(`🔑 API Key configured: ${API_KEY !== 'change_this_secret_key' ? 'YES' : 'NO (DEFAULT)'}`);
        logger.info(`🎯 Target JID: ${TARGET_JID || 'NOT SET'}`);
        logger.info(`💾 Session storage: MongoDB`);
        
        // Connect to WhatsApp
        connectToWhatsApp();
    } else {
        logger.error('❌ Cannot start without MongoDB connection');
        process.exit(1);
    }
});

// Graceful shutdown
process.on('SIGINT', async () => {
    logger.info('Shutting down...');
    if (sock) {
        await sock.end();
    }
    if (mongoClient) {
        await mongoClient.close();
        logger.info('MongoDB connection closed');
    }
    process.exit(0);
});

process.on('SIGTERM', async () => {
    logger.info('Received SIGTERM, shutting down gracefully...');
    if (sock) {
        await sock.end();
    }
    if (mongoClient) {
        await mongoClient.close();
    }
    process.exit(0);
});
