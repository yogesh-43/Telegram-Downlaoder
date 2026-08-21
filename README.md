# TG Saver

Local web app that bulk-downloads videos, photos, audio, and files from Telegram chats **your account can already open**. It uses the official [Telethon](https://docs.telethon.dev/) API on your computer. Credentials stay on this machine.

## What you need

- Python 3.10 or newer
- A Telegram account (phone number)
- Your own **API ID** and **API Hash** from Telegram (created once, free)

Use your own PC. Cloud VMs (AWS, GCP, Azure, and similar) are often blocked at login.

---

## Step 1 — Create a Telegram application

Every person must create **their own** app. Do not copy API values from a tutorial, a friend, or this repo.

### Sign in to my.telegram.org

1. Open [https://my.telegram.org](https://my.telegram.org).
2. In **Your phone number**, type the number of your Telegram account with country code, for example `+919876543210`.
3. Click **Next**.
4. Telegram sends a login code:
   - Usually a message from the account named **Telegram** inside the Telegram app
   - Sometimes SMS
5. Enter that code on the website and confirm.

If you have Two-Step Verification enabled, the site will also ask for that cloud password.

### Open API development tools

6. On the logged-in page, click **API development tools**.
7. If you have never created an app, Telegram shows a form. Fill it in:

| Field | What to enter |
|-------|----------------|
| **App title** | `TG Saver` (any name is fine) |
| **Short name** | `tgsaver` (letters only, no spaces, 5–32 characters) |
| **URL** | leave empty |
| **Platform** | Desktop |
| **Description** | optional, e.g. `Personal media downloader` |

8. Click **Create application**.

### Copy api_id and api_hash

9. Telegram shows your application page. Copy two values and keep them private:

| Field | Looks like |
|-------|------------|
| **`api_id`** | a number, e.g. `12345678` |
| **`api_hash`** | 32 characters, e.g. `0123456789abcdef0123456789abcdef` |

You will put these in `.env` (next step) or paste them into the TG Saver login screen.

**Do not** share them, screenshot them into a public issue, or commit them to GitHub. Anyone with them can request logins as your app.

If the site says the API ID is invalid or “published / flooded”, create a **new** application instead of reusing an old hash.

---

## Step 2 — Put credentials in `.env`

From this folder:

```bash
cp .env.example .env
```

On Windows (Command Prompt):

```bat
copy .env.example .env
```

Open `.env` and fill in the values from Step 1:

```
API_ID=12345678
API_HASH=paste_your_api_hash_here
```

No quotes, no spaces around `=`. Save the file.

`.env` is gitignored. The example file (`.env.example`) stays empty so it is safe to commit.

If you skip `.env`, the app still works: you paste API ID and API Hash in the browser on first launch. `.env` only pre-fills them when `data/config.json` does not already have values.

---

## Step 3 — Install and start the app

```bash
cd telegram_downloader
python3 -m venv .venv
```

**Linux / macOS**

```bash
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

**Windows**

```bat
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Leave that terminal open. In a browser go to [http://127.0.0.1:7860](http://127.0.0.1:7860).

---

## Step 4 — Process while you work (every session)

### First time only

1. If the **API** form appears, paste `api_id` and `api_hash` (skip if `.env` already filled them) → **Continue**.
2. Enter the **same phone number** you used on my.telegram.org, with country code → **Send login code**.
3. Open Telegram, copy the login code, paste it → **Verify**.
4. If asked, enter your Two-Step Verification password → **Unlock**.

The login session is saved in `data/tg_session.session`. You will not need a code again until you click **Log out** or delete `data/`.

### Each time you download media

1. Start the app (`python app.py`) and open [http://127.0.0.1:7860](http://127.0.0.1:7860).
2. Pick a chat on the left, **or** paste `@channel`, a `t.me/…` link, or a numeric chat ID.
3. Tick the media types you want (Video, Photo, Audio, Files, Voice, GIF).
4. Set **Scan limit** if the chat is huge (default 300 messages per type, max 2000).
5. Click **Fetch media** and wait. Already-saved files show as **on disk**.
6. Tick files (or **Select all**) → **Download selected**.
7. Watch **Downloads** on the right. Each finished file is written to disk immediately.
8. Click **Open folder** (or look under Settings) to find the files.

Default save location:

- Linux / macOS: `~/Downloads/TG Saver/<chat name>/`
- Windows: `%USERPROFILE%\Downloads\TG Saver\<chat name>\`

Change the folder, parallel downloads (1–8), and date prefixes under **Settings**.

You can only download from chats you already belong to. This tool does not join groups or bypass Telegram login.

Stop the app with `Ctrl+C` in the terminal when you are done.

---

## What not to do

- Do not commit `.env`, `data/`, `*.session`, or downloaded files (already in `.gitignore`)
- Do not run two `python app.py` processes at once (session database lock)
- Do not use a published / shared API hash
- Only save media you own or have permission to keep — you are responsible for [Telegram’s Terms](https://telegram.org/tos) and copyright

---

## Troubleshooting

| Problem | What to try |
|---------|-------------|
| Invalid API ID / hash | Recreate the app at [my.telegram.org/apps](https://my.telegram.org/apps). Put the new values in `.env` or the API form. |
| Phone number invalid | Include `+` and the country code. |
| Login blocked from this machine | Use your own PC, not a cloud VM. Or copy an existing `data/tg_session.session` from a PC that already logged in. |
| Flood wait | Wait the number of seconds Telegram shows, then try once. |
| Another process is using the session | Stop the other `python app.py` and start only one. |
| Scan finds nothing | You must already be a member of that chat. Try other media types or raise the scan limit. |
| `.env` values ignored | `data/config.json` already has API values. Log out, or delete `data/config.json` and restart. |

---

## Layout

| Path | What it is |
|------|------------|
| `app.py` | Local web server (port 7860) |
| `engine.py` | Login, scan, download queue |
| `fast_download.py` | Parallel chunk downloads |
| `media_utils.py` | Links, filenames, duplicates |
| `web/` | Browser UI |
| `.env.example` | Template for API ID / hash |
| `.env` | Your secrets (create this, gitignored) |
| `data/` | Config + Telegram session (gitignored) |

```bash
python -m pytest tests/ -q
```
