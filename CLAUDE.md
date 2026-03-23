# CLAUDE.md

## Project Overview

Yobo Bot is a two-tier WhatsApp AI chatbot. A Node.js gateway (Baileys) handles WhatsApp connectivity, a Python agent (LangGraph) handles AI logic. They communicate over WebSocket on port 8765.

## Running

```bash
make gateway          # Foreground (for QR scan)
make agent            # Foreground
make start            # Both in background
make stop             # Stop both
make restart          # Restart both
make status           # Check if running
```

Gateway runs from repo root (not `cd gateway`). Auth data saves to `./data/auth/`.

Node.js is installed via nvm — the Makefile uses the absolute path `$(HOME)/.nvm/versions/node/v24.14.0/bin/node` for background mode since nvm isn't available in non-interactive shells.

## Key Architecture

- **Gateway** (`gateway/src/`): Baileys WhatsApp connection, WebSocket bridge, media download/upload
- **Agent** (`agent/`): LangGraph pipeline — `load_user -> resolve_input -> classify_intent -> execute_skill -> tts -> save_user`
- **State** (`agent/state.py`): TypedDict passed through the pipeline. Add new fields here when skills need to pass data to downstream nodes.
- **Skills** (`agent/skills/`): Each skill is an async function that receives state dict and returns a partial state update.
- **Services** (`agent/services/`): LLM calls with fallback chains, user/voice storage, scheduler.

## LLM Configuration

`agent/llm_config.yaml` — per-capability provider chains (text, text_fast, vision, stt, tts). Each capability has a list of providers tried in order. The mlx-omni-server instances run on LAN at `10.0.0.3:8765` and `10.0.0.5:8765`.

## Important Patterns

- **WhatsApp strips underscores**: Voice names like `af_heart` arrive as `afheart`. Always normalize by stripping underscores when matching user input against known identifiers.
- **TTS dual-model routing**: Kokoro (82M) for preset voices, Qwen3-TTS (1.7B) for voice cloning. The server auto-selects based on `ref_audio` presence. Kokoro does NOT support voice cloning.
- **Dialogue TTS**: Multi-voice audio via `POST /v1/audio/dialogue` on mlx-omni-server. Each segment specifies its own voice. Segments with `ref_audio` route to Qwen3-TTS, others to Kokoro.
- **no_think prefill**: Qwen3 models skip reasoning when an empty `<think></think>` block is prefilled. Used for simple chat, summaries, and script generation.
- **State propagation**: If a skill produces data needed by a downstream node (e.g. `dialogue_segments` for TTS), the field must exist in `AgentState` in `state.py`.

## When Adding/Changing Functionality

1. **Update the help command** in `agent/nodes/execute_skill.py` if adding or changing user-facing commands.
2. **Update the README** commands table and any relevant sections.
3. **Update this CLAUDE.md** if the change affects architecture, patterns, or important conventions.

## Testing Changes

After modifying agent code, restart the agent:
```bash
kill $(cat logs/agent.pid) 2>/dev/null
nohup .venv/bin/python -m agent.main > logs/agent.log 2>&1 & echo $! > logs/agent.pid
```

After modifying gateway code, restart the gateway (will need QR re-scan if auth is missing):
```bash
make restart
```

Check logs:
```bash
tail -f logs/agent.log
tail -f logs/gateway.log
```

## External Dependencies

- **mlx-omni-server** (`/home/bobo/Dev/mlx-omni-server`): TTS, STT, Vision, S2S endpoints. Repo: `github.com:sharadboni/mlx-omni-server`. Changes there need deploy to LAN machines.
- **exo cluster**: Distributed LLM inference on LAN (`10.0.0.3:52415`, `10.0.0.5:52415`). Serves text/text_fast models.
