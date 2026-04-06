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

export async function startWhatsApp(bridge, log, waRef = null) {
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
                startWhatsApp(bridge, log, waRef).then(newSock => {
                    if (waRef) waRef.sock = newSock;
                });
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

    /** Quick text extraction for group filtering (before full media download) */
    function quickText(msg) {
        const m = msg.message;
        if (!m) return '';
        if (m.conversation) return m.conversation;
        if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
        if (m.imageMessage?.caption) return m.imageMessage.caption;
        if (m.documentMessage?.caption) return m.documentMessage.caption;
        if (m.documentWithCaptionMessage?.message?.documentMessage?.caption)
            return m.documentWithCaptionMessage.message.documentMessage.caption;
        return '';
    }

    /** Check if the bot should respond to a group message (resolves LIDs) */
    async function shouldRespondInGroup(msg) {
        const text = quickText(msg).trim();

        // Slash commands always trigger the bot
        if (text.startsWith('/')) return true;

        const ctx = msg.message?.extendedTextMessage?.contextInfo;

        // Check if bot is @mentioned (mentionedJid may contain LIDs)
        const mentionedJids = ctx?.mentionedJid || [];
        for (const jid of mentionedJids) {
            const resolved = await resolveJid(jid);
            if (sameUser(resolved, config.adminJid)) return true;
        }

        // Check if replying to a bot message (participant may be a LID)
        const quotedParticipant = ctx?.participant;
        if (quotedParticipant) {
            const resolved = await resolveJid(quotedParticipant);
            if (sameUser(resolved, config.adminJid)) return true;
        }

        return false;
    }

    // Inbound messages
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify') return;

        for (const msg of messages) {
            if (!msg.message) continue;     // skip protocol messages

            let from = msg.key.remoteJid;
            const isGroup = from.endsWith('@g.us');

            log.info({ from, fromMe: msg.key.fromMe, adminJid: config.adminJid, participant: msg.key.participant, isGroup }, 'RAW message');

            // Skip bot's own messages in groups
            if (isGroup && msg.key.fromMe) continue;

            // Group filtering: only respond to mentions, commands, or replies to bot
            if (isGroup) {
                if (!(await shouldRespondInGroup(msg))) continue;
            }

            // For self-chat in DMs: compare after resolving LID
            if (!isGroup && msg.key.fromMe) {
                const resolvedFrom = await resolveJid(from);
                const isSelfChat = sameUser(resolvedFrom, config.adminJid);
                log.info({ from, resolvedFrom, adminJid: config.adminJid, isSelfChat }, 'Self-chat check');
                if (!isSelfChat) continue; // skip non-self outgoing
            }

            // Resolve sender: in groups, participant is the actual sender
            let participant = null;
            if (isGroup) {
                participant = msg.key.participant ? await resolveJid(msg.key.participant) : null;
                if (!participant) continue;  // safety: skip if no participant
            } else {
                from = await resolveJid(from);
            }

            const content = await extractContent(msg, log);
            if (!content) continue;

            // Strip bot @mention from text in groups (may appear as LID or phone number)
            if (isGroup && content.type === 'text' && content.text) {
                const mentionedJids = msg.message?.extendedTextMessage?.contextInfo?.mentionedJid || [];
                for (const jid of mentionedJids) {
                    const resolved = await resolveJid(jid);
                    if (sameUser(resolved, config.adminJid)) {
                        // Remove @number from text (could be LID number or phone number)
                        const mentionNum = jid.split(':')[0].split('@')[0];
                        content.text = content.text.replace(`@${mentionNum}`, '').trim();
                        break;
                    }
                }
            }

            const chatJid = isGroup ? from : from;
            log.info({ chatJid, participant, isGroup, contentType: content.type }, 'Inbound message');

            // Show typing indicator while processing
            try {
                await sock.sendPresenceUpdate('composing', chatJid);
            } catch (e) { /* ignore presence errors */ }

            const payload = {
                type: 'message',
                id: msg.key.id || uuidv4(),
                from,
                pushName: msg.pushName || '',
                timestamp: msg.messageTimestamp,
                content,
            };

            // Add group metadata
            if (isGroup) {
                payload.isGroup = true;
                payload.participant = participant;
                // Fetch group name
                try {
                    const meta = await sock.groupMetadata(from);
                    if (meta?.subject) payload.groupName = meta.subject;
                } catch (e) { /* ignore — groupName will be absent */ }
                // Include message key so replies can quote the original
                payload.quotedMsgKey = {
                    remoteJid: from,
                    id: msg.key.id,
                    fromMe: false,
                    participant,
                };
            }

            bridge.sendToAgent(payload);
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

    // Document (PDF, text files, etc.)
    if (m.documentMessage || m.documentWithCaptionMessage) {
        const docMsg = m.documentWithCaptionMessage?.message?.documentMessage || m.documentMessage;
        if (!docMsg) return null;
        try {
            const buffer = await downloadMediaMessage(msg, 'buffer', {});
            return {
                type: 'document',
                caption: docMsg.caption || m.documentWithCaptionMessage?.message?.documentMessage?.caption || '',
                mimetype: docMsg.mimetype || 'application/octet-stream',
                filename: docMsg.fileName || 'document',
                data: buffer.toString('base64'),
            };
        } catch (err) {
            log.error({ err }, 'Failed to download document');
            return null;
        }
    }

    log.debug({ keys: Object.keys(m) }, 'Unsupported message type');
    return null;
}
