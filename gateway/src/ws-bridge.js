import { WebSocketServer } from 'ws';
import { v4 as uuidv4 } from 'uuid';
import pino from 'pino';

const log = pino({ name: 'ws-bridge' });

export class WSBridge {
    constructor(port) {
        this.port = port;
        this.wss = null;
        this.agent = null;          // single agent connection
        this.onOutboundMessage = null;  // callback set by index.js
        this._pendingReplies = new Map();
    }

    start() {
        this.wss = new WebSocketServer({ port: this.port });
        log.info({ port: this.port }, 'WebSocket server listening');

        this.wss.on('connection', (ws) => {
            if (this.agent) {
                log.warn('Agent already connected, replacing');
                this.agent.close();
            }
            this.agent = ws;
            log.info('Agent connected');

            // Re-send admin JID if WhatsApp is already connected
            if (this.adminJid) {
                this.sendAdminJid(this.adminJid);
            }

            ws.on('message', (raw) => {
                try {
                    const msg = JSON.parse(raw);
                    this._handleAgentMessage(msg);
                } catch (err) {
                    log.error({ err }, 'Bad message from agent');
                }
            });

            ws.on('close', () => {
                log.warn('Agent disconnected');
                this.agent = null;
            });

            ws.on('error', (err) => {
                log.error({ err }, 'Agent socket error');
            });
        });
    }

    _handleAgentMessage(msg) {
        if (msg.type === 'reply' || msg.type === 'admin_notify' || msg.type === 'clear_chats' || msg.type === 'typing') {
            if (this.onOutboundMessage) {
                this.onOutboundMessage(msg);
            }
        } else {
            log.warn({ type: msg.type }, 'Unknown message type from agent');
        }
    }

    /** Forward an inbound WhatsApp message to the agent */
    sendToAgent(payload) {
        if (!this.agent || this.agent.readyState !== 1) {
            log.warn('Agent not connected, dropping message');
            return;
        }
        this.agent.send(JSON.stringify(payload));
    }

    /** Send admin JID to agent so it knows who the admin is */
    sendAdminJid(jid) {
        this.adminJid = jid;  // cache for agent reconnects
        this.sendToAgent({ type: 'admin_jid', jid });
    }

    stop() {
        if (this.wss) {
            this.wss.close();
        }
    }
}
