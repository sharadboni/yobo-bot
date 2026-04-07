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
| **Weather** | Ask about weather anywhere — uses Open-Meteo API with date range support |
| **Podcasts** | `/podcast <topic>` generates a ~5 minute researched voice-note podcast |
| **Two-voice podcasts** | `/podcast <topic> --duo` creates a two-voice conversation via VibeVoice |
| **Scheduled updates** | `/schedule news daily 8am AI technology` delivers recurring digests |
| **Voice cloning** | Clone your own voice from a 3-second sample for TTS replies |
| **79 voice presets** | 54 Kokoro + 25 VibeVoice voices, set by number or name |
| **Web search** | `/search <query>` or just ask — the bot decides when to search |
| **News aggregation** | `/news <topic> [--from source]` — LLM picks sources or target a specific one; recency keywords filter to last 24h |
| **Google Calendar** | `/google link` to connect your account, then `/google calendar` or ask "Am I free at 3pm?" |
| **Group chats** | Add the bot to a group — responds to @mentions, replies to its messages, and `/commands`. Shared profile, history, and schedules per group. Supports images, documents, and voice in groups too |

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

### News

| Command | Example |
|---|---|
| `/news <topic>` | `/news AI technology` — 10-story briefing |
| `/news top 3 <topic>` | `/news top 3 indian news` — specific count |
| `/news <topic> --from <source>` | `/news AI --from hn` — single source |
| `/news latest <topic>` | `/news latest crypto` — last 24h only |

Keywords like `latest`, `recent`, `today`, `breaking` automatically filter to the last 24 hours. Results are always sorted newest-first.

**Source aliases:** `hn` (Hacker News), `reuters`, `ap`, `bbc`, `aljazeera`/`aj`, `npr`, `wsj`, `ars` (Ars Technica), `ndtv`, `google`

### Search

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

The `--dialogue` / `--duo` flag creates a two-voice conversation using VibeVoice. Set your host and guest with `/voice set duo`. Custom (cloned) voices work as host — segments are automatically routed to Qwen3-TTS for voice cloning. Scripts target ~500 words (mono) or ~750 words (dialogue) for ~5 minutes of audio.

### Documents

Send any file with a caption — the caption is your instruction.

| File type | Caption example |
|---|---|
| PDF | "Summarize this" |
| CSV | "What are the top 5 entries?" |
| JSON | "Review this config for issues" |
| TXT, HTML, Markdown, XML | "Extract the key points" |

No caption defaults to "Summarize this document." Documents up to 200k characters are supported.

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

Scheduled tasks use the same research pipeline as live requests — aggregated news from multiple sources with page reads for deeper content.

### Voice

| Command | Description |
|---|---|
| `/say <text>` | Convert text to speech (no LLM, direct TTS) |
| `/voice` | Show current voices |
| `/voice list` | Browse all voices (numbered, grouped by language) |
| `/voice set single <#\|name>` | Set voice for regular TTS (Kokoro) |
| `/voice set duo <host#> <guest#>` | Set both podcast dialogue voices (VibeVoice) |
| `/voice set duo host <#\|c#\|name>` | Set just the dialogue host |
| `/voice set duo guest <#\|name>` | Set just the dialogue guest |
| `/voice add <name> <transcript>` | Clone a voice — then send a voice note |
| `/voice remove <name\|c#>` | Remove a custom voice |

Voices are fetched from the TTS server on startup. Use numbers from `/voice list`, friendly names (e.g. `Heart`, `Emma`), or `c1`/`c2` for custom voices.

**Voice cloning example:**
```
/voice add myvoice Hello, this is what my voice sounds like
# Then send a voice note saying exactly that
/voice set single c1        # Use for regular TTS
/voice set duo host c1      # Use as podcast host (voice-cloned)
```

**Tri-model TTS:**
- **Kokoro 82M** — 54 preset voices for single-voice TTS (`/voice set single`)
- **VibeVoice 0.5B** — 25 multi-speaker voices for podcast dialogue (`/voice set duo`)
- **Qwen3-TTS 1.7B** — voice cloning from 3-10s samples (automatic when a custom voice is active)

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
│   ├── config.py                  # System prompts (3 independent), LLM config loader
│   ├── constants.py               # Token limits, temperatures, generation parameters
│   ├── sanitize.py                # Prompt injection defenses
│   ├── tools.py                   # Web search, news, weather, Wikipedia, Playwright
│   ├── nodes/                     # Pipeline stages
│   │   ├── load_user.py           # Profile loading, admin approval gate
│   │   ├── resolve_input.py       # Text/audio/image/document normalization
│   │   ├── classify_intent.py     # Command routing
│   │   ├── execute_skill.py       # Skill execution + /help
│   │   ├── tts.py                 # Text-to-speech (single + multi-voice dialogue)
│   │   └── save_user.py           # History management
│   ├── skills/                    # User-facing capabilities
│   │   ├── text_chat.py           # Classifier + smart routing + tool calling
│   │   ├── web_search.py          # /search command
│   │   ├── podcast.py             # Research + script + multi-voice TTS
│   │   ├── schedule.py            # Recurring tasks
│   │   └── voice.py               # Voice management
│   └── services/                  # Backend services
│       ├── llm.py                 # LLM calls with fallback chains + thinking control
│       ├── user_store.py          # File-based profiles with locking
│       ├── voice_store.py         # Voice sample storage
│       ├── scheduler.py           # Background task scheduler
│       └── task_handlers.py       # Scheduled task executors (same pipeline as live)
└── data/                          # Runtime data (gitignored)
    ├── users/                     # Per-user profiles (50 messages history)
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
- **resolve_input** — normalizes input: text passthrough, audio→STT, image→vision, document→text extraction (with prompt injection sanitization)
- **classify_intent** — routes `/commands` to skills, everything else to `text_chat`
- **execute_skill** — runs the matched skill (text_chat, web_search, podcast, schedule, voice)
- **tts** — generates voice reply: single voice via `/v1/audio/speech`, multi-voice via `/v1/audio/dialogue`
- **save_user** — appends to chat history, trims to 50 messages, saves with file lock

### Smart Routing

Every free-text message is routed to the right model:

```
User message
  │
  ▼
Classifier (fast model, 1 token, temp=0) — "Does this need a lookup?"
  │
  ├── No  → Fast model (4B, short reply, ~2-5s)
  │
  └── Yes → Big model (9B) + tools (news, search, weather, wiki, ~30-90s)
```

**Full routing table:**

| Input | Classifier | Model | History | Prompt |
|---|---|---|---|---|
| Simple chat ("Hi", jokes, coding) | yes → no | Fast (4B) | last 20 msgs | Short (2-3 sentences) |
| Needs lookup (news, weather, facts) | yes → yes | Big (9B) + tools | last 20 msgs | Tool-first ("MUST call a tool") |
| Document with caption | skipped | Big (9B), no tools | last 20 msgs | Document analysis |
| `/search <query>` | skipped | Big (9B) | — | Direct search |
| `/podcast <topic>` | skipped | Big (9B) | — | Script generation |
| Scheduled news/search | skipped | Big (9B) | — | Same pipeline as live requests |
| Voice note | STT first | then classifier | — | — |
| Image with caption | Vision first | then classifier | — | — |

**Classifier:** Defaults to YES on failure — safer to use tools than to hallucinate.

### Prompt Architecture

Three fully independent system prompts — no shared base, no conflicting instructions:

| Path | First line | Focus | Temperature |
|---|---|---|---|
| **Fast** (4B) | "You are Yobo, a WhatsApp assistant" | 2-3 sentences, no tool mentions | 0.5 |
| **Tools** (9B) | "You MUST call a tool before answering" | Tool routing table, complete answers | 0.5 |
| **Document** (9B) | "Analyze the document content" | Thorough but scannable | 0.3 |

The tool-calling prompt puts the imperative first because the model weights early instructions most heavily. All prompts inject today's date at runtime.

### Generation Parameters

All token limits and temperatures are centralized in `agent/constants.py`:

| Task | Max Tokens | Temperature |
|---|---|---|
| Classifier | 1 | 0.0 |
| Source picker | 30 | 0.0 |
| Fast chat | 512 | 0.5 |
| Tool round (per call) | 4,096 | 0.5 |
| Tool final answer | 8,192 | 0.7 |
| Document processing | 8,192 | 0.3 |
| Podcast script | 2,048 | 0.85 |
| Scheduled news | 6,000 | 0.5 |
| Scheduled search | 4,096 | 0.5 |

### News Aggregation

The fast model picks 3-5 relevant sources per query (Google News always included), or you can target a specific source with `--from`:

| Source | Alias | Specialty |
|---|---|---|
| Google News | `google` | General aggregator (always included) |
| Hacker News | `hn` | Tech, startups, AI (via Algolia API) |
| Reuters | `reuters` | Wire service, neutral/factual |
| AP News | `ap` | US news, general |
| BBC | `bbc` | International, UK, Europe |
| Al Jazeera | `aj` | Middle East, Global South |
| NPR | `npr` | US politics, culture, science |
| WSJ | `wsj` | Business, finance, markets |
| Ars Technica | `ars` | Tech deep dives, science |
| NDTV | `ndtv` | India, South Asia |

Source selection is LLM-driven: "AI funding" → HN + WSJ, "Gaza" → Al Jazeera + BBC, "India cricket" → NDTV. Use `--from hn` to bypass the picker and fetch from a single source.

Results are always sorted newest-first. Queries with recency keywords (`latest`, `recent`, `today`, `breaking`, `current`, `new`) automatically filter to the last 24 hours via Google News `when:1d` and pubDate filtering.

### Podcast Research Pipeline

Podcasts gather research from three sources concurrently, then generate a script:

```
/podcast quantum computing --duo
  ├── news_search_aggregated() ─┐
  ├── wikipedia()               ├── concurrent
  ├── web_search()             ─┘
  ├── Read top 3 page URLs (Playwright)
  ├── Generate script (9B, temp=0.85, ~750 words)
  └── TTS: Kokoro (single) or VibeVoice (duo dialogue)
```

### Connection Resilience

- Gateway uses a mutable socket proxy — WhatsApp reconnections automatically use the fresh connection
- WhatsApp sends retry up to 3 times with 5s delay on connection drops
- Agent WebSocket uses ping/pong (20s interval, 10s timeout)
- `make stop` kills orphan processes via `pgrep` to prevent zombie accumulation
- Scheduled messages go through `markdown_to_whatsapp` + `sanitize_llm_output` before sending

## LLM Configuration

Edit `agent/llm_config.yaml` to configure endpoints. Each capability has a fallback chain — providers are tried in order. Add `type: mlx_omni` for mlx-omni-server providers to enable native thinking control.

```yaml
text:                                  # Tool-calling model (9B)
  - name: local-llm
    type: mlx_omni
    base_url: http://YOUR_LAN_IP:52415/v1
    model: mlx-community/Qwen3.5-9B-4bit
    max_tokens: 60000
    temperature: 0.5

text_fast:                             # Fast chat model (4B)
  - name: local-fast
    type: mlx_omni
    base_url: http://YOUR_LAN_IP:52415/v1
    model: mlx-community/Qwen3.5-4B-4bit
    max_tokens: 512
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
- **Per-user data isolation** — separate files per user with path traversal protection
- **Tool result wrapping** — boundary markers so the LLM distinguishes data from instructions
- **Scheduled output sanitization** — markdown stripping + output sanitization before delivery

> **Disclaimer:** These defenses reduce the risk of prompt injection but cannot eliminate it entirely. LLMs are inherently susceptible to adversarial inputs, and novel attack vectors are discovered regularly. Do not use this bot to process sensitive or confidential documents. Do not rely on it for security-critical decisions. Use at your own risk.
