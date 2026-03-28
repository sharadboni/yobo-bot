"""Centralized token limits, temperatures, and generation parameters."""

# --- Temperatures ---
TEMP_CLASSIFIER = 0.0         # deterministic yes/no
TEMP_SOURCE_PICKER = 0.0      # deterministic source selection
TEMP_FAST_CHAT = 0.5          # balanced conversational replies
TEMP_TOOL_CALLING = 0.5       # focused tool decisions
TEMP_TOOL_ANSWER = 0.7        # natural final answers
TEMP_PODCAST_SCRIPT = 0.85    # creative, engaging script writing
TEMP_DOCUMENT = 0.3           # precise document analysis

# --- Token limits ---

# Classifier — yes/no decision
MAX_TOKENS_CLASSIFIER = 1

# Source picker — comma-separated source names
MAX_TOKENS_SOURCE_PICKER = 30

# Fast chat — short conversational replies
MAX_TOKENS_FAST_CHAT = 512

# Tool calling — per-round cap (tool decision + call, not full answer)
MAX_TOKENS_TOOL_ROUND = 4096

# Tool calling — final answer after tools return
MAX_TOKENS_TOOL_ANSWER = 8192

# Document processing
MAX_TOKENS_DOCUMENT = 8192

# Podcast script generation (~3-4 mins of speech at ~150 words/min = 450-600 words)
MAX_TOKENS_PODCAST_SCRIPT = 2048
MAX_WORDS_PODCAST_MONO = 500
MAX_WORDS_PODCAST_DIALOGUE = 1000

# Document extraction
MAX_DOC_CHARS = 200000
