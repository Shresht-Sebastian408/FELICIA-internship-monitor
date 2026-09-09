"""
Folds the three repositories' slightly different record shapes into one schema.

The trackers agree on the core fields (id, company_name, title, url, locations,
active, date_posted, sponsorship) and diverge on the rest: the Simplify feeds
carry `category`, `degrees` and `terms`; vanshb03 carries `season`; the
underclassmen tracker adds `opportunity_type`, `target_year`, `deadline` and
`scholarship_amount`. Everything optional is preserved when present and simply
absent otherwise, so no source loses detail on the way through.
"""
import hashlib
import time

from shared.utils import normalize_text

# Fields that are copied straight through when the source provides them.
_OPTIONAL_PASSTHROUGH = (
    "opportunity_type",
    "target_year",
    "deadline",
    "scholarship_amount",
    "field",
    "degrees",
)


def fingerprint(company: str, title: str) -> str:
    """
    Content key used to spot the same role appearing in more than one tracker.

    The Simplify and vanshb03 repos assign unrelated ids to the same posting,
    so id matching alone re-announces roughly 59 duplicate roles.
    """
    return f"{normalize_text(company)}|{normalize_text(title)}"


def _season(record: dict) -> str:
    terms = record.get("terms")
    if isinstance(terms, list) and terms:
        return str(terms[0])
    if terms:
        return str(terms)
    return str(record.get("season") or "")


def _locations(record: dict) -> list[str]:
    locs = record.get("locations")
    if isinstance(locs, list):
        return [str(x) for x in locs if x]
    if locs:
        return [str(locs)]
    return []


def _timestamp(value) -> int:
    try:
        ts = int(value or 0)
    except (TypeError, ValueError):
        return 0
    # A couple of trackers have emitted milliseconds. Anything far beyond now
    # is treated as ms and scaled back.
    if ts > 10_000_000_000:
        ts //= 1000
    return ts


def normalize_record(record: dict, source: dict) -> dict | None:
    """Convert one raw tracker record into the common shape, or None if unusable."""
    company = str(record.get("company_name") or record.get("company") or "").strip()
    title = str(record.get("title") or record.get("role") or "").strip()
    if not company or not title:
        return None

    raw_id = str(record.get("id") or "").strip()
    fp = fingerprint(company, title)
    if not raw_id:
        # The README adapter has no ids, so derive a stable one from content.
        raw_id = hashlib.sha1(fp.encode("utf-8")).hexdigest()[:16]

    normalized = {
        "uid": f"{source['key']}:{raw_id}",
        "source_key": source["key"],
        "source_name": source.get("name", source["key"]),
        "repo": source.get("repo", ""),
        "id": raw_id,
        "company": company,
        "title": title,
        "url": str(record.get("url") or "").strip(),
        "company_url": str(record.get("company_url") or "").strip(),
        "locations": _locations(record),
        "season": _season(record),
        "category": str(record.get("category") or "").strip(),
        "sponsorship": str(record.get("sponsorship") or "").strip(),
        "active": bool(record.get("active", True)),
        "is_visible": bool(record.get("is_visible", True)),
        "date_posted": _timestamp(record.get("date_posted")),
        "date_updated": _timestamp(record.get("date_updated")),
        "fingerprint": fp,
    }

    for field in _OPTIONAL_PASSTHROUGH:
        if record.get(field):
            normalized[field] = record[field]

    return normalized


def normalize_all(records: list[dict], source: dict) -> list[dict]:
    out = []
    for record in records:
        normalized = normalize_record(record, source)
        if normalized:
            out.append(normalized)
    return out


def passes_filters(record: dict, source: dict, filters: dict, now: float | None = None) -> bool:
    """
    Apply the configured filters to one normalised record.

    `max_age_days` is deliberately per-source. The underclassmen tracker carries
    months-old `date_posted` values on entries that were only just added, so a
    global age filter would silence that repo entirely; its config sets the
    guard to null.
    """
    if filters.get("require_active", True) and not record["active"]:
        return False
    if filters.get("require_visible", True) and not record["is_visible"]:
        return False

    max_age_days = source.get("max_age_days")
    if max_age_days:
        now = time.time() if now is None else now
        posted = record.get("date_posted") or 0
        if posted and posted < now - (max_age_days * 86400):
            return False

    return True
