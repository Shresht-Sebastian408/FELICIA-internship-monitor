import json
import unittest.mock
import sys
import types
import unittest
from pathlib import Path

_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _install_fake_sdk():
    """Stub the Gemini SDK so these tests never touch the network."""
    if "google.genai" in sys.modules:
        return
    google = types.ModuleType("google")
    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")

    class FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.models = types.SimpleNamespace(generate_content=lambda **kw: None)

    genai.Client = FakeClient
    genai_types.GenerateContentConfig = lambda **kw: None
    google.genai = genai
    sys.modules["google"] = google
    sys.modules["google.genai"] = genai
    sys.modules["google.genai.types"] = genai_types


_install_fake_sdk()

from internships import notify  # noqa: E402
from internships.dedupe import split_new  # noqa: E402
from internships.normalize import (  # noqa: E402
    fingerprint,
    normalize_all,
    normalize_record,
    passes_filters,
)
from internships.sources import _parse_readme_table, fetch_source  # noqa: E402
from shared.telegram import TelegramSender, esc  # noqa: E402

SIMPLIFY = {"key": "simplify_2027", "name": "SimplifyJobs Summer 2027",
            "repo": "SimplifyJobs/Summer2027-Internships", "branch": "dev",
            "path": ".github/scripts/listings.json", "adapter": "listings_json",
            "max_age_days": 21}
VANSH = {"key": "vansh_2027", "name": "vanshb03 Summer 2027",
         "repo": "vanshb03/Summer2027-Internships", "branch": "dev",
         "path": ".github/scripts/listings.json", "adapter": "listings_json",
         "max_age_days": 21}
UNDER = {"key": "underclassmen", "name": "Underclassmen Opportunities",
         "repo": "Jose-Gael-Cruz-Lopez/underclassmen-opportunities", "branch": "main",
         "path": ".github/scripts/listings.json", "adapter": "listings_json",
         "max_age_days": None}


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload if payload is not None else [])
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}})
        return self.response

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return self.response


class TestNormalize(unittest.TestCase):
    def test_simplify_record_uses_terms_for_season(self):
        raw = {"id": "abc", "company_name": "Arrowhead", "title": "AI Intern",
               "terms": ["Summer 2026"], "locations": ["Madison, WI"],
               "category": "AI/ML/Data", "active": True, "is_visible": True,
               "date_posted": 1768203424, "sponsorship": "Other",
               "degrees": ["Bachelor's"], "url": "https://x.test/1"}
        r = normalize_record(raw, SIMPLIFY)
        self.assertEqual(r["season"], "Summer 2026")
        self.assertEqual(r["uid"], "simplify_2027:abc")
        self.assertEqual(r["degrees"], ["Bachelor's"])

    def test_vansh_record_uses_season_field(self):
        raw = {"id": "df70", "company_name": "Rippling", "title": "Frontend Intern",
               "season": "Winter", "locations": ["New York, NY"], "active": True,
               "is_visible": True, "date_posted": 1749260792, "url": "https://x.test/2"}
        r = normalize_record(raw, VANSH)
        self.assertEqual(r["season"], "Winter")
        self.assertNotIn("degrees", r)

    def test_underclassmen_extra_fields_are_preserved(self):
        raw = {"id": "0dc4", "company_name": "American Express",
               "title": "Campus Opportunities", "season": "Multiple",
               "category": "Program", "opportunity_type": "Campus Opportunities",
               "target_year": ["Freshman (1st year)"], "scholarship_amount": "$5,000",
               "deadline": "2026-03-01", "active": True, "is_visible": True,
               "date_posted": 1770212009, "url": "https://x.test/3"}
        r = normalize_record(raw, UNDER)
        self.assertEqual(r["target_year"], ["Freshman (1st year)"])
        self.assertEqual(r["scholarship_amount"], "$5,000")
        self.assertEqual(r["deadline"], "2026-03-01")

    def test_record_without_company_or_title_is_dropped(self):
        self.assertIsNone(normalize_record({"id": "x", "title": "Intern"}, SIMPLIFY))
        self.assertIsNone(normalize_record({"id": "x", "company_name": "Acme"}, SIMPLIFY))

    def test_millisecond_timestamps_are_scaled(self):
        raw = {"id": "a", "company_name": "A", "title": "B", "date_posted": 1768203424000}
        self.assertEqual(normalize_record(raw, SIMPLIFY)["date_posted"], 1768203424)

    def test_missing_id_is_derived_from_content(self):
        raw = {"company_name": "Acme", "title": "SWE Intern"}
        first = normalize_record(raw, SIMPLIFY)
        second = normalize_record(dict(raw), SIMPLIFY)
        self.assertTrue(first["id"])
        self.assertEqual(first["uid"], second["uid"], "derived ids must be stable")


class TestFilters(unittest.TestCase):
    def _record(self, **kw):
        base = {"active": True, "is_visible": True, "date_posted": 1_000_000}
        base.update(kw)
        return base

    def test_inactive_is_filtered(self):
        filters = {"require_active": True, "require_visible": True}
        self.assertFalse(passes_filters(self._record(active=False), SIMPLIFY, filters))

    def test_age_guard_drops_old_posting(self):
        filters = {"require_active": True, "require_visible": True}
        now = 1_000_000 + (30 * 86400)
        self.assertFalse(passes_filters(self._record(), SIMPLIFY, filters, now=now))

    def test_source_without_age_guard_keeps_old_posting(self):
        # The underclassmen tracker adds entries carrying months-old dates. A
        # global age filter would silence the whole repo.
        filters = {"require_active": True, "require_visible": True}
        now = 1_000_000 + (300 * 86400)
        self.assertTrue(passes_filters(self._record(), UNDER, filters, now=now))


class TestDedupe(unittest.TestCase):
    def _rec(self, source, rid, company, title, posted=0):
        return {"uid": f"{source}:{rid}", "source_key": source, "id": rid,
                "company": company, "title": title, "date_posted": posted,
                "fingerprint": fingerprint(company, title)}

    def test_previously_seen_uid_is_skipped(self):
        rec = self._rec("simplify_2027", "a1", "Stripe", "SWE Intern")
        new, counts = split_new([rec], {"simplify_2027:a1"}, set())
        self.assertEqual(new, [])
        self.assertEqual(counts["seen_before"], 1)

    def test_same_role_from_two_trackers_is_sent_once(self):
        # Simplify and vanshb03 use unrelated id spaces for the same posting.
        a = self._rec("simplify_2027", "a1", "Stripe", "SWE Intern")
        b = self._rec("vansh_2027", "zz9", "Stripe", "SWE Intern")
        new, counts = split_new([a, b], set(), set(),
                                source_priority=["simplify_2027", "vansh_2027"])
        self.assertEqual(len(new), 1)
        self.assertEqual(counts["cross_source"], 1)

    def test_source_priority_decides_which_copy_wins(self):
        a = self._rec("simplify_2027", "a1", "Stripe", "SWE Intern")
        b = self._rec("vansh_2027", "zz9", "Stripe", "SWE Intern")
        new, _ = split_new([b, a], set(), set(),
                           source_priority=["simplify_2027", "vansh_2027"])
        self.assertEqual(new[0]["source_key"], "simplify_2027")

    def test_fingerprint_ignores_case_and_punctuation(self):
        self.assertEqual(fingerprint("AT&T", "SWE Intern"), fingerprint("at t", "swe  intern"))

    def test_previously_seen_fingerprint_blocks_a_new_uid(self):
        rec = self._rec("vansh_2027", "new-id", "Stripe", "SWE Intern")
        new, counts = split_new([rec], set(), {fingerprint("Stripe", "SWE Intern")})
        self.assertEqual(new, [])
        self.assertEqual(counts["seen_before"], 1)

    def test_exact_duplicate_within_one_run(self):
        rec = self._rec("simplify_2027", "a1", "Stripe", "SWE Intern")
        new, counts = split_new([rec, dict(rec)], set(), set())
        self.assertEqual(len(new), 1)
        self.assertEqual(counts["duplicate_in_run"], 1)


class TestFetch(unittest.TestCase):
    def test_304_returns_unchanged_and_no_records(self):
        session = FakeSession(FakeResponse(status_code=304))
        result = fetch_source(SIMPLIFY, etag='W/"abc"', session=session)
        self.assertEqual(result.status, "unchanged")
        self.assertEqual(result.records, [])
        self.assertEqual(session.calls[0]["headers"]["If-None-Match"], 'W/"abc"')

    def test_200_returns_records_and_new_etag(self):
        payload = [{"id": "1", "company_name": "A", "title": "B"}]
        session = FakeSession(FakeResponse(200, payload, headers={"ETag": 'W/"new"'}))
        result = fetch_source(SIMPLIFY, session=session)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.etag, 'W/"new"')
        self.assertEqual(len(result.records), 1)

    def test_http_error_is_reported_not_raised(self):
        session = FakeSession(FakeResponse(status_code=500))
        result = fetch_source(SIMPLIFY, session=session)
        self.assertEqual(result.status, "error")
        self.assertIn("500", result.detail)

    def test_non_array_payload_is_an_error(self):
        session = FakeSession(FakeResponse(200, {"not": "a list"}))
        result = fetch_source(SIMPLIFY, session=session)
        self.assertEqual(result.status, "error")

    def test_readme_table_adapter_parses_rows(self):
        md = """
| Company | Role | Location | Application | Age |
| --- | --- | --- | --- | --- |
| **[Stripe](https://stripe.com)** | Software Engineer Intern | NYC | [Apply](https://apply.test/1) | 1d |
| ↳ | Data Intern | SF | [Apply](https://apply.test/2) | 2d |
"""
        rows = _parse_readme_table(md)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["company_name"], "Stripe")
        self.assertEqual(rows[0]["url"], "https://apply.test/1")
        # The repeat marker inherits the company above it.
        self.assertEqual(rows[1]["company_name"], "Stripe")


class TestNotifyFormatting(unittest.TestCase):
    def _record(self, **kw):
        base = {"company": "Stripe", "title": "SWE Intern", "url": "https://apply.test/1",
                "locations": ["NYC"], "season": "Summer 2027", "domain": "Software Engineering",
                "source_name": "SimplifyJobs Summer 2027", "sponsorship": "Other",
                "relevance": 8.0, "blurb": "", "date_posted": 1}
        base.update(kw)
        return base

    def test_ampersand_in_company_is_escaped(self):
        # Unescaped, Telegram rejects the message with a 400 and the posting is lost.
        msg = notify.format_posting(self._record(company="AT&T"))
        self.assertIn("AT&amp;T", msg)
        self.assertNotIn("AT&T", msg)

    def test_angle_brackets_in_title_are_escaped(self):
        msg = notify.format_posting(self._record(title="<Senior> Intern"))
        self.assertIn("&lt;Senior&gt;", msg)

    def test_uninformative_sponsorship_is_omitted(self):
        msg = notify.format_posting(self._record(sponsorship="Other"))
        self.assertNotIn("Other", msg)

    def test_notable_sponsorship_is_shown(self):
        msg = notify.format_posting(self._record(sponsorship="Does Not Offer Sponsorship"))
        self.assertIn("Does Not Offer Sponsorship", msg)

    def test_underclassmen_fields_are_rendered(self):
        msg = notify.format_posting(self._record(
            target_year=["Freshman (1st year)"], scholarship_amount="$5,000",
            deadline="2026-03-01"))
        self.assertIn("Freshman", msg)
        self.assertIn("$5,000", msg)
        self.assertIn("2026-03-01", msg)

    def test_extra_locations_are_summarised(self):
        msg = notify.format_posting(self._record(
            locations=["A", "B", "C", "D", "E"]))
        self.assertIn("+2 more", msg)

    def test_ranking_puts_most_relevant_first(self):
        low = self._record(company="Low", relevance=2.0)
        high = self._record(company="High", relevance=9.0)
        self.assertEqual(notify.rank([low, high])[0]["company"], "High")


class TestDispatch(unittest.TestCase):
    def _sender(self):
        return TelegramSender("token", "chat", dry_run=True, sleep=lambda s: None)

    def _records(self, n):
        return [{"company": f"C{i}", "title": f"Role {i}", "url": "https://x.test",
                 "locations": [], "season": "", "domain": "Software Engineering",
                 "source_name": "src", "sponsorship": "", "relevance": float(i),
                 "blurb": "", "date_posted": i} for i in range(n)]

    def test_cap_is_respected_and_overflow_summarised(self):
        sender = self._sender()
        stats = notify.dispatch(self._records(20), sender, max_messages=5)
        self.assertEqual(stats["sent"], 5)
        self.assertEqual(stats["overflow"], 15)
        # 5 individual messages plus one summary
        self.assertEqual(len(sender.rendered), 6)
        self.assertIn("+15 more new postings", sender.rendered[-1])

    def test_no_overflow_message_when_under_cap(self):
        sender = self._sender()
        stats = notify.dispatch(self._records(3), sender, max_messages=10)
        self.assertEqual(stats["sent"], 3)
        self.assertEqual(stats["overflow"], 0)
        self.assertEqual(len(sender.rendered), 3)

    def test_empty_input_sends_nothing(self):
        sender = self._sender()
        stats = notify.dispatch([], sender)
        self.assertEqual(stats["sent"], 0)
        self.assertEqual(sender.rendered, [])

    def test_every_message_is_within_telegram_limit(self):
        sender = self._sender()
        notify.dispatch(self._records(30), sender, max_messages=10)
        for msg in sender.rendered:
            self.assertLessEqual(len(msg), 4096)


class TestTelegramSender(unittest.TestCase):
    def test_flood_limit_is_retried_with_the_given_delay(self):
        slept = []
        limited = FakeResponse(status_code=429, payload={"parameters": {"retry_after": 7}})
        session = FakeSession(limited)
        sender = TelegramSender("t", "c", sleep=slept.append, session=session)
        sender.send("hi")
        self.assertIn(7.0, slept)

    def test_send_is_skipped_when_unconfigured(self):
        sender = TelegramSender("", "", sleep=lambda s: None)
        self.assertFalse(sender.enabled)
        self.assertFalse(sender.send("hi"))

    def test_placeholder_credentials_count_as_unconfigured(self):
        sender = TelegramSender("your_telegram_bot_token_here",
                                "your_telegram_chat_id_here", sleep=lambda s: None)
        self.assertFalse(sender.enabled)

    def test_oversized_message_is_truncated(self):
        sender = TelegramSender("t", "c", dry_run=True, sleep=lambda s: None)
        sender.send("x" * 9000)
        self.assertLessEqual(len(sender.rendered[0]), 4096)

    def test_esc_handles_none(self):
        self.assertEqual(esc(None), "")


if __name__ == "__main__":
    unittest.main()


class TestModelConfiguration(unittest.TestCase):
    """
    Model ids expire. gemini-2.5-flash still appears in the models listing but
    returns 404 "no longer available to new users" for newer keys, and every
    Pro model is metered at zero on the free tier. Both defaults must therefore
    stay overridable and must not be pinned to a retired generation.
    """

    def test_no_source_file_pins_a_retired_model_id(self):
        import glob
        import os

        offenders = []
        for path in glob.glob("shared/*.py") + glob.glob("internships/*.py"):
            with open(path, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.strip()
                    if stripped.startswith("#"):
                        continue
                    if 'model="gemini-2.5' in line or "model='gemini-2.5" in line:
                        offenders.append(f"{os.path.basename(path)}:{lineno}")
        self.assertEqual(offenders, [], f"retired model id pinned at {offenders}")

    def test_call_defaults_come_from_config(self):
        from shared import config
        self.assertTrue(config.GEMINI_FLASH_MODEL)
        self.assertTrue(config.GEMINI_INTERNSHIPS_MODEL)

    def test_classification_default_is_not_a_pro_model(self):
        # Pro models are metered at zero on the free tier
        # ("limit: 0, model: gemini-3.1-pro"), so a Pro default would make
        # classification fail for anyone without billing enabled.
        from shared import config
        self.assertNotIn("pro", config.GEMINI_INTERNSHIPS_MODEL.lower())


class TestDomainGuarantee(unittest.TestCase):
    """
    The live run that motivated this sent five Airbus software roles (7.5-10.0)
    and buried every scholarship (2.0-4.0) in the overflow summary.
    """

    def _rec(self, company, domain, relevance):
        return {"company": company, "title": f"{company} role", "url": "https://x.test",
                "locations": [], "season": "", "domain": domain, "source_name": "src",
                "sponsorship": "", "relevance": relevance, "blurb": "", "date_posted": 0}

    def _airbus_scenario(self):
        # Nine high-scoring software roles, plus weaker roles in other domains.
        records = [self._rec(f"Airbus {i}", "Software Engineering", 10.0 - i * 0.25)
                   for i in range(9)]
        records.append(self._rec("Xcel Energy", "Data", 5.0))
        records.append(self._rec("GirlsWhoML", "Scholarship/Program", 4.0))
        records.append(self._rec("MPOWER", "Scholarship/Program", 2.0))
        return records

    def test_without_guarantee_one_domain_takes_every_slot(self):
        head, _ = notify.select(self._airbus_scenario(), max_messages=5,
                                guarantee_per_domain=0)
        self.assertEqual({r["domain"] for r in head}, {"Software Engineering"})

    def test_guarantee_gives_each_domain_a_slot(self):
        head, tail = notify.select(self._airbus_scenario(), max_messages=5,
                                   guarantee_per_domain=1)
        self.assertEqual(len(head), 5)
        self.assertEqual({r["domain"] for r in head},
                         {"Software Engineering", "Data", "Scholarship/Program"})
        # Nothing is lost: everything not sent individually is still summarised.
        self.assertEqual(len(head) + len(tail), 12)

    def test_guaranteed_slot_goes_to_the_domains_best_posting(self):
        head, _ = notify.select(self._airbus_scenario(), max_messages=5,
                                guarantee_per_domain=1)
        scholarships = [r for r in head if r["domain"] == "Scholarship/Program"]
        self.assertEqual(len(scholarships), 1)
        # 4.0 (GirlsWhoML), not 2.0 (MPOWER)
        self.assertEqual(scholarships[0]["company"], "GirlsWhoML")

    def test_remaining_slots_still_go_by_relevance(self):
        head, _ = notify.select(self._airbus_scenario(), max_messages=5,
                                guarantee_per_domain=1)
        software = [r for r in head if r["domain"] == "Software Engineering"]
        # Three domains take one slot each, leaving two for the top software roles.
        self.assertEqual(len(software), 3)
        self.assertEqual(software[0]["company"], "Airbus 0")

    def test_cap_smaller_than_domain_count_favours_strongest_domains(self):
        head, _ = notify.select(self._airbus_scenario(), max_messages=2,
                                guarantee_per_domain=1)
        self.assertEqual(len(head), 2)
        self.assertEqual({r["domain"] for r in head}, {"Software Engineering", "Data"})

    def test_head_is_still_ordered_by_relevance(self):
        head, _ = notify.select(self._airbus_scenario(), max_messages=5,
                                guarantee_per_domain=1)
        scores = [r["relevance"] for r in head]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_duplicates_between_head_and_tail(self):
        head, tail = notify.select(self._airbus_scenario(), max_messages=5,
                                   guarantee_per_domain=1)
        self.assertEqual(len({id(r) for r in head} & {id(r) for r in tail}), 0)

    def test_guarantee_of_two_per_domain(self):
        head, _ = notify.select(self._airbus_scenario(), max_messages=6,
                                guarantee_per_domain=2)
        counts = {}
        for r in head:
            counts[r["domain"]] = counts.get(r["domain"], 0) + 1
        self.assertEqual(counts["Scholarship/Program"], 2)

    def test_dispatch_reports_domain_count(self):
        sender = TelegramSender("t", "c", dry_run=True, sleep=lambda s: None)
        stats = notify.dispatch(self._airbus_scenario(), sender, max_messages=5,
                                guarantee_per_domain=1)
        self.assertEqual(stats["sent"], 5)
        self.assertEqual(stats["domains_sent"], 3)

    def test_fewer_records_than_cap_sends_everything(self):
        records = [self._rec("A", "Data", 5.0), self._rec("B", "AI/ML", 6.0)]
        head, tail = notify.select(records, max_messages=10, guarantee_per_domain=1)
        self.assertEqual(len(head), 2)
        self.assertEqual(tail, [])


class TestNoOpRunsDoNotDirtyState(unittest.TestCase):
    """
    A 30-minute poll that finds nothing must not rewrite state.json.

    save_state always refreshes last_run_at, so saving unconditionally made
    every no-op run dirty the file and produce a commit: 48 metadata-only
    commits a day on the repository.
    """

    def test_state_is_untouched_when_nothing_changed(self):
        import internships.main as im
        from internships.sources import FetchResult

        saved = []
        state = {"seeded": True, "runs": 3, "etags": {"simplify_2027": 'W/"a"'},
                 "seen_uids": [], "seen_fingerprints": [], "announced_total": 0}

        with unittest.mock.patch.object(
                im, "load_internship_sources",
                return_value={"sources": [dict(SIMPLIFY, enabled=True, name="S")],
                              "filters": {}, "delivery": {}, "classification": {}}), \
             unittest.mock.patch.object(im.state_mod, "load_state", return_value=state), \
             unittest.mock.patch.object(im.state_mod, "save_state",
                                        side_effect=lambda s, p=None: saved.append(s)), \
             unittest.mock.patch.object(
                 im, "fetch_all",
                 return_value={"simplify_2027": FetchResult(
                     "simplify_2027", "unchanged", etag='W/"a"', detail="304")}), \
             unittest.mock.patch.object(im, "internships_sender"):
            result = im.run()

        self.assertEqual(result["status"], "no_new")
        self.assertFalse(result["state_written"])
        self.assertEqual(saved, [], "a no-op run must not write state")

    def test_state_is_written_when_an_etag_moves(self):
        import internships.main as im
        from internships.sources import FetchResult

        saved = []
        state = {"seeded": True, "runs": 3, "etags": {"simplify_2027": 'W/"old"'},
                 "seen_uids": [], "seen_fingerprints": [], "announced_total": 0}

        with unittest.mock.patch.object(
                im, "load_internship_sources",
                return_value={"sources": [dict(SIMPLIFY, enabled=True, name="S")],
                              "filters": {}, "delivery": {}, "classification": {}}), \
             unittest.mock.patch.object(im.state_mod, "load_state", return_value=state), \
             unittest.mock.patch.object(im.state_mod, "save_state",
                                        side_effect=lambda s, p=None: saved.append(s)), \
             unittest.mock.patch.object(
                 im, "fetch_all",
                 return_value={"simplify_2027": FetchResult(
                     "simplify_2027", "ok", records=[], etag='W/"new"', detail="0")}), \
             unittest.mock.patch.object(im, "internships_sender"):
            result = im.run()

        self.assertTrue(result["state_written"])
        self.assertEqual(len(saved), 1, "a moved ETag must be persisted")
