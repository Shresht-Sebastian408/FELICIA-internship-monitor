# Credits

## Written for this project

Everything under `internships/`, `scripts/`, `config/`, `tests/`, plus
`shared/config.py`, `shared/telegram.py` and `shared/utils.py`.

## Derived from Alpha-Forge

Two modules originate from the [Alpha-Forge](https://github.com/Anchitlahkar/Alpha-Forge)
project by **Anchit Lahkar**, reused under the MIT licence its README declares:

| File | What came from Alpha-Forge | What changed here |
|---|---|---|
| `shared/gemini_client.py` | `GeminiClientManager` and the rate-limit / quota / dead-key classification helpers | Model ids moved to configuration; `call_gemini_text` added |
| `shared/json_repair.py` | `repair_json` in full | Unchanged |

Both carry an attribution header. The key rotator is genuinely good work and
was not worth reimplementing to avoid a credit line: it distinguishes
per-minute throttling from per-day exhaustion, honours the `retryDelay` the API
returns, and cools keys down individually rather than retiring the whole pool
on the first 429.

If you are the original author and would prefer different attribution or a
different arrangement, please open an issue.
