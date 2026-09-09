"""
Fetches raw opportunity listings from the tracked GitHub repositories.

Two adapters exist. `listings_json` reads the machine-readable
`.github/scripts/listings.json` that the Simplify-style trackers publish;
`readme_table` parses a markdown table straight out of README.md, so a repo
that only maintains a human-readable table can still be added by config alone.

Fetches are ETag-conditional. The Simplify listing is 12MB, and it changes a
few times a day at most, so an unchanged poll should cost a 304 rather than a
full download.
"""
import re

import requests

RAW_ROOT = "https://raw.githubusercontent.com"
REQUEST_TIMEOUT = 60
USER_AGENT = "internship-monitor-bot/1.0 (+https://github.com)"


def raw_url(source: dict) -> str:
    return f"{RAW_ROOT}/{source['repo']}/{source['branch']}/{source['path']}"


class FetchResult:
    """Outcome of one source fetch, including why nothing came back."""

    def __init__(self, key: str, status: str, records: list | None = None,
                 etag: str | None = None, detail: str = ""):
        self.key = key
        self.status = status  # ok | unchanged | error
        self.records = records or []
        self.etag = etag
        self.detail = detail

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __repr__(self):
        return f"<FetchResult {self.key} {self.status} n={len(self.records)} {self.detail}>"


def _parse_readme_table(text: str) -> list[dict]:
    """
    Parse a markdown table README into listing-shaped dicts.

    Trackers vary, but the column order is conventionally
    Company | Role | Location | Application | Age. Rows using the repeat marker
    (an arrow or blank company cell) inherit the company above them.
    """
    records: list[dict] = []
    last_company = ""

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or line.count("|") < 4:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue

        head = cells[0].lower()
        # Skip the header row and its |---|---| separator
        if head.startswith("company") or set(cells[0]) <= {"-", ":", " "}:
            continue

        def clean(value: str) -> str:
            # Strip markdown links, bold markers and stray HTML
            value = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", value)
            value = re.sub(r"<[^>]+>", " ", value)
            return re.sub(r"[*_`]", "", value).strip()

        def first_link(value: str) -> str:
            m = re.search(r"\((https?://[^)\s]+)\)", value) or re.search(r"(https?://\S+)", value)
            return m.group(1).rstrip(">)") if m else ""

        company = clean(cells[0])
        if company in ("", "↳", "->", "&nbsp;"):
            company = last_company
        else:
            last_company = company

        title = clean(cells[1])
        if not company or not title:
            continue

        location = clean(cells[2]) if len(cells) > 2 else ""
        url = ""
        for cell in cells[3:]:
            url = first_link(cell)
            if url:
                break
        if not url:
            url = first_link(cells[1])

        records.append({
            "id": "",  # synthesised from the fingerprint during normalisation
            "company_name": company,
            "title": title,
            "url": url,
            "locations": [location] if location else [],
            "active": True,
            "is_visible": True,
        })

    return records


def fetch_source(source: dict, etag: str | None = None, session=None) -> FetchResult:
    """Fetch one source, skipping the body when the ETag says nothing changed."""
    session = session or requests
    url = raw_url(source)
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag

    try:
        resp = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except Exception as e:
        return FetchResult(source["key"], "error", detail=f"request failed: {e}")

    if resp.status_code == 304:
        return FetchResult(source["key"], "unchanged", etag=etag,
                           detail="ETag matched, no download")

    if resp.status_code != 200:
        return FetchResult(source["key"], "error",
                           detail=f"HTTP {resp.status_code} for {url}")

    new_etag = resp.headers.get("ETag")
    adapter = source.get("adapter", "listings_json")

    try:
        if adapter == "listings_json":
            records = resp.json()
            if not isinstance(records, list):
                return FetchResult(source["key"], "error",
                                   detail=f"expected a JSON array, got {type(records).__name__}")
        elif adapter == "readme_table":
            records = _parse_readme_table(resp.text)
        else:
            return FetchResult(source["key"], "error", detail=f"unknown adapter {adapter!r}")
    except Exception as e:
        return FetchResult(source["key"], "error", detail=f"parse failed: {e}")

    return FetchResult(source["key"], "ok", records=records, etag=new_etag,
                       detail=f"{len(records)} records")


def fetch_all(sources: list[dict], etags: dict, session=None) -> dict[str, FetchResult]:
    """Fetch every enabled source. One bad repo must not abort the run."""
    results: dict[str, FetchResult] = {}
    for source in sources:
        if not source.get("enabled", True):
            continue
        key = source["key"]
        result = fetch_source(source, etag=etags.get(key), session=session)
        print(f"[fetch] {source['name']}: {result.status} ({result.detail})")
        results[key] = result
    return results
