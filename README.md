# email-triage

Repository for the `email-triage` skill and its helper scripts. The repo supports Gmail OAuth and generic IMAP, fetches messages into local files, and can create drafts from generated reply data.

Agent workflow, prompting rules, and behavior live in `SKILL.md`.

## Install

Install skill from GitHub:

```powershell
npx skills add https://github.com/MisterSawyer/email-triage --skill email-triage
```

## Configuration

### Gmail

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

### IMAP

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

### Notes

- IMAP defaults to SSL port `993`.
- Set `PYTHONENCODING=utf-8` if your terminal has encoding issues.
- Gmail helper scripts store OAuth credentials in `token-gmail.json`.
