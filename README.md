# ConnectCSE to Google Drive Sync Bot

Logs into `https://connectcse.ruet.ac.bd`, checks `https://connectcse.ruet.ac.bd/classposts/student` for new PDF, PowerPoint, and Word attachments, and uploads them to a Google Drive folder without duplicates.

## 1. Local setup on Windows

```powershell
cd d:\Tuition\connectcse-drive-bot
python -m venv .venv
.\.venv\bin\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Add your ConnectCSE email and password to `.env` (never commit `.env` to Git).

## 2. Test ConnectCSE login first

```powershell
.\.venv\bin\python.exe sync.py --dry-run
```

If no links show up or the page uses nested post pages, run:

```powershell
.\.venv\bin\python.exe sync.py --inspect
```

## 3. Connect Google Drive once

1. Open Google Cloud Console and create a project.
2. Enable the Google Drive API.
3. Go to Google Auth Platform -> Audience -> add your Gmail address as a Test User, and keep Publishing status as Testing or switch to Production.
4. Go to Clients -> Create Client -> Desktop app, then download the JSON file and save it as `client_secret.json` in this folder.
5. Run:

```powershell
python auth_drive.py
```

Sign in to Google in the browser window. The script writes `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` directly into `.env`.

6. Run a full sync:

```powershell
python sync.py
```

## 4. Run free in the cloud with GitHub Actions

1. Push this folder to a new GitHub repository (public gives unlimited free minutes; private gives 2,000 free minutes per month).
2. Open the repository on GitHub -> Settings -> Secrets and variables -> Actions -> New repository secret, and add:
   - `CONNECTCSE_EMAIL`
   - `CONNECTCSE_PASSWORD`
   - `GOOGLE_DRIVE_FOLDER_ID`
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
   - `GOOGLE_REFRESH_TOKEN`
3. Open the Actions tab, select **Sync ConnectCSE to Google Drive**, and click **Run workflow** to verify. After that it runs every 30 minutes automatically.

## 5. Switching to a different Google account later

1. Add the new Google email as a Test User in Google Cloud Console (or publish the OAuth app).
2. Run `python auth_drive.py` and sign in with the new Google account.
3. Update `GOOGLE_DRIVE_FOLDER_ID` in `.env` and in GitHub Secrets with the new folder ID, plus the new `GOOGLE_REFRESH_TOKEN`.
