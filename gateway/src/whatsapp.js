import makeWASocket, {
    useMultiFileAuthState,
    DisconnectReason,
    makeCacheableSignalKeyStore,
    fetchLatestBaileysVersion,
    isLidUser,
    downloadMediaMessage,
} from '@whiskeysockets/baileys';
import { config } from './config.js';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import { v4 as uuidv4 } from 'uuid';
import path from 'path';
import fs from 'fs';

const waLog = pino({ level: 'silent' });

/** Compare JIDs ignoring device suffix (e.g. "123:2@s.whatsapp.net" vs "123@s.whatsapp.net") */
function sameUser(jid1, jid2) {
    if (!jid1 || !jid2) return false;
    return jid1.split(':')[0].split('@')[0] === jid2.split(':')[0].split('@')[0];
}

export async function startWhatsApp(bridge, log) {
    const authDir = path.resolve(config.authDir);
    fs.mkdirSync(authDir, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(authDir);
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
        version,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, waLog),
        },
        logger: waLog,
        printQRInTerminal: false,
    });

    // QR code display
    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            log.info('Scan QR code to login:');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'open') {
            const rawJid = sock.user?.id;
            // Normalize: strip device suffix for consistent comparisons
            const [user, server] = (rawJid || '').split('@');
            const myJid = user.split(':')[0] + '@' + server;
            log.info({ rawJid, normalizedJid: myJid }, 'WhatsApp connected');
            config.adminJid = myJid;
            bridge.sendAdminJid(myJid);
        }

        if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode;
            if (code !== DisconnectReason.loggedOut) {
                log.warn({ code }, 'Connection closed, reconnecting...');
                startWhatsApp(bridge, log);
            } else {
                log.error('Logged out. Delete auth dir and restart.');
            }
        }
    });

    sock.ev.on('creds.update', saveCreds);

    // Resolve LID to phone number JID
    async function resolveJid(jid) {
        if (!isLidUser(jid)) return jid;
        try {
            const pn = await sock.signalRepository.lidMapping.getPNForLID(jid);
            if (pn) {
                log.info({ lid: jid, pn }, 'Resolved LID to phone number');
                return pn;
            }
        } catch (err) {
            log.warn({ err, jid }, 'Failed to resolve LID');
        }
        return jid; // fallback to LID if resolution fails
    }

    // Inbound messages
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return;

        for (const msg of messages) {
            if (!msg.message) continue;     // skip protocol messages

            let from = msg.key.remoteJid;

            log.info({ from, fromMe: msg.key.fromMe, adminJid: config.adminJid, participant: msg.key.participant }, 'RAW message');

            // For self-chat: compare after resolving LID
            let isSelfChat = false;
            if (msg.key.fromMe) {
                const resolvedFrom = await resolveJid(from);
                isSelfChat = sameUser(resolvedFrom, config.adminJid);
                log.info({ from, resolvedFrom, adminJid: config.adminJid, isSelfChat }, 'Self-chat check');
                if (!isSelfChat) continue; // skip non-self outgoing
            }

            // Resolve LID to phone number
            from = await resolveJid(from);

            const content = await extractContent(msg, log);
            if (!content) continue;

            log.info({ from, contentType: content.type }, 'Inbound message');

            bridge.sendToAgent({
                type: 'message',
                id: msg.key.id || uuidv4(),
                from,
                pushName: msg.pushName || '',
                timestamp: msg.messageTimestamp,
                content,
            });
        }
    });

    return sock;
}

async function extractContent(msg, log) {
    const m = msg.message;
    log.info({ messageKeys: Object.keys(m) }, 'extractContent');

    // Text
    if (m.conversation) {
        return { type: 'text', text: m.conversation };
    }
    if (m.extendedTextMessage?.text) {
        return { type: 'text', text: m.extendedTextMessage.text };
    }

    // Image — download and send as base64
    if (m.imageMessage) {
        try {
            const buffer = await downloadMediaMessage(msg, 'buffer', {});
            return {
                type: 'image',
                caption: m.imageMessage.caption || '',
                mimetype: m.imageMessage.mimetype || 'image/jpeg',
                data: buffer.toString('base64'),
            };
        } catch (err) {
            log.error({ err }, 'Failed to download image');
            return null;
        }
    }

    // Audio / voice note — download and send as base64
    if (m.audioMessage) {
        try {
            const buffer = await downloadMediaMessage(msg, 'buffer', {});
            return {
                type: 'audio',
                mimetype: m.audioMessage.mimetype || 'audio/ogg',
                data: buffer.toString('base64'),
                seconds: m.audioMessage.seconds,
                ptt: m.audioMessage.ptt || false,
            };
        } catch (err) {
            log.error({ err }, 'Failed to download audio');
            return null;
        }
    }

    log.debug({ keys: Object.keys(m) }, 'Unsupported message type');
    return null;
}
