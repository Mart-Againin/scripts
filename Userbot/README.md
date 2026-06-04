# tg-channel-monitor

Userbot for monitoring Telegram channels across multiple independent projects. Built on [Telethon](https://github.com/LonamiWebs/Telethon). Runs as a regular user account — no bot token required.

---

## Operating modes

The bot runs in one of two independent modes, set via `BOT_MODE` in `.env`.

### `BOT_MODE=keyword` (default)

Monitors configured channels and forwards **only posts that match at least one keyword** from `PEOPLE_N` or `COMPANIES_N`. Each alert contains a short excerpt with the matching keywords highlighted.

```
📰 Channel Name (12,345 subs) #Keyword1 #Keyword2

First two sentences of the post...

Sentence containing **Keyword1** in context.

<...>

Another sentence with **Keyword2**.

🔗 Post (https://t.me/c/...)
```

`digest.py` is not needed in this mode.

### `BOT_MODE=digest`

Monitors configured channels and forwards **every post with text**, regardless of content. Long posts are summarised into an annotation and key theses. Keywords are not used.

```
📰 Channel Name (12,345 subs)

📌 First paragraph or opening sentence (annotation).

Theses:
1. Most informative sentence from the post.
2. Another key sentence from a different part.
3. ...

🔗 https://t.me/c/...
```

Posts shorter than or equal to `DIGEST_SHORT_LIMIT` (default 800 chars) are always forwarded in full.

Requires `digest.py` in the same folder and `sumy` installed.

### Mode 3 (future)

Keyword filtering + digest summarisation combined. Not implemented yet, but the architecture supports adding it.

---

## Requirements

- Python 3.10+
- Telegram account — API credentials from [my.telegram.org](https://my.telegram.org)

**keyword mode:**
```bash
pip install telethon python-dotenv
```

**digest mode (additional):**
```bash
pip install sumy
python -c "import nltk; nltk.download('punkt_tab')"
```

`punkt_tab` downloads once (~2 MB) and is cached locally.

---

## Setup

**1.** Go to [my.telegram.org](https://my.telegram.org) → API development tools → create an app. Get `API_ID` and `API_HASH`.

**2.** Get your Telegram user ID from [@userinfobot](https://t.me/userinfobot).

**3.** Copy and fill the config:
```bash
cp .env.example .env
```

**4.** Set `BOT_MODE=keyword` or `BOT_MODE=digest` in `.env`.

**5.** Run:
```bash
python userbot.py
```

First run prompts for phone number and confirmation code, creating `user.session`. Subsequent runs start silently.

---

## Configuration

### Global

| Variable | Description |
|---|---|
| `API_ID` | Telegram API ID |
| `API_HASH` | Telegram API hash |
| `MASTER_ID` | Your user ID — receives `/status`, `/ping` commands |
| `BOT_MODE` | `keyword` or `digest` |

### Digest settings (digest mode only)

| Variable | Default | Description |
|---|---|---|
| `DIGEST_SHORT_LIMIT` | `800` | Posts ≤ this are forwarded in full |
| `DIGEST_MEDIUM_LIMIT` | `1500` | Upper bound of the "medium" zone |
| `DIGEST_LONG_LIMIT` | `3000` | Upper bound of "long"; above = longread |
| `DIGEST_MEDIUM_THESES` | `2` | Theses for medium posts |
| `DIGEST_LONG_THESES` | `3` | Theses for long posts |
| `DIGEST_LONGREAD_THESES` | `4` | Theses for longreads |

Thresholds must be strictly ascending. Can also be changed live via Telegram commands.

### Per-project settings

Each project is numbered `_1` through `_10`. Active when both `CHANNELS_N` and `OWNER_N` are set.

| Variable | Description |
|---|---|
| `CHANNELS_N` | Channel usernames or numeric IDs, comma-separated |
| `OWNER_N` | User ID who receives alerts for this project |
| `PEOPLE_N` | Person names to match (keyword mode only) |
| `COMPANIES_N` | Company/org names to match (keyword mode only) |

### Channel tags

```
CHANNEL_TAGS=rbc_news:🔴;kommersant:📰;other:[None]
```

Optional labels shown in alert headers. `[None]` disables the tag for that channel.

---

## Commands

### Base (MASTER_ID only)

| Command | Description |
|---|---|
| `/ping` | Health check |
| `/status` | Active projects, channel counts, current mode |
| `/update_meta` | Force refresh of channel metadata |

### Digest scale (MASTER_ID + all OWNER_N, digest mode only)

Settings persist in `digest_config.json` across restarts.

| Command | Example | Description |
|---|---|---|
| `/digest_status` | | Current scale and theses counts |
| `/digest_set` | `/digest_set short 600` | Set zone boundary in characters |
| `/digest_theses` | `/digest_theses longread 5` | Set theses count for a zone |
| `/digest_reset` | | Revert to `.env` defaults |

Zones for `/digest_set`: `short`, `medium`, `long`
Zones for `/digest_theses`: `medium`, `long`, `longread`

---

## File structure

```
.
├── userbot.py           # Main script
├── digest.py            # Summarisation module (digest mode only)
├── .env                 # Your config (never commit)
├── .env.example         # Config template
├── channel_meta.json    # Auto-generated — weekly metadata cache
├── digest_config.json   # Auto-generated — digest scale overrides
├── user.session         # Auto-generated — Telethon session (never commit)
├── test_userbot.py      # Tests — core logic
└── test_digest.py       # Tests — digest module
```

### .gitignore

```
.env
user.session
user.session-journal
channel_meta.json
digest_config.json
__pycache__/
*.pyc
```

---

## Tests

```bash
python test_userbot.py   # 40/40
python test_digest.py    # 49/49
```

No Telegram connection required.

---

## Notes

- The bot runs on your personal account. Keep the channel list reasonable.
- `user.session` is your authenticated session — treat it like a password.
- In digest mode, every post fires a summarisation call. On weak hardware this adds ~0.1–0.5s per post (LSA, CPU-only, no GPU needed).
- `digest_config.json` stores live scale overrides. Delete it or run `/digest_reset` to revert to `.env` defaults.
