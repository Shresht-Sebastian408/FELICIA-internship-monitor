"""
Telegram setup and live-test helper.

    python scripts/telegram_setup.py --check           # who am I? are the tokens valid?
    python scripts/telegram_setup.py --discover        # find the chat id to use
    python scripts/telegram_setup.py --test            # send a plain hello
    python scripts/telegram_setup.py --sample          # send real formatted samples
    python scripts/telegram_setup.py --all             # check, discover, then sample

Pass --token/--chat to try credentials without putting them in .env first.
"""
import argparse
import sys
from pathlib import Path

_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import requests  # noqa: E402

from shared import config  # noqa: E402
from shared.telegram import API_ROOT, TelegramSender, esc  # noqa: E402

OK = "[ ok ]"
BAD = "[fail]"
INFO = "[info]"


def get_me(token: str) -> dict | None:
    if not config.is_configured(token):
        return None
    try:
        r = requests.get(f"{API_ROOT}/bot{token}/getMe", timeout=15)
        data = r.json()
    except Exception as e:
        print(f"{BAD} getMe request failed: {e}")
        return None
    if not data.get("ok"):
        print(f"{BAD} Telegram rejected the token: {data.get('description')}")
        return None
    return data["result"]


def check(tokens: dict[str, str]) -> dict[str, dict]:
    """Validate each configured token and report the bot behind it."""
    print("== Token check ==")
    seen: dict[str, dict] = {}
    for label, token in tokens.items():
        if not config.is_configured(token):
            print(f"{INFO} {label:12} not configured")
            continue
        me = get_me(token)
        if me:
            print(f"{OK} {label:12} @{me['username']} ({me.get('first_name','')}) id={me['id']}")
            seen[label] = me
        else:
            print(f"{BAD} {label:12} token present but invalid")
    if not seen:
        print(f"\n{BAD} No usable token. Create a bot with @BotFather, then put the "
              f"token in .env as TELEGRAM_BOT_TOKEN.")
    return seen


def discover(token: str) -> list[dict]:
    """
    List every chat the bot has seen, so the right chat id can be picked.

    A bot cannot enumerate its chats. It only learns a chat id when something
    happens there, which is why the instructions below ask for a message or for
    the bot to be added to the channel first.
    """
    print("\n== Chat discovery ==")
    if not config.is_configured(token):
        print(f"{BAD} No token to discover with.")
        return []

    try:
        r = requests.get(f"{API_ROOT}/bot{token}/getUpdates",
                         params={"timeout": 0, "limit": 100}, timeout=20)
        data = r.json()
    except Exception as e:
        print(f"{BAD} getUpdates failed: {e}")
        return []

    if not data.get("ok"):
        print(f"{BAD} getUpdates rejected: {data.get('description')}")
        return []

    chats: dict[int, dict] = {}
    for update in data.get("result", []):
        for key in ("message", "channel_post", "edited_channel_post",
                    "my_chat_member", "chat_member"):
            payload = update.get(key)
            if isinstance(payload, dict) and isinstance(payload.get("chat"), dict):
                chat = payload["chat"]
                chats[chat["id"]] = chat

    if not chats:
        print(f"{INFO} No chats seen yet. Do one of these, then re-run:")
        print("       - Direct message: open the bot in Telegram and send it /start")
        print("       - Channel: add the bot as an ADMIN, then post any message there")
        print("       - Group: add the bot, then send any message in the group")
        return []

    for chat_id, chat in chats.items():
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
        print(f"{OK} chat_id={chat_id:<16} type={chat.get('type'):<10} {title}")

    print("\n   Put the id you want in .env:")
    for chat_id in chats:
        print(f"       TELEGRAM_CHAT_ID={chat_id}")
    return list(chats.values())


def write_env(updates: dict[str, str], path: Path | None = None) -> None:
    """
    Update keys in .env in place, preserving comments and ordering.

    Only the named keys are touched, so a hand-edited file keeps everything
    else exactly as it was.
    """
    path = path or (Path(_ROOT) / ".env")
    if not path.exists():
        example = Path(_ROOT) / ".env.example"
        path.write_text(example.read_text(encoding="utf-8") if example.exists() else "",
                        encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"

    for key, value in remaining.items():
        lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{OK} wrote {', '.join(updates)} to {path.name}")


def send_test(token: str, chat_id: str) -> bool:
    print("\n== Plain test message ==")
    sender = TelegramSender(token, chat_id, label="test", sleep=__import__("time").sleep)
    if not sender.enabled:
        print(f"{BAD} Token or chat id missing.")
        return False
    ok = sender.send(
        "<b>Connection test</b>\n"
        "If you can read this, the bot can post here.\n"
        "Escaping check: AT&amp;T · &lt;Senior&gt; Engineer"
    )
    print(f"{OK if ok else BAD} plain test message")
    return ok


def send_samples(token: str, chat_id: str) -> bool:
    """Send genuinely formatted messages from both services."""
    import time

    from internships import notify

    print("\n== Formatted samples ==")
    sender = TelegramSender(token, chat_id, label="sample", sleep=time.sleep)
    if not sender.enabled:
        print(f"{BAD} Token or chat id missing.")
        return False

    internship = {
        "company": "AT&T", "title": "<Senior> Software Engineer Intern",
        "url": "https://example.test/apply", "locations": ["Dallas, TX", "Atlanta, GA"],
        "season": "Summer 2027", "domain": "Software Engineering",
        "source_name": "SimplifyJobs Summer 2027",
        "sponsorship": "Does Not Offer Sponsorship",
        "blurb": "Backend team, 12-week paid program.", "relevance": 9.0,
        "date_posted": 0,
    }
    scholarship = {
        "company": "MPOWER Financing", "title": "MPOWER Global Citizen Scholarship",
        "url": "https://example.test/scholarship", "locations": ["United States"],
        "season": "Multiple", "domain": "Scholarship/Program",
        "source_name": "Underclassmen Opportunities", "sponsorship": "Not Specified",
        "target_year": ["Freshman (1st year)", "Sophomore (2nd year)"],
        "scholarship_amount": "$1,000 - $8,000", "deadline": "Aug 31, 2026",
        "blurb": "", "relevance": 8.0, "date_posted": 0,
    }

    results = [
        sender.send(notify.format_posting(internship)),
        sender.send(notify.format_posting(scholarship)),
        sender.send(notify.format_overflow([
            dict(internship, company="ByteDance"),
            dict(internship, company="Stripe"),
            dict(scholarship, company="Scholarship America"),
        ])),
    ]

    ok = all(results)
    print(f"{OK if ok else BAD} sent {sum(results)}/{len(results)} sample messages")
    return ok


def main():
    p = argparse.ArgumentParser(description="Telegram setup and live test")
    p.add_argument("--check", action="store_true", help="Validate tokens via getMe")
    p.add_argument("--discover", action="store_true", help="List chat ids the bot has seen")
    p.add_argument("--test", action="store_true", help="Send one plain message")
    p.add_argument("--sample", action="store_true", help="Send real formatted samples")
    p.add_argument("--all", action="store_true", help="check, discover, then sample")
    p.add_argument("--token", default=None, help="Override the bot token")
    p.add_argument("--chat", default=None, help="Override the chat id")
    p.add_argument("--save", action="store_true",
                   help="Write the token and discovered chat id back to .env")
    args = p.parse_args()

    if not any([args.check, args.discover, args.test, args.sample, args.all]):
        p.print_help()
        return

    token = args.token or config.TELEGRAM_INTERNSHIPS_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN
    chat_id = args.chat or config.TELEGRAM_INTERNSHIPS_CHAT_ID or config.TELEGRAM_CHAT_ID

    if args.check or args.all:
        check({
            "shared": config.TELEGRAM_BOT_TOKEN,
            "internships": config.TELEGRAM_INTERNSHIPS_BOT_TOKEN,
            "override": args.token or "",
        })

    if args.discover or args.all:
        found = discover(token)
        if found and not config.is_configured(chat_id):
            # Prefer a channel or group over a DM when several are visible: a
            # dedicated feed is what both services are pointed at in production.
            ranked = sorted(found, key=lambda c: 0 if c.get("type") in
                            ("channel", "supergroup", "group") else 1)
            chat_id = str(ranked[0]["id"])
            print(f"{INFO} Using discovered chat id {chat_id} ({ranked[0].get('type')}).")

        # Only persist a chat id that is real. The unset placeholder from
        # .env.example is truthy, and writing it back would look like success.
        if args.save and config.is_configured(chat_id):
            updates = {"TELEGRAM_CHAT_ID": chat_id}
            if args.token:
                updates["TELEGRAM_BOT_TOKEN"] = args.token
            write_env(updates)

    if args.test:
        send_test(token, chat_id)

    if args.sample or args.all:
        send_samples(token, chat_id)


if __name__ == "__main__":
    main()
