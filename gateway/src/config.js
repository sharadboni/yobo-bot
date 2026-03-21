import 'dotenv/config';

export const config = {
    wsPort: parseInt(process.env.WS_PORT || '8765', 10),
    logLevel: process.env.LOG_LEVEL || 'info',
    authDir: process.env.AUTH_DIR || '../data/auth',
    adminJid: process.env.ADMIN_JID || '',  // set after WhatsApp login
};
