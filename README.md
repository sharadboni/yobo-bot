# Yobo Bot

Two-tier WhatsApp AI chatbot. A Node.js gateway handles WhatsApp connectivity via Baileys, a Python agent handles all AI logic via LangGraph. They communicate over WebSocket.

```
WhatsApp <-> Gateway (Node.js/Baileys) <-> WebSocket :8765 <-> Agent (Python/LangGraph)
```

## Features

- **Admin approval** — new users must be approved via self-chat (`/add`, `/ignore`)
- **Multi-modal input** — text, voice notes (STT), images (vision)
- **Voice replies** — dual-model TTS: Kokoro (sub-second) for preset voices, Qwen3-TTS 1.7B for cloning
- **Voice cloning** — users can clone voices from a 3-second audio sample
- **LLM fallback** — configurable provider chain with per-capability endpoints
- **Tool calling** — LLM auto-searches the web when needed (`web_search` + `read_page`)
- **Web search fallback** — DuckDuckGo -> Tavily -> Bing (scrape) -> Serper (Google) -> Yahoo (scrape)
- **News search** — Google News RSS -> Reuters -> BBC -> AP (free, unlimited)
- **Wikipedia** — factual lookups via Wikipedia REST API
- **Podcast generation** — `/podcast <topic>` researches and generates a voice-note podcast
- **Task scheduler** — users schedule recurring news/search/podcast deliveries
- **Concurrent messaging** — send multiple messages without waiting, all process in parallel
- **Typing indicator** — shows "typing..." while processing, stays active during long operations
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

## Smart Routing

Messages are automatically routed to the right model:

```
User message
  |
  v
Classifier (4B, 1 token, ~1s) — "Does this need external data?"
  |
  ├── No  → Fast model (4B, no_think, max 512 tokens, ~5s)
  |
  └── Yes → Tool model (9B, thinking ON, web_search/news/wiki, ~10-30s)
```

This keeps simple conversations fast while complex queries that need web search, news, or Wikipedia get the full tool-calling model with reasoning.

### Thinking mode

The `no_think` trick prefills an empty `<think></think>` block as an assistant message, making Qwen3 skip reasoning and respond directly. This halves response time for simple tasks.

| Path | Model | Thinking | Why |
|---|---|---|---|
| Classifier | 4B | OFF | Just needs "yes" or "no" |
| Simple chat | 4B | OFF | Fast conversational replies |
| Tool calling | 9B | ON | Needs to reason about which tools to use and synthesize results |
| /search summary | 9B | OFF | Data already fetched, just summarize |
| /podcast script | 9B | OFF | Creative output, no reasoning needed |
| Scheduler handlers | 9B | OFF | Summaries from pre-fetched data |

## WhatsApp Formatting

All LLM output is stripped of markdown before sending — plain text only. The system prompt instructs the LLM to avoid formatting, and a post-processor (`markdown_to_whatsapp`) catches any remaining markdown:

- `**bold**` / `*italic*` -> plain text
- `## Headers` -> plain text
- `[links](url)` -> text (url)
- Bullet points -> plain text
- Code blocks -> removed

## Connection Resilience

- Agent uses WebSocket ping/pong (20s interval, 10s timeout) to detect dead connections
- Gateway retries failed WhatsApp sends up to 3 times with 5s delay (handles connection drops)
- `make restart` cleanly stops all processes before starting new ones
- `make stop` kills by PID file + orphan cleanup on port 8765

## LLM Configuration

Edit `agent/llm_config.yaml` to configure endpoints per capability. Each has a fallback chain — providers are tried in order.

```yaml
text:                                  # For tool calling (web search, news, wiki)
  - name: local-llm
    base_url: http://YOUR_LAN_IP:52415/v1
    api_key: "no-key"
    model: mlx-community/Qwen3.5-9B-8bit
    max_tokens: 32000
    temperature: 0.5

text_fast:                             # For simple chat (no tools needed)
  - name: local-fast
    base_url: http://YOUR_LAN_IP:52415/v1
    api_key: "no-key"
    model: mlx-community/Qwen3.5-4B-4bit
    max_tokens: 256
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
    model: mlx-community/Kokoro-82M-bf16
    voice: af_heart              # Kokoro: af_heart, af_bella, am_adam, bf_alice, etc.
    response_format: opus       # requires ffmpeg on the server
# Voice cloning automatically uses Qwen3-TTS-1.7B when user has a custom voice
```

Use `${ENV_VAR}` in YAML values to reference `.env` variables (e.g., `api_key: ${OPENAI_API_KEY}`).

## LLM Tools

The LLM can automatically call these tools during conversation:

| Tool | Use case | Sources |
|---|---|---|
| `web_search` | General queries (weather, prices, facts) | DuckDuckGo -> Tavily -> Bing (scrape) -> Serper (Google) -> Yahoo (scrape) |
| `news_search` | News, headlines, current events | Google News RSS -> Reuters RSS -> BBC RSS -> AP RSS -> fallback to web_search |
| `wikipedia` | Facts, people, places, history, science | Wikipedia REST API (free, unlimited) |
| `read_page` | Deep dive into a specific URL | Playwright headless browser |

**Search fallback chain**: if one provider fails or rate-limits, the next is tried automatically. API-based providers (Tavily, Serper) require keys in `.env`. Scrape-based providers (Bing, Yahoo) use headless Chromium with rotating user agents.

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
# (uses Qwen3-TTS 1.7B for cloning — higher quality, slower)

# Switch back to a built-in Kokoro voice (sub-second, consistent)
/voice set af_heart
```

**Dual-model TTS:**
- **Kokoro 82M** (default) — sub-second latency, 54 preset voices, consistent output
- **Qwen3-TTS 1.7B** (automatic for cloned voices) — better quality voice cloning from 3-10s samples
- The server picks the right model automatically based on whether a custom voice is active
- Including a transcript when adding a voice (`/voice add name <transcript>`) improves cloning quality

**Kokoro voice presets:**
- American: `af_heart`, `af_bella`, `af_nova`, `af_sky`, `am_adam`, `am_echo`, `am_eric`, `am_liam`
- British: `bf_alice`, `bf_emma`, `bm_daniel`, `bm_george`

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
