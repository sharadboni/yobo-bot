# Yobo Bot

An AI-powered WhatsApp assistant that understands text, voice, images, and documents. Built with a two-tier architecture: a Node.js gateway for WhatsApp connectivity and a Python agent for AI logic.

Pair with [mlx-omni-server](https://github.com/sharadboni/mlx-omni-server) to run TTS, STT, vision, and speech-to-speech models locally on Apple Silicon.

```
WhatsApp <-> Gateway (Node.js/Baileys) <-> WebSocket :8765 <-> Agent (Python/LangGraph)
```

## What It Can Do

| Capability | How It Works |
|---|---|
| **Chat** | Send any message — the bot searches the web, looks up news, or answers from knowledge as needed |
| **Voice notes** | Send a voice note and get a voice reply back (speech-to-speech) |
| **Images** | Send a photo with a caption like "What is this?" |
| **Documents** | Send a PDF, CSV, or text file with a caption like "Summarize this" |
| **Podcasts** | `/podcast <topic>` generates a researched voice-note podcast |
| **Two-voice podcasts** | `/podcast <topic> --dialogue` creates a HOST/GUEST conversation |
| **Scheduled updates** | `/schedule news daily 8am AI technology` delivers recurring digests |
| **Voice cloning** | Clone your own voice from a 3-second sample for TTS replies |
| **50+ voice presets** | Switch between voices with `/voice set af_bella` |
| **Web search** | `/search <query>` or just ask — the bot decides when to search |
| **News aggregation** | Pulls from 10 sources: Google News, Hacker News, Reuters, AP, BBC, Al Jazeera, NPR, WSJ, Ars Technica, NDTV |

## Quick Start

```bash
# 1. Copy config templates
cp .env.example .env
cp agent/llm_config.yaml.example agent/llm_config.yaml

# 2. Edit both files with your LLM endpoints

# 3. Install everything
make setup

# 4. First run (scan QR code)
make gateway    # Terminal 1
make agent      # Terminal 2

# 5. After QR scan, run in background
make start
```

## Running

| Command | Description |
|---|---|
| `make gateway` | Run gateway in foreground (for QR scan) |
| `make agent` | Run agent in foreground |
| `make start` | Start both in background |
| `make stop` | Stop both (kills orphan processes too) |
| `make restart` | Restart both |
| `make status` | Check if running |
| `make logs-gateway` | Tail gateway logs |
| `make logs-agent` | Tail agent logs |

## Commands

### Chat & Search

| Command | Example |
|---|---|
| `/search <query>` | `/search weather in Boston today` |
| `/s <query>` | `/s latest iPhone price` |

Or just ask naturally — the bot automatically searches when needed.

### Podcasts

| Command | Example |
|---|---|
| `/podcast <topic>` | `/podcast AI breakthroughs` |
| `/podcast <topic> --dialogue` | `/podcast space exploration --dialogue` |
| `/p <topic>` | `/p tech news --duo` |

The `--dialogue` / `--duo` flag creates a two-voice conversation. HOST uses your active voice, GUEST is automatically picked as a contrasting voice.

### Documents

Send any file with a caption — the caption is your instruction.

| File type | Caption example |
|---|---|
| PDF | "Summarize this" |
| CSV | "What are the top 5 entries?" |
| JSON | "Review this config for issues" |
| TXT, HTML, Markdown, XML | "Extract the key points" |

No caption defaults to "Summarize this document."

### Scheduling

```
/schedule news daily 8am AI technology
/schedule podcast weekly monday 9am tech news --audio
/schedule search daily 6pm weather forecast
```

| Command | Description |
|---|---|
| `/schedules` | List your scheduled tasks |
| `/unschedule <id>` | Remove a task |

### Voice

| Command | Description |
|---|---|
| `/voice` | Show your current voice |
| `/voice list` | Browse 50+ voices |
| `/voice set <name>` | Switch voice (underscores optional: `afheart` = `af_heart`) |
| `/voice add <name> <transcript>` | Clone a voice — then send a voice note |
| `/voice remove <name>` | Remove a custom voice |

**Voice cloning example:**
```
/voice add myvoice Hello, this is what my voice sounds like
# Then send a voice note saying exactly that
# Bot saves it and uses it for all future replies
```

**Built-in voices:** American (`af_heart`, `af_bella`, `af_nova`, `af_sky`, `am_adam`, `am_echo`, `am_fenrir`, `am_michael`, `am_puck`), British (`bf_alice`, `bf_emma`, `bm_fable`, `bm_george`), Spanish, Hindi, and more.

**Dual-model TTS:**
- **Kokoro 82M** — sub-second, 50+ preset voices (default)
- **Qwen3-TTS 1.7B** — higher quality voice cloning from 3-10s samples (automatic when a custom voice is active)

### Admin Commands (self-chat only)

| Command | Description |
|---|---|
| `/add <number>` | Approve a user |
| `/add` | Approve the last pending user |
| `/ignore <number>` | Ignore a user |
| `/clear` | Clear all WhatsApp chats |
| `/clear <number>` | Clear a specific chat |

New users are held in a pending state until approved. They receive a notification with `/help` when approved.

## How It Works

### Architecture

```
yobo-bot/
├── gateway/                       # Node.js WhatsApp gateway
│   └── src/
│       ├── index.js               # Entry point, outbound message handling
│       ├── whatsapp.js            # Baileys connection, QR auth, media download
│       ├── ws-bridge.js           # WebSocket server (agent <-> gateway)
│       └── config.js              # Environment config
├── agent/                         # Python AI agent
│   ├── main.py                    # WebSocket client, message dispatch
│   ├── graph.py                   # LangGraph pipeline definition
│   ├── config.py                  # System prompt, LLM config loader
│   ├── sanitize.py                # Prompt injection defenses
│   ├── tools.py                   # Web search, news, Wikipedia, Playwright
│   ├── nodes/                     # Pipeline stages
│   │   ├── load_user.py           # Profile loading, admin approval gate
│   │   ├── resolve_input.py       # Text/audio/image/document normalization
│   │   ├── classify_intent.py     # Command routing
│   │   ├── execute_skill.py       # Skill execution
│   │   ├── tts.py                 # Text-to-speech generation
│   │   └── save_user.py           # History management
│   ├── skills/                    # User-facing capabilities
│   │   ├── text_chat.py           # Smart routing + tool calling
│   │   ├── web_search.py          # /search command
│   │   ├── podcast.py             # Research + script + TTS
│   │   ├── schedule.py            # Recurring tasks
│   │   └── voice.py               # Voice management
│   └── services/                  # Backend services
│       ├── llm.py                 # LLM calls with fallback chains
│       ├── user_store.py          # File-based profiles with locking
│       ├── voice_store.py         # Voice sample storage
│       ├── scheduler.py           # Background task scheduler
│       └── task_handlers.py       # Scheduled task executors
└── data/                          # Runtime data (gitignored)
    ├── users/                     # Per-user profiles
    ├── schedules/                 # Per-user schedule files
    ├── voices/                    # Voice samples for cloning
    └── auth/                      # Baileys WhatsApp session
```

### Pipeline

Every incoming message flows through this LangGraph pipeline:

```
load_user -> resolve_input -> classify_intent -> execute_skill -> tts -> save_user
```

- **load_user** — loads profile, gates new/ignored users, notifies admin of new contacts
- **resolve_input** — normalizes input: text passthrough, audio->STT, image->vision, document->text extraction
- **classify_intent** — routes `/commands` to skills, everything else to `text_chat`
- **execute_skill** — runs the matched skill
- **tts** — generates voice reply if the input was audio or the skill requests it
- **save_user** — appends to chat history, trims, saves

### Smart Routing

```
User message
  │
  ▼
Classifier (fast model, 1 token) — "Does this need a lookup?"
  │
  ├── No  → Fast model (direct reply, ~2-5s)
  │
  └── Yes → Tool-calling model (web search, news, Wikipedia, ~10-30s)
```

The classifier errs on the side of searching — company info, funding rounds, specific facts all trigger a lookup. Simple greetings, math, and coding questions skip straight to the fast model.

Documents skip the classifier entirely and go to the full model for processing.

### News Aggregation

News queries fetch from 10 sources concurrently and deduplicate results:

| Source | Type |
|---|---|
| Google News | General aggregator |
| Hacker News | Tech community (via Algolia API) |
| Reuters, AP | Wire services (neutral, factual) |
| BBC, Al Jazeera | International perspectives |
| NPR, WSJ | US varied editorial leanings |
| Ars Technica | Tech, in-depth |
| NDTV | India |

Each result is tagged with its source so the LLM can synthesize across perspectives.

### Connection Resilience

- Gateway uses a mutable socket reference — reconnections automatically use the fresh connection
- WhatsApp sends retry up to 3 times with 5s delay on connection drops
- Agent WebSocket uses ping/pong (20s interval, 10s timeout)
- `make stop` kills orphan processes via `pgrep` to prevent zombie accumulation

## LLM Configuration

Edit `agent/llm_config.yaml` to configure endpoints. Each capability has a fallback chain — providers are tried in order.

```yaml
text:                                  # Tool-calling model
  - name: local-llm
    base_url: http://YOUR_LAN_IP:52415/v1
    model: mlx-community/Qwen3.5-9B-8bit
    max_tokens: 32000
    temperature: 0.5

text_fast:                             # Fast chat model
  - name: local-fast
    base_url: http://YOUR_LAN_IP:52415/v1
    model: mlx-community/Qwen3.5-4B-4bit
    max_tokens: 256
    temperature: 0.5

vision:                                # Image analysis
  - name: mlx-omni
    type: mlx_omni
    base_url: http://YOUR_LAN_IP:8765/v1
    model: mlx-community/Qwen2.5-VL-3B-Instruct-8bit

stt:                                   # Speech-to-text
  - name: mlx-omni
    base_url: http://YOUR_LAN_IP:8765/v1
    model: mlx-community/Qwen3-ASR-0.6B-8bit

tts:                                   # Text-to-speech
  - name: mlx-omni
    base_url: http://YOUR_LAN_IP:8765/v1
    model: mlx-community/Kokoro-82M-bf16
    voice: af_heart
    response_format: opus
```

Use `${ENV_VAR}` in YAML values to reference `.env` variables.

## Security

- **Input sanitization** — zero-width character stripping on all user messages
- **Tool output sanitization** — injection pattern detection and redaction
- **Document sanitization** — uploaded files are sanitized before LLM processing
- **URL validation** — blocks SSRF (private networks) and exfiltration endpoints
- **LLM output sanitization** — redacts leaked JIDs, file paths, and API keys
- **System prompt hardening** — rules against data leaks, instruction override, cross-user access
- **Per-user data isolation** — separate files per user with path traversal protection
- **Tool result wrapping** — boundary markers so the LLM distinguishes data from instructions

> **Disclaimer:** These defenses reduce the risk of prompt injection but cannot eliminate it entirely. LLMs are inherently susceptible to adversarial inputs, and novel attack vectors are discovered regularly. Do not use this bot to process sensitive or confidential documents. Do not rely on it for security-critical decisions. Use at your own risk.
