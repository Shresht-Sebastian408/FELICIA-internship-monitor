# Internship Monitor

A Telegram bot that watches three GitHub internship trackers and posts new
openings as they appear, categorised by an LLM.

The trackers publish tens of thousands of records between them and update in
bursts. The interesting part is not fetching them; it is deciding what counts
as *new* without flooding a channel or missing a whole source.

```
3 repos ──► fetch (ETag) ──► normalise ──► filter ──► diff ──► classify ──► Telegram
             304 = free       one schema   per-source  2 passes   batched     paced
```

---

## Tracked sources

| Repo | Records | Active | New/day |
|---|---|---|---|
| [`SimplifyJobs/Summer2027-Internships`](https://github.com/SimplifyJobs/Summer2027-Internships) | 16,355 | 3,292 | ~98 |
| [`vanshb03/Summer2027-Internships`](https://github.com/vanshb03/Summer2027-Internships) | 471 | 371 | 0 — last posting 2026-08-21 |
| [`Jose-Gael-Cruz-Lopez/underclassmen-opportunities`](https://github.com/Jose-Gael-Cruz-Lopez/underclassmen-opportunities) | 112 | 18 | scholarships and programs, added in batches |

Sources live in `config/internship_sources.json`. Adding or swapping a repo is
a config edit, not a code change. Repos that only keep a markdown table can use
`"adapter": "readme_table"` instead of the JSON adapter.

---

## Four decisions the data forced

Each of these came from looking at the actual feeds, and each one is a bug if
you get it wrong.

### The first run announces nothing

There are 16,938 records and 3,681 active postings on a cold start. A naive
first run reads every one of them as new. The `seeded` flag makes run one
record the world as a baseline and post a single "monitor armed" notice.

```bash
python -m internships.main --reseed   # rebuild the baseline deliberately
```

### Newness is identity, never date

The obvious diff is "posted in the last N days". That silently breaks the
underclassmen tracker, whose newest `date_posted` is 2026-04-19 even though the
repo was updated days ago — it adds entries carrying months-old dates. A global
recency filter drops 100% of that source.

So the diff is identity-based, and `max_age_days` is **per source**, set to
`null` for that repo.

### Dedupe needs two passes

The two Simplify feeds share ids outright. `vanshb03` assigns *unrelated* ids to
postings Simplify also carries — 59 roles overlap by company and title alone.
Matching on id only would announce those twice.

1. `uid` — source key plus tracker id
2. normalised `company|title` fingerprint

### One domain must not take every slot

Ranking purely by relevance sounds right until a batch of nine Airbus software
roles scoring 7.5–10.0 takes all five slots and buries every scholarship at
2.0–4.0. Measured on a real run:

| | Domains covered |
|---|---|
| Pure relevance | **1** |
| `guarantee_per_domain: 1` | **4** |

Same five messages. Nothing is lost either way — everything not sent
individually rolls into an overflow summary.

---

## Cost control

**Fetches are ETag-conditional.** The Simplify listing is 12 MB and changes a
few times a day. An unchanged poll costs a `304` and zero bytes, which is what
makes a 30-minute schedule reasonable.

**Classification is batched.** 20 postings per Gemini call, capped at 6 calls
per run. One call per posting would be ~150 requests a day.

**Free-tier quota is metered per model, not per key:**

```
429 RESOURCE_EXHAUSTED
  GenerateRequestsPerDayPerProjectPerModel-FreeTier
  limit: 20, model: gemini-3.8-flash
```

20 requests per model per day. Classification defaults to
`gemini-flash-lite-latest` — sufficient for short-form categorisation and on its
own quota bucket. Extra keys only help if they come from *different* Google
projects.

**Nothing is written when nothing happened.** A poll that returns three 304s
leaves `state.json` untouched, so the scheduled workflow produces no commit.
Saving unconditionally meant 48 metadata-only commits a day.

**Degradation is graceful.** If Gemini is unavailable or out of quota,
classification falls back to the trackers' own category fields and postings are
still delivered, correctly categorised.

---

## Setup

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Create a bot with [@BotFather](https://t.me/BotFather) (`/newbot`), put the
token in `.env`, then:

```bash
python scripts/telegram_setup.py --check      # validate the token
python scripts/telegram_setup.py --discover   # find the chat id
python scripts/telegram_setup.py --sample     # send formatted samples
```

A bot cannot list its own chats. Before `--discover` finds anything, either
send the bot `/start` in a DM, or add it to a channel as an **admin** with post
rights.

---

## Running

```bash
python -m internships.main              # normal run
python -m internships.main --dry-run    # render and validate, send nothing
python -m internships.main --reseed     # rebuild the baseline
python -m internships.main --limit 3    # override the per-run message cap
```

`--dry-run` exercises fetch, dedupe, classification and rendering with no
credentials and no writes.

### Tuning `config/internship_sources.json`

| Key | Default | Effect |
|---|---|---|
| `delivery.max_messages_per_run` | 12 | Individual messages before the summary takes over |
| `delivery.guarantee_per_domain` | 1 | Reserved slots per domain; `0` for pure relevance |
| `delivery.seconds_between_messages` | 3.5 | Telegram allows ~20/min to one channel |
| `classification.min_relevance_to_notify` | 0 | Raise to 6–7 for only strongly relevant roles |
| `classification.batch_size` | 20 | Postings per Gemini call |
| `sources[].max_age_days` | varies | Per-source age guard; `null` disables |

---

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

51 tests, no network calls — the Gemini SDK is stubbed and HTTP sessions are
faked. Covers schema normalisation for all three repos, per-source age guards,
both dedupe passes, ETag/304 handling, README-table parsing, HTML escaping
(`AT&T`, `<Senior>`), send caps and overflow, flood-limit retry, the domain
guarantee, and that no-op runs leave state untouched.

---

## Deployment

`.github/workflows/internships.yml` runs every 30 minutes, `concurrency`
guarded so runs cannot overlap and re-announce.

Repository secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEYS`.
Settings → Actions → General → **Read and write permissions**, since the run
commits state back.

**Run the workflow manually with `reseed` checked before enabling the
schedule.** Seeding during a cron tick is riskier than doing it deliberately.

---

## Credits

`shared/gemini_client.py` and `shared/json_repair.py` derive from
[Alpha-Forge](https://github.com/Anchitlahkar/Alpha-Forge) by **Anchit Lahkar**,
reused under the MIT licence its README declares. Everything else was written
for this project. See [CREDITS.md](CREDITS.md).
