"""
Categorises new postings with Gemini, in batches.

One Gemini call per posting would be ~150 requests a day, well past the
free-tier ceiling of 20 per model per day. Postings are therefore batched (20
per call) and the number of batches per run is capped. Anything past the cap,
or any batch the model fails to return, falls back to the tracker's own
category field.
"""
from pydantic import BaseModel, Field

from shared.config import GEMINI_INTERNSHIPS_MODEL
from shared.gemini_client import call_gemini_structured

DOMAINS = [
    "AI/ML",
    "Software Engineering",
    "Data",
    "Hardware",
    "Quant/Finance",
    "Product",
    "Security",
    "Research",
    "Scholarship/Program",
    "Other",
]

# Maps the trackers' own category vocabulary onto ours, for the fallback path.
_SOURCE_CATEGORY_MAP = {
    "ai/ml/data": "AI/ML",
    "data science, ai & machine learning": "AI/ML",
    "software": "Software Engineering",
    "software engineering": "Software Engineering",
    "hardware": "Hardware",
    "hardware engineering": "Hardware",
    "quant": "Quant/Finance",
    "quantitative finance": "Quant/Finance",
    "product": "Product",
    "product management": "Product",
    "scholarship": "Scholarship/Program",
    "program": "Scholarship/Program",
    "research": "Research",
    "internship": "Software Engineering",
}


class RoleClassification(BaseModel):
    index: int = Field(description="The index of the posting from the input list")
    domain: str = Field(description="One domain copied verbatim from the allowed list")
    relevance: float = Field(description="Relevance to a CS student, 0 to 10")
    blurb: str = Field(description="One short factual clause about the role, max 12 words")


class ClassificationResponse(BaseModel):
    items: list[RoleClassification]


def _fallback_domain(record: dict) -> str:
    """Use the tracker's own category when the model is unavailable."""
    raw = (record.get("category") or "").strip().lower()
    if raw in _SOURCE_CATEGORY_MAP:
        return _SOURCE_CATEGORY_MAP[raw]

    opp = (record.get("opportunity_type") or "").strip().lower()
    if opp:
        for key, mapped in _SOURCE_CATEGORY_MAP.items():
            if key in opp:
                return mapped

    title = record.get("title", "").lower()
    for needle, domain in (
        ("machine learning", "AI/ML"), ("ai ", "AI/ML"), ("data", "Data"),
        ("quant", "Quant/Finance"), ("trading", "Quant/Finance"),
        ("hardware", "Hardware"), ("asic", "Hardware"), ("firmware", "Hardware"),
        ("security", "Security"), ("product man", "Product"),
        ("research", "Research"), ("scholarship", "Scholarship/Program"),
        ("software", "Software Engineering"), ("engineer", "Software Engineering"),
    ):
        if needle in title:
            return domain
    return "Other"


def _apply_fallback(records: list[dict]) -> None:
    for record in records:
        record.setdefault("domain", _fallback_domain(record))
        record.setdefault("relevance", 5.0)
        record.setdefault("blurb", "")
        record.setdefault("classified_by", "fallback")


def _batch_prompt(batch: list[dict]) -> str:
    lines = []
    for i, record in enumerate(batch):
        locations = ", ".join(record.get("locations") or []) or "Unspecified"
        extra = record.get("category") or record.get("opportunity_type") or ""
        lines.append(
            f"{i}. company={record['company']} | title={record['title']} | "
            f"locations={locations} | season={record.get('season') or 'Unspecified'}"
            + (f" | source_category={extra}" if extra else "")
        )

    return f"""
You are categorising newly posted internship and early-career opportunities for
a computer science student.

For each posting below, return one object with:
- index: the posting's number, exactly as given
- domain: copied verbatim from this list, no additions or slashes of your own:
  {", ".join(DOMAINS)}
- relevance: 0 to 10, how relevant this is to a CS student looking for
  technical experience. A core software, AI or data role scores high. A
  non-technical or unrelated role scores low. Do not cluster everything at 7.
- blurb: at most 12 words stating one concrete fact about the role that is not
  already the company or the title. If the title says everything, return an
  empty string rather than restating it.

Do not invent postings. Return exactly {len(batch)} items, one per index.

Postings:
{chr(10).join(lines)}
"""


def classify(records: list[dict], batch_size: int = 20, max_batches: int = 6,
             enabled: bool = True) -> dict:
    """
    Annotate records in place with `domain`, `relevance` and `blurb`.

    Returns per-run stats. Never raises for model problems: an unclassified
    posting is still worth announcing, so failure downgrades to the fallback
    rather than dropping the posting.
    """
    stats = {"classified": 0, "fallback": 0, "batches": 0, "batches_failed": 0}

    if not records:
        return stats

    if not enabled:
        _apply_fallback(records)
        stats["fallback"] = len(records)
        return stats

    limit = batch_size * max_batches
    to_classify, overflow = records[:limit], records[limit:]

    for start in range(0, len(to_classify), batch_size):
        batch = to_classify[start:start + batch_size]
        stats["batches"] += 1
        try:
            response = call_gemini_structured(
                _batch_prompt(batch), ClassificationResponse,
                model=GEMINI_INTERNSHIPS_MODEL,
            )
        except RuntimeError as e:
            # Keys fully exhausted. Stop calling and fall back for the rest.
            print(f"[classify] Gemini unavailable, using fallback: {e}")
            response = None
            _apply_fallback(to_classify[start:])
            stats["batches_failed"] += 1
            break

        if not response or not response.items:
            stats["batches_failed"] += 1
            _apply_fallback(batch)
            continue

        for item in response.items:
            if not 0 <= item.index < len(batch):
                continue
            record = batch[item.index]
            domain = item.domain.strip()
            record["domain"] = domain if domain in DOMAINS else _fallback_domain(record)
            record["relevance"] = max(0.0, min(10.0, float(item.relevance)))
            record["blurb"] = item.blurb.strip()
            record["classified_by"] = "gemini"

        # Anything the model skipped still needs values.
        _apply_fallback(batch)

    if overflow:
        print(f"[classify] {len(overflow)} postings past the batch cap, using fallback")
        _apply_fallback(overflow)

    stats["classified"] = sum(1 for r in records if r.get("classified_by") == "gemini")
    stats["fallback"] = len(records) - stats["classified"]
    return stats
