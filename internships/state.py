"""
Persistent run state for the internship monitor: what has been announced, and
the ETag of each source's last download.

The `seeded` flag is the guard that makes the first run safe. On a cold start
there are ~6,000 active postings across the three trackers; without seeding,
the bot would read every one of them as new and try to send 6,000 messages.
The first run therefore records identities and announces nothing.
"""
from shared.config import INTERNSHIPS_STATE_FILE
from shared.utils import load_json, save_json, utc_now_iso

STATE_VERSION = 1


def empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "seeded": False,
        "seeded_at": None,
        "last_run_at": None,
        "runs": 0,
        "announced_total": 0,
        "etags": {},
        "seen_uids": [],
        "seen_fingerprints": [],
    }


def load_state(path=None) -> dict:
    path = path or INTERNSHIPS_STATE_FILE
    state = load_json(path, default=None)
    if not isinstance(state, dict) or not state:
        return empty_state()

    base = empty_state()
    base.update(state)
    # Lists are stored on disk for compactness and readability; callers work
    # with sets.
    base["seen_uids"] = list(base.get("seen_uids") or [])
    base["seen_fingerprints"] = list(base.get("seen_fingerprints") or [])
    base["etags"] = dict(base.get("etags") or {})
    return base


def save_state(state: dict, path=None) -> None:
    path = path or INTERNSHIPS_STATE_FILE
    payload = dict(state)
    payload["version"] = STATE_VERSION
    payload["last_run_at"] = utc_now_iso()
    payload["seen_uids"] = sorted(set(payload.get("seen_uids") or []))
    payload["seen_fingerprints"] = sorted(set(payload.get("seen_fingerprints") or []))
    save_json(payload, path)


def record_seen(state: dict, records: list[dict]) -> None:
    """Mark records as known so a later run does not announce them again."""
    uids = set(state.get("seen_uids") or [])
    fps = set(state.get("seen_fingerprints") or [])
    for record in records:
        uids.add(record["uid"])
        fps.add(record["fingerprint"])
    state["seen_uids"] = sorted(uids)
    state["seen_fingerprints"] = sorted(fps)


def mark_seeded(state: dict) -> None:
    state["seeded"] = True
    state["seeded_at"] = utc_now_iso()
