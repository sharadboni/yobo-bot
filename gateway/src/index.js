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
    const wa = await startWhatsApp(bridge, log);

    // 3. Handle outbound messages from agent → WhatsApp
    bridge.onOutboundMessage = async (msg) => {
        // Clear chats command
        if (msg.type === 'clear_chats') {
            await handleClearChats(wa, msg.target, log);
            return;
        }

        let to, text;

        if (msg.type === 'admin_notify') {
            // Send to admin's self-chat (must use JID without device suffix)
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
                const audioBuffer = Buffer.from(audioB64, 'base64');
                await wa.sendMessage(to, {
                    audio: audioBuffer,
                    mimetype: msg.content.audio_mimetype || 'audio/ogg; codecs=opus',
                    ptt: true,  // send as voice note
                });
                log.info({ to }, 'Sent voice reply');
            }

            // Always send text as well
            await wa.sendMessage(to, { text });
            log.info({ to }, 'Sent text reply');
        } catch (err) {
            log.error({ err, to }, 'Failed to send WhatsApp message');
        }
    };

    // Clear chats handler
    async function handleClearChats(sock, target, log) {
        try {
            const chats = await sock.groupFetchAllParticipating?.() || {};
            // Get all chat JIDs from the store or use chatModify
            if (target === 'all') {
                log.info('Clearing all chats...');
                // Fetch chats by listing conversations
                const [result] = await sock.fetchMessageHistory(50, null, null) || [[]];
                // Clear using chatModify — delete for me
                const store = sock.store;
                // Use the direct approach: iterate known chats
                let cleared = 0;
                try {
                    await sock.chatModify({ clear: { messages: [] } }, config.adminJid);
                    cleared++;
                } catch (e) { /* self chat might not clear */ }

                // Clear chats we've interacted with from data/users
                const fs = await import('fs');
                const path = await import('path');
                const usersDir = path.default.resolve('../data/users');
                if (fs.default.existsSync(usersDir)) {
                    const files = fs.default.readdirSync(usersDir);
                    for (const file of files) {
                        if (!file.endsWith('.json')) continue;
                        const number = file.replace('.json', '');
                        const jid = `${number}@s.whatsapp.net`;
                        try {
                            await sock.chatModify(
                                { clear: { messages: [] } },
                                jid
                            );
                            cleared++;
                            log.info({ jid }, 'Chat cleared');
                        } catch (err) {
                            log.warn({ jid, err: err.message }, 'Failed to clear chat');
                        }
                    }
                }
                log.info({ cleared }, 'Clear chats complete');
            } else {
                // Clear specific chat by number
                const jid = target.includes('@') ? target : `${target}@s.whatsapp.net`;
                await sock.chatModify({ clear: { messages: [] } }, jid);
                log.info({ jid }, 'Chat cleared');
            }
        } catch (err) {
            log.error({ err }, 'Failed to clear chats');
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
