"""
Gemini access: key rotation, quota classification and schema-checked calls.

ATTRIBUTION
-----------
`GeminiClientManager` and the error-classification helpers in this module
originate from the Alpha-Forge project by Anchit Lahkar
(https://github.com/Anchitlahkar/Alpha-Forge), reused under the MIT licence its
README declares. See CREDITS.md.

Changes made here: model ids moved out to configuration, and `call_gemini_text`
was added alongside the structured call.
"""
import time
import json
import re
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel

from shared.config import GEMINI_API_KEYS, GEMINI_FLASH_MODEL
from shared.json_repair import repair_json

RATE_LIMIT_MARKERS = ["RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED", "429", "RATE LIMIT", "RATE_LIMIT"]

# Markers that mean the key is done for the day, not for the minute. A per-day
# quota will not recover inside a run, so waiting on it is pointless.
DAILY_QUOTA_MARKERS = ["PERDAY", "PER DAY", "PER_DAY", "REQUESTSPERDAY", "DAILY LIMIT"]

# Markers that mean the key is bad, not throttled. No amount of waiting helps.
DEAD_KEY_MARKERS = ["API_KEY_INVALID", "API KEY NOT VALID", "PERMISSION_DENIED", "UNAUTHENTICATED"]

DEFAULT_COOLDOWN_SECONDS = 60.0
# Ceiling on cumulative sleeping across a whole process, so a genuinely dry day
# ends the run instead of idling in CI for an hour.
MAX_TOTAL_WAIT_SECONDS = 300.0

_RETRY_DELAY_PATTERNS = (
    r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s",
    r"retry[_ ]after['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)",
)


def is_rate_limit_error(err: str) -> bool:
    return any(m in err.upper() for m in RATE_LIMIT_MARKERS)


def is_daily_quota_error(err: str) -> bool:
    return any(m in err.upper().replace("-", "") for m in DAILY_QUOTA_MARKERS)


def is_dead_key_error(err: str) -> bool:
    return any(m in err.upper() for m in DEAD_KEY_MARKERS)


def parse_retry_delay(err: str) -> float | None:
    """Gemini returns the wait it wants in the 429 body; honour it when present."""
    for pattern in _RETRY_DELAY_PATTERNS:
        m = re.search(pattern, err, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return None


class GeminiClientManager:
    """
    Rotates across keys and, when every key is throttled, waits for the soonest
    one to come back instead of aborting.

    Most free-tier 429s are per-minute, not per-day, so treating the last 429 as
    permanent exhaustion threw away a whole run while key 1 was already usable
    again. Keys are cooled down individually and reused; only per-day quota and
    genuinely bad keys are retired for the process.
    """

    def __init__(self, keys: list[str] | None = None, sleep=time.sleep, monotonic=time.monotonic):
        self.keys = list(GEMINI_API_KEYS if keys is None else keys)
        self._sleep = sleep
        self._monotonic = monotonic

        # index -> monotonic timestamp before which the key must not be used
        self.cooldown_until: dict[int, float] = {}
        # indices retired for this process (daily quota exhausted, or invalid)
        self.retired: set[int] = set()
        self.total_waited = 0.0

        print(f"Loaded {len(self.keys)} Gemini keys")
        for i, key in enumerate(self.keys):
            masked = key[:4] + "..." if len(key) > 4 else "xxxx..."
            print(f"Key {i + 1}: {masked}")
        print()

        # Keys are validated lazily. The old startup sweep spent one live request
        # per key on every import, which burned quota before any work and could
        # trip the very per-minute limit it was checking for.
        self.current_index = 0
        self.client = None
        self._init_client()

    def _init_client(self):
        if not self.keys or self.current_index >= len(self.keys):
            print("[Gemini] No API keys available.")
            self.client = None
            return
        print(f"[Gemini] Using key {self.current_index + 1}/{len(self.keys)}")
        self.client = genai.Client(api_key=self.keys[self.current_index])
        print("[Gemini] New client created\n")

    def _usable_now(self, i: int) -> bool:
        return i not in self.retired and self.cooldown_until.get(i, 0.0) <= self._monotonic()

    def mark_failed(self, error: str):
        """Record why the current key failed so rotation can pick sensibly."""
        i = self.current_index
        if is_dead_key_error(error):
            print(f"[Gemini] Key {i + 1} rejected (invalid credentials); retiring it")
            self.retired.add(i)
        elif is_daily_quota_error(error):
            print(f"[Gemini] Key {i + 1} out of daily quota; retiring it for this run")
            self.retired.add(i)
        else:
            delay = parse_retry_delay(error) or DEFAULT_COOLDOWN_SECONDS
            self.cooldown_until[i] = self._monotonic() + delay
            print(f"[Gemini] Key {i + 1} throttled; cooling down {delay:.0f}s")

    def rotate_key(self) -> bool:
        """
        Move to the next usable key. If every key is merely cooling down, wait
        for the soonest and carry on. Raises only when nothing can recover.
        """
        if not self.keys:
            raise RuntimeError("All Gemini API keys exhausted")

        n = len(self.keys)
        # Prefer a key that is ready right now, scanning forward from the current one.
        for step in range(1, n + 1):
            candidate = (self.current_index + step) % n
            if self._usable_now(candidate):
                self.current_index = candidate
                print(f"[Gemini] Rotating to key {candidate + 1}/{n}")
                self._init_client()
                return True

        # Nothing ready. Anything still cooling down?
        waiting = {i: t for i, t in self.cooldown_until.items()
                   if i not in self.retired and t > self._monotonic()}
        if not waiting:
            self.client = None
            raise RuntimeError("All Gemini API keys exhausted")

        soonest = min(waiting, key=lambda i: waiting[i])
        wait = max(0.0, waiting[soonest] - self._monotonic())

        if self.total_waited + wait > MAX_TOTAL_WAIT_SECONDS:
            self.client = None
            raise RuntimeError(
                f"All Gemini API keys exhausted (waited {self.total_waited:.0f}s, "
                f"next key needs {wait:.0f}s more)"
            )

        print(f"[Gemini] All {n} keys throttled; waiting {wait:.0f}s for key {soonest + 1}")
        self._sleep(wait)
        self.total_waited += wait
        self.cooldown_until.pop(soonest, None)
        self.current_index = soonest
        self._init_client()
        return True


client_manager = GeminiClientManager()


def call_gemini_structured(prompt: str, schema_model: type[BaseModel],
                           model: str | None = None, retries: int = 3) -> Any:
    """Prompt Gemini for JSON matching `schema_model`, rotating keys on 429s."""
    model = model or GEMINI_FLASH_MODEL
    # Injecting the schema directly into the prompt to bypass SDK schema conversion bugs
    schema_json = json.dumps(schema_model.model_json_schema(), indent=2)
    full_prompt = (
        f"{prompt}\n\n"
        "IMPORTANT: You must return valid JSON that strictly conforms to the following JSON schema:\n"
        f"{schema_json}\n"
        "Return ONLY the JSON. No explanations, no markdown blocks."
    )

    while True:
        if not client_manager.client:
            print("[Gemini] No active client available.")
            return None

        is_rate_limited = False
        limit_error = ""

        for attempt in range(retries):
            try:
                response = client_manager.client.models.generate_content(
                    model=model,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(temperature=0.1),
                )

                if not response or not response.text:
                    continue

                return schema_model.model_validate_json(repair_json(response.text))
            except Exception as e:
                err_str = str(e)
                print(f"Gemini API failure (attempt {attempt+1}): {err_str}")

                # Rate limits and bad credentials are key problems, not prompt
                # problems: stop retrying this key and let rotation handle it.
                if is_rate_limit_error(err_str) or is_dead_key_error(err_str):
                    is_rate_limited = True
                    limit_error = err_str
                    break
                time.sleep(2 * (attempt + 1))

        if is_rate_limited:
            client_manager.mark_failed(limit_error)
            # Raises only when no key can recover; callers treat that as fatal.
            client_manager.rotate_key()
            continue  # Retry the same prompt with the new key

        # Non-rate-limit errors exhausted all retries
        return None


def call_gemini_text(prompt: str, model: str | None = None,
                     temperature: float = 0.3, fallback: str = "") -> str:
    """Free-text Gemini call with the same rotation behaviour as the JSON path."""
    model = model or GEMINI_FLASH_MODEL
    while True:
        if not client_manager.client:
            return fallback
        try:
            response = client_manager.client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
            return response.text if response and response.text else fallback
        except Exception as e:
            err_str = str(e)
            print(f"Gemini text call error: {err_str}")
            if is_rate_limit_error(err_str) or is_dead_key_error(err_str):
                client_manager.mark_failed(err_str)
                try:
                    client_manager.rotate_key()
                except RuntimeError:
                    return fallback
                continue
            return fallback
