# ConnectCSE to Google Drive sync bot

Logs into `https://connectcse.ruet.ac.bd`, scans every `/classposts/<id>` post detail page under `/classposts/student`, and syncs attachments and shared links into per-course subfolders inside your Google Drive without duplicates.

## What it syncs

- Multi-file PDF, PowerPoint, and Word attachments (`.pdf`, `.ppt`, `.pptx`, `.doc`, `.docx`).
- Accessible Google Drive folders and Google Docs links inside post messages as native Drive shortcuts.
- Restricted or external links inside post messages as clickable `<Post Title> - Links.html` files.
- Organizes all items into subfolders named after each post's course name.

## Project structure

```text
connectcse-drive-bot/
├── sync.py                    # Main CLI entry point (--dry-run, --inspect, --max-pages)
├── config.py                  # Loads .env and environment variables into SyncConfig
├── connectcse_client.py       # Laravel login, pagination, and file downloads
├── post_parser.py             # Extracts course name, multi-file links, and shared URLs
├── drive_client.py            # Google Drive v3 subfolders, uploads, shortcuts, and dedup
├── auth_drive.py              # One-time local Google OAuth helper for refresh tokens
├── requirements.txt           # Pure-Python dependencies (requests, beautifulsoup4, python-dotenv)
├── .env.example               # Template for local credentials and folder ID
└── .github/workflows/sync.yml # GitHub Actions workflow running every 5 hours
```

## How to use

### 1. Local setup

```bash
cd /d/Tuition/connectcse-drive-bot
python -m venv .venv
./.venv/bin/python.exe -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `CONNECTCSE_EMAIL`, `CONNECTCSE_PASSWORD`, and `GOOGLE_DRIVE_FOLDER_ID` inside `.env`. Never commit `.env` or `client_secret.json` to Git.

### 2. Connect Google Drive once

1. Open Google Cloud Console at https://console.cloud.google.com/ and enable the Google Drive API.
2. Under **Google Auth Platform**, **Audience**, add your Google email under **Test users** or click **Publish app** so refresh tokens do not expire after 7 days.
3. Under **Clients**, create a **Desktop app** OAuth client, download its JSON, and save it as `client_secret.json` in this folder.
4. Run the OAuth helper and approve Drive access in your browser:

```bash
./.venv/bin/python.exe auth_drive.py
```

### 3. Run manually

In Git Bash:

```bash
cd /d/Tuition/connectcse-drive-bot
./.venv/bin/python.exe sync.py
```

In PowerShell:

```powershell
cd d:\Tuition\connectcse-drive-bot
.\.venv\bin\python.exe sync.py
```

Useful flags:

- `--dry-run` logs in and lists discovered course folders, files, and links without uploading.
- `--inspect` prints each post's raw parsed files and links for debugging.
- `--max-pages N` sets how many pagination pages to scan. Default is 5.

### 4. Run automatically in the cloud with GitHub Actions

1. Push this repository to GitHub.
2. Open **Settings**, **Secrets and variables**, **Actions**, and add these six repository secrets from `.env`:
   - `CONNECTCSE_EMAIL`
   - `CONNECTCSE_PASSWORD`
   - `GOOGLE_DRIVE_FOLDER_ID`
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`
3. Open the **Actions** tab, select **Sync ConnectCSE to Google Drive**, and click **Run workflow** once. After that, `.github/workflows/sync.yml` runs every 5 hours at `00:00`, `05:00`, `10:00`, `15:00`, and `20:00` UTC.

### 5. Switch Google accounts later

1. Update `GOOGLE_DRIVE_FOLDER_ID` in `.env` to the new account's folder ID.
2. Run `./.venv/bin/python.exe auth_drive.py` and sign in with the new Google account.
3. Update `GOOGLE_DRIVE_FOLDER_ID` and `GOOGLE_REFRESH_TOKEN` in your GitHub Repository Secrets.

## Author

- **Name:** MD Moon Babu
- **GitHub:** https://github.com/MominulMoon
- **Email:** m.mmoon1527@gmail.com
- **Repository:** https://github.com/MominulMoon/connectcse-drive-bot
