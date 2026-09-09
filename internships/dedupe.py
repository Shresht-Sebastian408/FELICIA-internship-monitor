"""
Decides which of the fetched postings are genuinely new.

Two passes are needed because the trackers disagree about identity:

1. `uid` (source key + tracker id) catches the same posting seen on a previous
   run, and the 236 ids the two Simplify feeds share outright.
2. A normalised company+title fingerprint catches the same role carried by
   repos with unrelated id spaces, which is how vanshb03 overlaps Simplify.

Newness is decided by identity, never by `date_posted`. The underclassmen
tracker adds entries carrying months-old posted dates, and a date-based rule
would drop every one of them.
"""


def split_new(candidates: list[dict], seen_uids: set, seen_fingerprints: set,
              source_priority: list[str] | None = None) -> tuple[list[dict], dict]:
    """
    Partition candidates into genuinely new postings and skip counts.

    When one role arrives from several trackers in the same run, the copy from
    the earliest source in `source_priority` wins so the announcement links to
    the most reliable tracker.
    """
    priority = {key: i for i, key in enumerate(source_priority or [])}

    ordered = sorted(
        candidates,
        key=lambda r: (priority.get(r["source_key"], len(priority)), -r.get("date_posted", 0)),
    )

    new_records: list[dict] = []
    batch_uids: set = set()
    batch_fingerprints: set = set()
    counts = {"seen_before": 0, "duplicate_in_run": 0, "cross_source": 0}

    for record in ordered:
        uid = record["uid"]
        fp = record["fingerprint"]

        if uid in seen_uids or fp in seen_fingerprints:
            counts["seen_before"] += 1
            continue
        if uid in batch_uids:
            counts["duplicate_in_run"] += 1
            continue
        if fp in batch_fingerprints:
            # Same role, different tracker, same run.
            counts["cross_source"] += 1
            continue

        batch_uids.add(uid)
        batch_fingerprints.add(fp)
        new_records.append(record)

    return new_records, counts
