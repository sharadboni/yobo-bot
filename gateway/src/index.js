import { config } from './config.js';
import { WSBridge } from './ws-bridge.js';
import { startWhatsApp } from './whatsapp.js';
import pino from 'pino';

/** Strip device suffix: "12345:2@s.whatsapp.net" → "12345@s.whatsapp.net" */
function normalizeJid(jid) {
    if (!jid) return jid;
    const [user, server] = jid.split('@');
    return user.split(':')[0] + '@' + server;
}

const log = pino({ name: 'gateway', level: config.logLevel });

async function main() {
    // 1. Start WebSocket bridge
    const bridge = new WSBridge(config.wsPort);
    bridge.start();
    log.info('Gateway started');

    // 2. Start WhatsApp connection
    // wa is mutable — startWhatsApp updates it on reconnect
    const waRef = { sock: null };
    waRef.sock = await startWhatsApp(bridge, log, waRef);
    const wa = new Proxy(waRef, {
        get: (target, prop) => target.sock[prop],
    });

    // Retry queue for failed sends
    const MAX_RETRIES = 3;
    const RETRY_DELAY = 5000; // 5 seconds

    async function sendWithRetry(sendFn, retries = MAX_RETRIES) {
        for (let attempt = 1; attempt <= retries; attempt++) {
            try {
                await sendFn();
                return true;
            } catch (err) {
                const isConnectionError = err.message?.includes('Connection Closed')
                    || err.output?.statusCode === 428;
                if (isConnectionError && attempt < retries) {
                    log.warn({ attempt, retries }, 'Send failed (connection), retrying in %dms...', RETRY_DELAY);
                    await new Promise(r => setTimeout(r, RETRY_DELAY));
                } else {
                    throw err;
                }
            }
        }
    }

    // 3. Handle outbound messages from agent → WhatsApp
    bridge.onOutboundMessage = async (msg) => {
        // Clear chats command
        if (msg.type === 'clear_chats') {
            await handleClearChats(wa, msg.target, log);
            return;
        }

        // Typing indicator from agent (for long operations)
        if (msg.type === 'typing') {
            try {
                const to = msg.to || normalizeJid(config.adminJid);
                await wa.sendPresenceUpdate('composing', to);
            } catch (e) { /* ignore */ }
            return;
        }

        // Stop typing indicator
        if (msg.type === 'typing_stop') {
            try {
                const to = msg.to || normalizeJid(config.adminJid);
                await wa.sendPresenceUpdate('paused', to);
            } catch (e) { /* ignore */ }
            return;
        }

        let to, text;

        if (msg.type === 'admin_notify') {
            to = normalizeJid(config.adminJid);
            text = msg.content?.text;
            log.info({ to, adminJid: config.adminJid }, 'Admin notify');
        } else if (msg.type === 'reply') {
            to = msg.to;
            text = msg.content?.text;
        }

        if (!to || !text) {
            log.warn({ msg }, 'Invalid outbound payload');
            return;
        }

        try {
            // Send audio voice note if present
            const audioB64 = msg.content?.audio;
            if (audioB64) {
                await sendWithRetry(() => wa.sendMessage(to, {
                    audio: Buffer.from(audioB64, 'base64'),
                    mimetype: msg.content.audio_mimetype || 'audio/ogg; codecs=opus',
                    ptt: true,
                }));
                log.info({ to }, 'Sent voice reply');
            }

            // Always send text as well
            await sendWithRetry(() => wa.sendMessage(to, { text }));
            log.info({ to }, 'Sent text reply');
        } catch (err) {
            log.error({ err, to }, 'Failed to send WhatsApp message after retries');
        } finally {
            try {
                await wa.sendPresenceUpdate('paused', to);
            } catch (e) { /* ignore */ }
        }
    };

    // Delete chat handler — deletes entire chat (like long-press → Delete Chat in the app)
    async function handleClearChats(sock, target, log) {
        const fs = await import('fs');
        const path = await import('path');

        async function deleteChat(jid) {
            try {
                // Use a future timestamp to ensure all messages are covered
                const now = Math.floor(Date.now() / 1000);
                await sock.chatModify({
                    delete: true,
                    lastMessages: [{
                        key: { remoteJid: jid, id: 'clear', fromMe: false },
                        messageTimestamp: now,
                    }],
                }, jid);
                log.info({ jid }, 'Chat deleted');
                return true;
            } catch (err) {
                log.warn({ jid, err: err.message }, 'Failed to delete chat');
                return false;
            }
        }

        try {
            if (target === 'all') {
                log.info('Deleting all chats...');
                let deleted = 0;

                // Delete all known user chats from data/users
                const usersDir = path.default.resolve('data/users');
                if (fs.default.existsSync(usersDir)) {
                    for (const file of fs.default.readdirSync(usersDir)) {
                        if (!file.endsWith('.json')) continue;
                        const number = file.replace('.json', '');
                        if (await deleteChat(`${number}@s.whatsapp.net`)) deleted++;
                    }
                }

                // Delete admin self-chat last
                if (await deleteChat(normalizeJid(config.adminJid))) deleted++;

                log.info({ deleted }, 'Delete chats complete');
            } else {
                const jid = target.includes('@') ? target : `${target}@s.whatsapp.net`;
                await deleteChat(jid);
            }
        } catch (err) {
            log.error({ err }, 'Failed to delete chats');
        }
    }

    // Graceful shutdown
    const shutdown = () => {
        log.info('Shutting down...');
        bridge.stop();
        process.exit(0);
    };
    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);
}

main().catch((err) => {
    log.error({ err }, 'Fatal error');
    process.exit(1);
});
