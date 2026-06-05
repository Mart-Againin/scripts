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
• Most informative sentence from the post.
• Another key sentence from a different part.
• ...

🔗 https://t.me/c/...
```

Posts shorter than or equal to `DIGEST_SHORT_LIMIT` (default 800 chars) are always forwarded in full.

Requires `digest.py` in the same folder and a virtual environment with Python 3.11 + NumPy 1.26.4 (see [Setup](#setup)).

### Mode 3 (future)

Keyword filtering + digest summarisation combined. Not implemented yet, but the architecture supports adding it.

---

## Requirements

- Python 3.11 installed to `C:\Python311\` (see Setup step 1)
- Telegram account — API credentials from [my.telegram.org](https://my.telegram.org)
- All dependencies are installed automatically by `setup_env.bat`

> **Why Python 3.11?**
> The digest mode uses LSA summarisation which requires NumPy. NumPy 2.x requires
> X86_V2 CPU instructions not available on QEMU Virtual CPU 2.5+. NumPy 1.26.4
> (Python 3.11) has no such requirement and works on any x86-64 processor.

---

## Setup

### Step 1 — Install Python 3.11

Download: [python.org/downloads/release/python-3119](https://www.python.org/downloads/release/python-3119/)
File: **Windows installer (64-bit)** → `python-3.11.9-amd64.exe`

During installation:
- ✅ **Add Python 3.11 to PATH**
- On the install location screen, change path to `C:\Python311\`

Verify:
```cmd
py -3.11 --version
```
Expected: `Python 3.11.9`

### Step 2 — Copy files to script folder

Place all these files in `C:\Python_scripts\Feedpars\`:

```
userbot.py
digest.py
.env
requirements.txt
setup_env.bat
run.bat
check_env.bat
```

### Step 3 — Fill in `.env`

Copy the template and fill in your values:
```cmd
copy .env.example .env
```

Set at minimum: `API_ID`, `API_HASH`, `MASTER_ID`, `BOT_MODE`, `CHANNELS_1`, `OWNER_1`.

### Step 4 — Create virtual environment

```cmd
cd C:\Python_scripts\Feedpars
setup_env.bat
```

This will:
1. Create `.venv\` with Python 3.11
2. Install all dependencies from `requirements.txt`
3. Download NLTK punkt_tab (~2 MB, one time)

Takes 2–5 minutes depending on connection speed.

### Step 5 — Verify

```cmd
check_env.bat
```

Expected output includes:
```
[OK] NumPy 1.26.4
[OK] LSA available
```

### Step 6 — Run

```cmd
run.bat
```

First run will prompt for your Telegram phone number and confirmation code — this creates `user.session`. Subsequent runs start silently.

> Always use `run.bat` instead of `python userbot.py` directly — it activates the correct virtual environment first.

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

Thresholds must be strictly ascending. Can also be changed live via Telegram commands without restarting.

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
C:\Python311\                    ← Python 3.11 (system-wide)

C:\Python_scripts\Feedpars\
    userbot.py                   # Main script
    digest.py                    # Summarisation module (digest mode only)
    .env                         # Your config (never commit)
    .env.example                 # Config template
    requirements.txt             # Pinned dependencies
    setup_env.bat                # Run once to create virtual environment
    run.bat                      # Run the bot
    check_env.bat                # Verify environment is healthy
    .venv\                       # Auto-generated virtual environment
    channel_meta.json            # Auto-generated — weekly metadata cache
    digest_config.json           # Auto-generated — digest scale overrides
    user.session                 # Auto-generated — Telethon session (never commit)
    test_userbot.py              # Tests — core logic
    test_digest.py               # Tests — digest module
```

### .gitignore

```
.env
user.session
user.session-journal
channel_meta.json
digest_config.json
.venv/
__pycache__/
*.pyc
```

---

## Tests

Run from inside the virtual environment:

```cmd
.venv\Scripts\activate.bat
python test_userbot.py   # 40/40
python test_digest.py    # 50/50
```

---

## Troubleshooting

**`py -3.11` not found after install** — close and reopen CMD. If still missing, check that `C:\Python311\` and `C:\Python311\Scripts\` are in the PATH environment variable.

**NumPy import error on startup** — you're running `python userbot.py` directly instead of `run.bat`. Always use `run.bat`.

**To reset the environment completely:**
```cmd
rmdir /s /q .venv
setup_env.bat
```

---

## Notes

- The bot runs on your personal account. Keep the channel list reasonable.
- `user.session` is your authenticated session — treat it like a password.
- In digest mode, every post fires a summarisation call. On this hardware ~0.1–0.5s per post (LSA, CPU-only).
- `digest_config.json` stores live scale overrides. Delete it or run `/digest_reset` to revert to `.env` defaults.
- The virtual environment in `.venv\` is fully isolated — other Python scripts on the server are not affected.
