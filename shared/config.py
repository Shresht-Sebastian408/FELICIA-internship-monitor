"""Environment and path configuration."""
import sys
import os
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Windows consoles default to cp1252, which raises on the box-drawing and emoji
# characters the run logs use. Force UTF-8 before anything prints.
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"

INTERNSHIPS_DATA_DIR = DATA_DIR / "internships"
INTERNSHIPS_STATE_FILE = INTERNSHIPS_DATA_DIR / "state.json"
INTERNSHIPS_ARCHIVE_DIR = INTERNSHIPS_DATA_DIR / "archive"


def _split_keys(*raw_values: str) -> list[str]:
    """Flatten comma-separated key lists, trim, and drop repeats in order."""
    out: list[str] = []
    for raw in raw_values:
        if not raw:
            continue
        for part in raw.split(","):
            clean = part.strip()
            if clean and clean not in out:
                out.append(clean)
    return out


GEMINI_API_KEYS = _split_keys(
    os.getenv("GEMINI_API_KEYS", ""),
    os.getenv("GEMINI_API_KEY", ""),
)

# Placeholder values shipped in .env.example. Treated as "unset" so a partially
# configured checkout skips dispatch instead of posting to a bogus chat.
PLACEHOLDERS = {
    "",
    "your_telegram_bot_token_here",
    "your_telegram_chat_id_here",
    "your_gemini_api_key_here",
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_INTERNSHIPS_BOT_TOKEN = (
    os.getenv("TELEGRAM_INTERNSHIPS_BOT_TOKEN", "") or TELEGRAM_BOT_TOKEN
)
TELEGRAM_INTERNSHIPS_CHAT_ID = (
    os.getenv("TELEGRAM_INTERNSHIPS_CHAT_ID", "") or TELEGRAM_CHAT_ID
)

# Model ids expire. `gemini-2.5-flash` still appears in the models listing but
# returns 404 "no longer available to new users" for keys created after its
# cutoff, so a `-latest` alias is the default.
#
# Free-tier request quota is also metered PER MODEL, not per key:
#     "limit: 20, model: gemini-3.8-flash"
# Classification here is short-form categorisation, so the lite model is both
# sufficient for the task and cheaper on quota.
GEMINI_INTERNSHIPS_MODEL = os.getenv(
    "GEMINI_INTERNSHIPS_MODEL", "gemini-flash-lite-latest"
)

# Kept as a separate name so callers that want a stronger model for some other
# purpose have an obvious knob, rather than editing the classification default.
GEMINI_FLASH_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-flash-latest")


def is_configured(value: str | None) -> bool:
    return bool(value) and value not in PLACEHOLDERS


def load_json_config(filename: str) -> dict | list:
    with open(CONFIG_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


def load_internship_sources() -> dict:
    return load_json_config("internship_sources.json")


for _d in (INTERNSHIPS_DATA_DIR, INTERNSHIPS_ARCHIVE_DIR):
    _d.mkdir(parents=True, exist_ok=True)
