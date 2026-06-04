# tg-channel-monitor

Userbot for monitoring Telegram channels across multiple independent projects. Watches for keyword matches in real time and sends formatted alerts to designated recipients.

Built on [Telethon](https://github.com/LonamiWebs/Telethon). Runs as a regular user account — no bot token required.

---

## How it works

- Listens to `NewMessage` events across all configured channels
- Matches post text against two keyword groups per project: **people** and **companies**
- Extracts the most relevant sentences containing the matched keywords
- Sends a formatted alert with highlighted keywords, channel metadata (tag, subscriber count), and a direct link to the post
- Channel metadata (tags, subscriber counts) is fetched once a week and cached locally in `channel_meta.json`

### Alert format

```
📰 Channel Name (12,345 subscribers) #Keyword1 #Keyword2

First two sentences of the post if it's long...

Sentence containing **Keyword1** in context.

<...>

Another sentence containing **Keyword2**.

🔗 Post (https://t.me/c/...)
```

For short posts (≤ 3 sentences), the full text is shown with keywords highlighted.

---

## Requirements

- Python 3.10+
- A Telegram account (API credentials from [my.telegram.org](https://my.telegram.org))

Install dependencies:

```bash
pip install telethon python-dotenv
```

---

## Setup

**1. Get Telegram API credentials**

Go to [my.telegram.org](https://my.telegram.org) → API development tools → create an app.  
You'll get `API_ID` (number) and `API_HASH` (string).

**2. Find your Telegram user ID**

Send any message to [@userinfobot](https://t.me/userinfobot) — it will reply with your numeric ID.

**3. Create `.env`**

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

**4. Run**

```bash
python userbot.py
```

On first run, Telethon will prompt for your phone number and a confirmation code — this creates a `user.session` file. Subsequent runs start silently.

---

## Configuration

All configuration lives in `.env`. The structure is project-based: you can monitor up to 10 independent projects, each with its own channels, keywords, and alert recipient.

### Global settings

| Variable | Description |
|---|---|
| `API_ID` | Telegram API ID from my.telegram.org |
| `API_HASH` | Telegram API hash from my.telegram.org |
| `MASTER_ID` | Your Telegram user ID — receives `/status`, `/ping` commands |

### Per-project settings

Each project is numbered `_1` through `_10`. A project is active when both `CHANNELS_N` and `OWNER_N` are set.

| Variable | Description |
|---|---|
| `CHANNELS_N` | Comma-separated list of channel usernames or numeric IDs |
| `OWNER_N` | Telegram user ID who receives alerts for this project |
| `PEOPLE_N` | Comma-separated list of person names to watch for |
| `COMPANIES_N` | Comma-separated list of company/org names to watch for |

Channels can be specified as:
- `username` — e.g. `durov`, `rbc_news`
- numeric ID — e.g. `-1001234567890`

At least one of `PEOPLE_N` or `COMPANIES_N` must be non-empty for a project to generate alerts.

### Channel tags

Optional display labels shown in alerts. Useful for marking channel type or region at a glance.

```
CHANNEL_TAGS=channelname:🔴 Breaking;otherchannel:[None]
```

Format: `username:tag`, separated by `;`. Use `[None]` to explicitly set no tag.

---

## Bot commands

Send these from the `MASTER_ID` account (in any chat where the userbot is active, or as a self-message):

| Command | Description |
|---|---|
| `/ping` | Health check — replies `pong` |
| `/status` | Shows all loaded projects with channel counts and owner IDs |
| `/update_meta` | Forces immediate refresh of channel metadata (tags + subscriber counts) |

---

## File structure

```
.
├── userbot.py          # Main script
├── .env                # Your configuration (never commit this)
├── .env.example        # Configuration template
├── channel_meta.json   # Auto-generated metadata cache (weekly refresh)
├── user.session        # Auto-generated Telethon session (never commit this)
└── test_userbot.py     # Unit tests for core logic (no Telegram required)
```

### .gitignore

Make sure to exclude sensitive and generated files:

```
.env
user.session
user.session-journal
channel_meta.json
__pycache__/
*.pyc
```

---

## Running the tests

The test suite covers all text processing and alert formatting logic without requiring a Telegram connection:

```bash
python test_userbot.py
```

Expected output: `40/40 тестов прошло ✅`

---

## Notes

- The userbot uses your personal Telegram account. Aggressive monitoring of many channels could theoretically trigger Telegram's anti-spam measures — keep the channel list reasonable.
- `user.session` stores your authenticated session. Treat it like a password.
- Metadata (subscriber counts) is updated at most once per week to avoid unnecessary API calls. Use `/update_meta` to force a refresh after adding new channels.
- Duplicate message handling uses a bounded in-memory cache (last 10,000 messages). This resets on restart, which is safe — Telethon won't re-deliver old events after reconnection.
