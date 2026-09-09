"""
Internship Opportunity Monitor.

Polls the tracked GitHub repositories, works out which postings are genuinely
new, has Gemini categorise them, and pushes them to Telegram.

    python -m internships.main              # normal run
    python -m internships.main --dry-run    # render everything, send nothing
    python -m internships.main --reseed     # rebuild the baseline, announce nothing
"""
import argparse
import sys
from pathlib import Path

# Allow `python internships/main.py` as well as `python -m internships.main`.
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from shared.config import (  # noqa: E402
    INTERNSHIPS_ARCHIVE_DIR,
    load_internship_sources,
)
from shared.telegram import internships_sender  # noqa: E402
from shared.utils import get_today_str, save_json, utc_now_iso  # noqa: E402

from internships import notify, state as state_mod  # noqa: E402
from internships.dedupe import split_new  # noqa: E402
from internships.normalize import normalize_all, passes_filters  # noqa: E402
from internships.sources import fetch_all  # noqa: E402


def _health_lines(sources: list[dict], results: dict) -> list[str]:
    lines = []
    for source in sources:
        if not source.get("enabled", True):
            continue
        result = results.get(source["key"])
        if result is None:
            lines.append(f"{source['name']}: skipped")
        else:
            lines.append(f"{source['name']}: {result.status} ({result.detail})")
    return lines


def run(dry_run: bool = False, reseed: bool = False, limit: int | None = None) -> dict:
    config = load_internship_sources()
    sources = [s for s in config.get("sources", []) if s.get("enabled", True)]
    filters = config.get("filters", {})
    delivery = config.get("delivery", {})
    classification = config.get("classification", {})

    if not sources:
        print("No enabled sources configured.")
        return {"status": "no_sources"}

    state = state_mod.load_state()
    if reseed:
        print("Reseeding: the baseline will be rebuilt and nothing announced.")
        state["seeded"] = False

    etags_before = dict(state.get("etags", {}))
    results = fetch_all(sources, state.get("etags", {}))

    by_key = {s["key"]: s for s in sources}
    all_normalized: list[dict] = []
    candidates: list[dict] = []

    for key, result in results.items():
        if not result.ok:
            continue
        source = by_key[key]
        normalized = normalize_all(result.records, source)
        all_normalized.extend(normalized)
        candidates.extend(
            r for r in normalized if passes_filters(r, source, filters)
        )
        # Only advance the ETag for a source we actually parsed, so a failed
        # parse re-downloads next run instead of being skipped as unchanged.
        if result.etag:
            state.setdefault("etags", {})[key] = result.etag

    etags_changed = dict(state.get("etags", {})) != etags_before
    health = _health_lines(sources, results)
    fetched_ok = sum(1 for r in results.values() if r.ok)
    unchanged = sum(1 for r in results.values() if r.status == "unchanged")
    print(f"\n[run] sources ok={fetched_ok} unchanged={unchanged} "
          f"errors={len(results) - fetched_ok - unchanged}")
    print(f"[run] {len(all_normalized)} records, {len(candidates)} pass filters")

    sender = internships_sender(dry_run=dry_run,
                                gap_seconds=delivery.get("seconds_between_messages", 3.5))

    # First run: record the world as it is and announce nothing. Without this
    # the bot would treat ~6,000 existing active postings as new.
    if not state.get("seeded"):
        state_mod.record_seen(state, all_normalized)
        state_mod.mark_seeded(state)
        state["runs"] = state.get("runs", 0) + 1
        # A dry run must leave no trace, or "just checking" would silently
        # consume the one chance to seed correctly.
        if not dry_run:
            state_mod.save_state(state)

        print(f"[run] SEEDED with {len(all_normalized)} postings"
              f"{' (dry run, state not saved)' if dry_run else ''}. Nothing announced.")
        if fetched_ok:
            sender.send(notify.format_seed_notice({"recorded": len(all_normalized)}, health))
        return {"status": "seeded", "recorded": len(all_normalized), "health": health}

    seen_uids = set(state.get("seen_uids") or [])
    seen_fingerprints = set(state.get("seen_fingerprints") or [])

    new_records, skip_counts = split_new(
        candidates, seen_uids, seen_fingerprints,
        source_priority=[s["key"] for s in sources],
    )
    print(f"[run] {len(new_records)} new postings "
          f"(skipped: {skip_counts})")

    if not new_records:
        # Persist only when something real moved. save_state always rewrites
        # last_run_at, so saving unconditionally made every 30-minute poll dirty
        # the state file and produce a commit: 48 metadata-only commits a day.
        if etags_changed and not dry_run:
            state["runs"] = state.get("runs", 0) + 1
            state_mod.save_state(state)
            print("[run] Nothing new. Source ETags moved, state refreshed.")
        else:
            print("[run] Nothing new. No message sent, state left untouched.")
        return {"status": "no_new", "health": health, "state_written": etags_changed}

    from internships.classify import classify  # imported late so a run with no
    # new postings never constructs a Gemini client
    class_stats = classify(
        new_records,
        batch_size=classification.get("batch_size", 20),
        max_batches=classification.get("max_batches_per_run", 6),
        enabled=classification.get("enabled", True),
    )
    print(f"[run] classification: {class_stats}")

    min_relevance = classification.get("min_relevance_to_notify", 0)
    to_send = [r for r in new_records if float(r.get("relevance", 5)) >= min_relevance]
    held_back = len(new_records) - len(to_send)

    dispatch_stats = notify.dispatch(
        to_send,
        sender,
        max_messages=limit if limit is not None else delivery.get("max_messages_per_run", 12),
        overflow_as_summary=delivery.get("overflow_as_summary", True),
        guarantee_per_domain=delivery.get("guarantee_per_domain", 0),
    )
    print(f"[run] dispatch: {dispatch_stats} (held back below relevance: {held_back})")

    # Every new posting is recorded, including ones covered by the overflow
    # summary. Recording only what was sent individually would re-announce the
    # remainder on the next run.
    state["runs"] = state.get("runs", 0) + 1
    if not dry_run:
        state_mod.record_seen(state, new_records)
        state["announced_total"] = state.get("announced_total", 0) + len(new_records)
        state_mod.save_state(state)

    archive = {
        "run_at": utc_now_iso(),
        "new_postings": len(new_records),
        "dispatch": dispatch_stats,
        "classification": class_stats,
        "skipped": skip_counts,
        "health": health,
        "postings": new_records,
    }
    if not dry_run:
        save_json(archive, INTERNSHIPS_ARCHIVE_DIR / f"{get_today_str()}.json")

    return {"status": "ok", "new": len(new_records), "dispatch": dispatch_stats,
            "health": health}


def main():
    parser = argparse.ArgumentParser(description="Internship Opportunity Monitor")
    parser.add_argument("--dry-run", action="store_true",
                        help="Render and validate messages without sending them")
    parser.add_argument("--reseed", action="store_true",
                        help="Rebuild the baseline from current listings, announcing nothing")
    parser.add_argument("--limit", type=int, default=None,
                        help="Override the per-run individual message cap")
    args = parser.parse_args()

    result = run(dry_run=args.dry_run, reseed=args.reseed, limit=args.limit)
    print(f"\n[run] finished: {result.get('status')}")


if __name__ == "__main__":
    main()
