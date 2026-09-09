"""
Renders and dispatches new postings to the internship Telegram channel.

Delivery is per-posting and paced. The Simplify tracker alone adds ~98 active
roles a day and can land them in one push, so an uncapped loop would both flood
the channel and trip Telegram's ~20 messages/minute limit. Postings are ranked,
the top `max_messages_per_run` are sent individually, and the remainder are
rolled into a single summary rather than being dropped silently.
"""
from shared.telegram import esc

# Sponsorship values worth surfacing. "Other" and "Not Specified" carry no
# information and would just add a line to every message.
_NOTABLE_SPONSORSHIP = {
    "does not offer sponsorship",
    "offers sponsorship",
    "u.s. citizenship is required",
    "u.s. citizenship required",
}


def rank(records: list[dict]) -> list[dict]:
    """Most relevant first, then most recently posted."""
    return sorted(
        records,
        key=lambda r: (-float(r.get("relevance", 5.0)), -int(r.get("date_posted", 0))),
    )


def select(records: list[dict], max_messages: int,
           guarantee_per_domain: int = 0) -> tuple[list[dict], list[dict]]:
    """
    Choose which postings get an individual message.

    Pure relevance ranking lets one domain take every slot. A batch of nine
    Airbus software roles scoring 7.5-10.0 buries a scholarship at 2.0 in the
    overflow summary every single time, so entire categories become invisible.

    `guarantee_per_domain` reserves that many slots for each domain present,
    filling the rest by relevance. Guarantees are awarded to the strongest
    domains first, so a cap smaller than the domain count still spends its
    slots on the most relevant categories rather than alphabetical luck.
    """
    ranked = rank(records)
    if guarantee_per_domain <= 0 or max_messages <= 0:
        return ranked[:max_messages], ranked[max_messages:]

    by_domain: dict[str, list[dict]] = {}
    for record in ranked:
        by_domain.setdefault(record.get("domain") or "Other", []).append(record)

    # Order domains by their best posting, so the cap favours strong categories.
    domains = sorted(by_domain, key=lambda d: -float(by_domain[d][0].get("relevance", 5.0)))

    chosen: list[dict] = []
    chosen_ids = set()
    for domain in domains:
        for record in by_domain[domain][:guarantee_per_domain]:
            if len(chosen) >= max_messages:
                break
            chosen.append(record)
            chosen_ids.add(id(record))
        if len(chosen) >= max_messages:
            break

    # Fill whatever is left purely by relevance.
    for record in ranked:
        if len(chosen) >= max_messages:
            break
        if id(record) not in chosen_ids:
            chosen.append(record)
            chosen_ids.add(id(record))

    head = rank(chosen)
    tail = [r for r in ranked if id(r) not in chosen_ids]
    return head, tail


def format_posting(record: dict) -> str:
    lines = [f"<b>{esc(record['company'])}</b>", esc(record["title"])]

    meta = []
    if record.get("domain"):
        meta.append(esc(record["domain"]))
    if record.get("season"):
        meta.append(esc(record["season"]))
    if meta:
        lines.append(" · ".join(meta))

    locations = record.get("locations") or []
    if locations:
        shown = ", ".join(locations[:3])
        if len(locations) > 3:
            shown += f" +{len(locations) - 3} more"
        lines.append(esc(shown))

    if record.get("blurb"):
        lines.append("")
        lines.append(esc(record["blurb"]))

    details = []
    if record.get("target_year"):
        years = record["target_year"]
        years = ", ".join(years) if isinstance(years, list) else str(years)
        details.append(f"For: {esc(years)}")
    if record.get("scholarship_amount"):
        details.append(f"Award: {esc(record['scholarship_amount'])}")
    if record.get("deadline"):
        details.append(f"Deadline: {esc(record['deadline'])}")

    sponsorship = (record.get("sponsorship") or "").strip()
    if sponsorship.lower() in _NOTABLE_SPONSORSHIP:
        details.append(esc(sponsorship))

    if details:
        lines.append("")
        lines.extend(details)

    lines.append("")
    if record.get("url"):
        lines.append(f'<a href="{esc(record["url"])}">Apply</a> · via {esc(record["source_name"])}')
    else:
        lines.append(f'via {esc(record["source_name"])}')

    return "\n".join(lines)


def format_overflow(records: list[dict]) -> str:
    """One message covering everything past the per-run send cap."""
    by_domain: dict[str, list[dict]] = {}
    for record in records:
        by_domain.setdefault(record.get("domain") or "Other", []).append(record)

    lines = [f"<b>+{len(records)} more new postings</b>", ""]
    for domain in sorted(by_domain, key=lambda d: -len(by_domain[d])):
        items = by_domain[domain]
        companies = []
        for record in items[:8]:
            companies.append(record["company"])
        shown = ", ".join(dict.fromkeys(companies))
        if len(items) > 8:
            shown += f" +{len(items) - 8} more"
        lines.append(f"<b>{esc(domain)}</b> ({len(items)})")
        lines.append(esc(shown))
        lines.append("")

    lines.append("Full lists are in the tracked repositories.")
    return "\n".join(lines)


def format_seed_notice(counts: dict, health: list[str]) -> str:
    lines = [
        "<b>Internship monitor armed</b>",
        "",
        f"Recorded {counts.get('recorded', 0)} existing postings as the baseline. "
        "Nothing was announced for them.",
        "Only postings added from now on will be sent.",
        "",
        "<b>Sources</b>",
    ]
    lines.extend(esc(line) for line in health)
    return "\n".join(lines)


def dispatch(records: list[dict], sender, max_messages: int = 12,
             overflow_as_summary: bool = True,
             guarantee_per_domain: int = 0) -> dict:
    """Send selected postings, capped, with the remainder summarised."""
    stats = {"sent": 0, "overflow": 0, "failed": 0, "domains_sent": 0}
    if not records:
        return stats

    head, tail = select(records, max_messages, guarantee_per_domain)
    stats["domains_sent"] = len({r.get("domain") or "Other" for r in head})

    for record in head:
        if sender.send(format_posting(record)):
            stats["sent"] += 1
        else:
            stats["failed"] += 1

    if tail:
        stats["overflow"] = len(tail)
        if overflow_as_summary:
            sender.send(format_overflow(tail))

    return stats
