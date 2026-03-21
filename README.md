# Yobo Bot

Two-tier WhatsApp AI chatbot. A Node.js gateway handles WhatsApp connectivity via Baileys, a Python agent handles all AI logic via LangGraph. They communicate over WebSocket.

```
WhatsApp <-> Gateway (Node.js/Baileys) <-> WebSocket :8765 <-> Agent (Python/LangGraph)
```

## Features

- **Admin approval** — new users must be approved via self-chat (`/add`, `/ignore`)
- **Multi-modal input** — text, voice notes (STT), images (vision)
- **Voice replies** — TTS voice notes for audio messages and podcasts
- **Voice cloning** — users can clone voices from a 3-second audio sample
- **LLM fallback** — configurable provider chain with per-capability endpoints
- **Tool calling** — LLM auto-searches the web when needed (`web_search` + `read_page`)
- **Web search fallback** — DuckDuckGo -> Bing (headless scrape) -> Google (headless scrape)
- **Podcast generation** — `/podcast <topic>` researches and generates a voice-note podcast
- **Task scheduler** — users schedule recurring news/search/podcast deliveries
- **Prompt injection defenses** — input/output sanitization, URL validation, exfiltration blocking
- **Per-user data isolation** — separate profile, schedule, and voice files per user

## Architecture

```
yobo-bot/
├── gateway/                       # Node.js WhatsApp gateway
│   └── src/
│       ├── index.js               # Entry point, wires WS bridge <-> WhatsApp
│       ├── config.js              # Environment config
│       ├── ws-bridge.js           # WebSocket server
│       └── whatsapp.js            # Baileys connection, QR auth, media download
├── agent/                         # Python AI agent
│   ├── main.py                    # WS client, message dispatch, scheduler bootstrap
│   ├── admin.py                   # Admin command handling (/add, /ignore, /clear)
│   ├── config.py                  # Environment + LLM config loader
│   ├── graph.py                   # LangGraph pipeline definition
│   ├── state.py                   # Pipeline state schema (TypedDict)
│   ├── models.py                  # User profile model (TypedDict)
│   ├── jid.py                     # JID utility functions (single source of truth)
│   ├── sanitize.py                # Prompt injection + exfiltration defenses
│   ├── tools.py                   # LLM tools (web_search, read_page) + Playwright
│   ├── nodes/                     # Pipeline nodes
│   │   ├── load_user.py           # Load/create profile, admin approval gate
│   │   ├── resolve_input.py       # text passthrough, audio->STT, image->vision, voice clone
│   │   ├── classify_intent.py     # Slash commands -> skill routing
│   │   ├── execute_skill.py       # Run matched skill
│   │   ├── tts.py                 # Text-to-speech with user voice preferences
│   │   └── save_user.py           # Append history, trim, save with file lock
│   ├── skills/                    # User-facing skills
│   │   ├── text_chat.py           # Conversational AI with tool calling
│   │   ├── web_search.py          # Explicit /search command
│   │   ├── podcast.py             # Research + generate podcast voice note
│   │   ├── schedule.py            # Recurring task scheduling
│   │   └── voice.py               # Voice management (set, add, remove, list)
│   └── services/                  # Backend services
│       ├── llm.py                 # LLM with fallback + tool calling loop
│       ├── user_store.py          # File-based user profiles with locking
│       ├── voice_store.py         # Per-user voice sample storage
│       ├── scheduler.py           # Cron-like background task runner
│       └── task_handlers.py       # Scheduler task executors (news, search, podcast)
└── data/                          # Runtime data (gitignored)
    ├── users/                     # User profiles (JSON per number)
    ├── schedules/                 # Per-user schedule files
    ├── voices/                    # Per-user voice samples for cloning
    └── auth/                      # Baileys auth state
```

## LangGraph Pipeline

```
load_user -> resolve_input -> classify_intent -> execute_skill -> tts -> save_user
    |             |                  |
    |             |                  ├── /search   -> web_search skill
    |             |                  ├── /podcast  -> podcast skill
    |             |                  ├── /schedule -> scheduler skill
    |             |                  ├── /voice    -> voice management skill
    |             |                  └── (default) -> text_chat (with tool calling)
    |             |
    |             └── voice clone pending? -> save voice -> skip to save_user
    |
    └── new/ignored user? -> skip to save_user
```

## Setup

```bash
# 1. Clone and enter
cd yobo-bot

# 2. Copy config templates
cp .env.example .env
cp agent/llm_config.yaml.example agent/llm_config.yaml

# 3. Edit .env and agent/llm_config.yaml with your API keys and LAN endpoints

# 4. Install everything
make setup

# 5. First run — foreground (to scan QR code)
# Terminal 1:
make gateway
# Terminal 2:
make agent
```

## Running

```bash
# Foreground (for development / QR scan)
make gateway          # Terminal 1
make agent            # Terminal 2

# Background
make start            # Start both, logs in logs/
make stop             # Stop both
make status           # Check if running
make logs-gateway     # Tail gateway logs
make logs-agent       # Tail agent logs
```

## LLM Configuration

Edit `agent/llm_config.yaml` to configure endpoints per capability. Each has a fallback chain — providers are tried in order.

```yaml
text:
  - name: local-llm
    base_url: http://YOUR_LAN_IP:52415/v1
    api_key: "no-key"
    model: mlx-community/Qwen3.5-9B-8bit
    max_tokens: 32000
    temperature: 0.5

vision:
  - name: mlx-omni
    type: mlx_omni                    # custom /v1/vision endpoint
    base_url: http://YOUR_LAN_IP:8765/v1
    model: mlx-community/Qwen2.5-VL-3B-Instruct-8bit

stt:
  - name: mlx-omni
    base_url: http://YOUR_LAN_IP:8765/v1
    model: mlx-community/Qwen3-ASR-0.6B-8bit

tts:
  - name: mlx-omni
    base_url: http://YOUR_LAN_IP:8765/v1
    model: mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit
    voice: Chelsie
    response_format: opus       # requires ffmpeg on the server
```

Use `${ENV_VAR}` in YAML values to reference `.env` variables (e.g., `api_key: ${OPENAI_API_KEY}`).

## Commands

### User commands
| Command | Description |
|---|---|
| `/search <query>` | Search the web and summarize |
| `/s <query>` | Search shortcut |
| `/podcast <topic>` | Generate a podcast voice note |
| `/p <topic>` | Podcast shortcut |
| `/schedule <type> <freq> [day] <time> [--audio] <topic>` | Schedule a recurring task |
| `/schedules` | List your scheduled tasks |
| `/unschedule <id>` | Remove a scheduled task |
| `/voice` | Show current voice and usage |
| `/voice list` | List all available voices |
| `/voice set <name>` | Switch TTS voice |
| `/voice add <name> [transcript]` | Add a custom voice (then send audio) |
| `/voice remove <name>` | Remove a custom voice |
| `/help` | Show available commands |

### Schedule examples
```
/schedule news daily 4pm AI technology
/schedule podcast daily 8am --audio tech news
/schedule search weekly monday 9am weather forecast
```

### Voice cloning
```
# Step 1: Start adding a voice with the transcript of what you'll say
/voice add myvoice Hello, this is what my voice sounds like

# Step 2: Send a voice note saying exactly that

# The bot saves your voice and uses it for all future TTS replies

# Switch back to a built-in voice
/voice set Chelsie
```

**Voice consistency notes:**
- Custom cloned voices use speaker embedding extraction for consistency across generations
- Including a transcript when adding a voice (`/voice add name <transcript>`) improves cloning quality
- For best results, record a clear 3-10 second sample with minimal background noise

### Admin commands (self-chat only)
| Command | Description |
|---|---|
| `/add <number>` | Approve a user |
| `/add` | Approve the last pending user |
| `/ignore <number>` | Ignore a user |
| `/clear` | Clear all WhatsApp chats |
| `/clear <number>` | Clear a specific chat |

## Security

- **Input sanitization** — zero-width character stripping on all user messages
- **Tool output sanitization** — injection pattern detection and redaction at the source (web_search, read_page)
- **URL validation** — blocks SSRF (private networks) and exfiltration endpoints (webhook.site, ngrok, etc.)
- **LLM output sanitization** — redacts leaked JIDs, file paths, and API keys before sending
- **System prompt hardening** — explicit rules against data leaks, instruction override, and cross-user access
- **Per-user data isolation** — separate files for profiles, schedules, and voice samples; path traversal protection on voice names
- **Tool result wrapping** — clear boundary markers so the LLM distinguishes data from instructions
