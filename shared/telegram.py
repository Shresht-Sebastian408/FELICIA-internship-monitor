"""
Telegram delivery.

Postings are sent one message each and can arrive in bursts of ~100, so
dispatch is paced and honours Telegram's `retry_after` rather than dropping
messages on the floor.
"""
import html
import time

import requests

from shared.config import PLACEHOLDERS

API_ROOT = "https://api.telegram.org"

# Telegram allows roughly 20 messages/minute to a single group or channel.
# 3.5s between sends keeps a burst comfortably under that.
DEFAULT_GAP_SECONDS = 3.5

# Telegram rejects anything over 4096 characters outright.
MAX_MESSAGE_CHARS = 4096

MAX_SEND_ATTEMPTS = 4


def is_configured(value: str | None) -> bool:
    return bool(value) and value not in PLACEHOLDERS


def esc(text) -> str:
    """
    Escape for Telegram's HTML parse mode.

    Company names and job titles are arbitrary third-party strings: `AT&T`,
    `Research & Development`, `<Senior> Engineer`. Unescaped they produce a
    400 from Telegram and the posting is silently lost.
    """
    return html.escape(str(text if text is not None else ""), quote=False)


def truncate(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n\n[truncated]"


class TelegramSender:
    """
    A configured bot/chat pair with paced delivery.

    `dry_run` renders and validates every message without calling the API, which
    is how the pipeline can be exercised end to end with no credentials.
    """

    def __init__(self, token: str, chat_id: str, label: str = "telegram",
                 gap_seconds: float = DEFAULT_GAP_SECONDS, dry_run: bool = False,
                 sleep=time.sleep, session=None):
        self.token = token
        self.chat_id = chat_id
        self.label = label
        self.gap_seconds = gap_seconds
        self.dry_run = dry_run
        self._sleep = sleep
        self._session = session or requests
        self._last_send_at: float | None = None

        self.sent = 0
        self.failed = 0
        self.rendered: list[str] = []

    @property
    def enabled(self) -> bool:
        return is_configured(self.token) and is_configured(self.chat_id)

    def _pace(self):
        """Hold the configured gap between consecutive sends."""
        if self._last_send_at is None:
            return
        elapsed = time.monotonic() - self._last_send_at
        remaining = self.gap_seconds - elapsed
        if remaining > 0:
            self._sleep(remaining)

    def send(self, text: str, parse_mode: str = "HTML",
             disable_preview: bool = True) -> bool:
        """Send one message. Returns True when Telegram accepted it."""
        text = truncate(text)
        self.rendered.append(text)

        if self.dry_run:
            self.sent += 1
            print(f"[{self.label}] DRY RUN, would send {len(text)} chars")
            return True

        if not self.enabled:
            print(f"[{self.label}] Bot token or chat id missing. Skipping send.")
            return False

        url = f"{API_ROOT}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_preview,
        }

        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            self._pace()
            try:
                resp = self._session.post(url, json=payload, timeout=15)
                self._last_send_at = time.monotonic()

                if resp.status_code == 429:
                    # Telegram tells us exactly how long to hold off.
                    retry_after = DEFAULT_GAP_SECONDS
                    try:
                        retry_after = float(
                            resp.json().get("parameters", {}).get("retry_after", retry_after)
                        )
                    except Exception:
                        pass
                    print(f"[{self.label}] Flood limit; waiting {retry_after:.0f}s "
                          f"(attempt {attempt}/{MAX_SEND_ATTEMPTS})")
                    self._sleep(retry_after)
                    continue

                if resp.status_code == 400 and parse_mode:
                    # Almost always a markup problem in third-party text. Retry
                    # once as plain text so the posting still gets delivered.
                    print(f"[{self.label}] 400 from Telegram; retrying without parse_mode")
                    payload.pop("parse_mode", None)
                    parse_mode = ""
                    continue

                resp.raise_for_status()
                self.sent += 1
                return True

            except Exception as e:
                print(f"[{self.label}] Send failed (attempt {attempt}/{MAX_SEND_ATTEMPTS}): {e}")
                self._last_send_at = time.monotonic()
                if attempt < MAX_SEND_ATTEMPTS:
                    self._sleep(min(2 ** attempt, 15))

        self.failed += 1
        return False


def internships_sender(**kwargs) -> TelegramSender:
    from shared.config import TELEGRAM_INTERNSHIPS_BOT_TOKEN, TELEGRAM_INTERNSHIPS_CHAT_ID
    return TelegramSender(TELEGRAM_INTERNSHIPS_BOT_TOKEN, TELEGRAM_INTERNSHIPS_CHAT_ID,
                          label="internships", **kwargs)
