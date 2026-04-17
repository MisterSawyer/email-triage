# email-triage

Agent-agnostic inbox triage toolkit for:

- Gmail (OAuth)
- Any IMAP server

## Install

```powershell
npx skills add https://github.com/MisterSawyer/email-triage --skill email-triage
```

## Setup

Configure provider

### Gmail provider env vars

```powershell
$env:GMAIL_OAUTH_CLIENT_ID="<client_id>"
$env:GMAIL_OAUTH_CLIENT_SECRET="<client_secret>"
# optional
$env:GMAIL_OAUTH_PROJECT_ID="<project_id>"
```

Optional full client JSON:

```powershell
$env:GMAIL_OAUTH_CLIENT_CONFIG_JSON=(Get-Content -Raw path\to\oauth-client.json)
```

### IMAP provider env vars

```powershell
$env:IMAP_HOST="<imap_server_host>"
$env:IMAP_USERNAME="<mailbox_login>"
$env:IMAP_PASSWORD="<mailbox_password_or_app_password>"
# optional
$env:IMAP_PORT="993"
$env:IMAP_MAILBOX="INBOX"
$env:IMAP_DRAFTS_MAILBOX="Drafts"
$env:IMAP_FROM="me@example.com"
```

Notes:

- Default IMAP SSL port is `993`.
- For terminal output with emojis/non-ASCII text, set `PYTHONENCODING` to UTF-8 before running scripts:

```powershell
$env:PYTHONENCODING="utf-8"
```

## What it does

- fetches emails into `output/emails.json`
- classifies messages by priority/actionability
- treats newsletters, ads, and similar bulk/promotional mail as ignore by default
- generates suggested replies into `output/reply-drafts.json`
- avoids drafting replies when no specific actionable context is present
- writes a summary report to `output/triage-report.md`
- optionally creates provider drafts from `output/reply-drafts.json` (Gmail or IMAP)
- when source thread metadata is provided, creates reply-style drafts (`In-Reply-To`/`References`, and Gmail `threadId`)
- when a corrected draft is created for the same source email/thread, keeps the newest and removes older managed drafts
- never sends emails automatically

## Fetch commands

### Gmail fetch

```powershell
.venv\Scripts\python.exe scripts\fetch_gmail.py --query "in:inbox" --limit 5 --output output\emails.json
```

### IMAP fetch

```powershell
.venv\Scripts\python.exe scripts\fetch_imap.py --search ALL --limit 5 --output output\emails.json
```

Example for unread-first IMAP triage:

```powershell
.venv\Scripts\python.exe scripts\fetch_imap.py --search UNSEEN --limit 20 --output output\emails.json
```

## Create drafts

### Gmail drafts

```powershell
.venv\Scripts\python.exe scripts\create_gmail_drafts.py output\reply-drafts.json
```

The script removes superseded drafts automatically after creation. For the same source email/thread, it keeps the newest managed draft and removes older ones.
For true Gmail thread replies, include `source_thread_id` (or `thread_id`) and an RFC `source_message_id` like `<abc123@example.com>`.

### IMAP drafts

```powershell
.venv\Scripts\python.exe scripts\create_imap_drafts.py output\reply-drafts.json
```

The script removes superseded drafts automatically after creation. For the same source email/thread, it keeps the newest managed draft and removes older ones.
For thread-aware behavior in clients that support it, include an RFC `source_message_id` and optional `source_references`.

`IMAP_DRAFTS_MAILBOX` is optional. If not set, the script tries to auto-detect the mailbox flagged as `\\Drafts`, then falls back to `Drafts`.

## Output example

`output/reply-drafts.json`:

```json
[
  {
    "source_ref": "thread:18c4d6e9a1b2c3d4",
    "source_thread_id": "18c4d6e9a1b2c3d4",
    "source_message_id": "<abc123@example.com>",
    "source_references": [
      "<older1@example.com>",
      "<older2@example.com>"
    ],
    "to": "person@example.com",
    "subject": "Re: Project update",
    "body": "Hi,\n\nThanks for your email ...\n\nBest,"
  }
]
```

`source_ref` is required in each draft item. Use a stable value per source email/thread, for example:
- `thread:<thread_id>` when thread id is known
- `message:<message_id>` when thread id is unavailable

When building draft items from `output/emails.json`, use:
- `thread_id` -> `source_thread_id`
- `internet_message_id` (or `message_id` when it is RFC-style) -> `source_message_id`
- `references` -> `source_references` (optional)
