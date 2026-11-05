# Yoink

Paste a link, get the video. A small tkinter front end over
[yt-dlp](https://github.com/yt-dlp/yt-dlp), with ffmpeg to merge video and audio.
Plus an Android app: **Share → Yoink** on your phone and the link lands in
the queue on your PC.

## PC

### Use

- On start (and whenever you click back into the window) Yoink fills in any
  link on your clipboard. Press **Enter** or **Yoink!** to queue it. You can
  paste several links at once.
- The first download of a session asks where to save. It remembers the last
  folder as the starting point. **Change...** picks another one mid-session.
- Downloads run one at a time, always best quality, saved as `.mp4`
  (`Title [id].mp4`).
- **Use Firefox cookies** lets yt-dlp use your Firefox login, which X and
  other logged-in sites need. Untick it if a site acts weird.
- **Open in VideoCut** (or double-click a finished row) opens the file in
  VideoCutPlayer. The first time it can't find it, you point to the exe once.
- **Retry** / **Stop** / **Remove** / **Clear finished** work on the selected rows.
  Delete also removes.
- **Update yt-dlp**: sites break yt-dlp every few weeks. When a site that used
  to work fails, click this first.

Single links only: playlists are ignored (`--no-playlist`). DRM sites
(Netflix, Disney+, Videoland...) will never work.

### Build

Double-click `build.bat`. It:

1. copies ffmpeg + ffprobe from `..\VideoCut\bin\` (or from PATH)
2. downloads `yt-dlp.exe` into `bin\`, or updates it if it's already there
3. installs PyInstaller + `qrcode` if needed and builds
4. copies `bin\` to `dist\Yoink\bin\`

Result: `dist\Yoink\Yoink.exe`. Run from source: `pip install qrcode`, then
`python yoink.py` (needs `bin\yt-dlp.exe`, or yt-dlp on PATH).

Settings (last folder, cookies on/off, VideoCut path, phone key) live in
`%LOCALAPPDATA%\Yoink\yoink_config.json` for the exe, or next to
`yoink.py` when run from source.

## Phone

### Install

Download **`Yoink.apk`** from the **Releases** of this repo (release `latest`)
and open it on the phone.

### Pair (once)

1. Start Yoink on the PC → **Pair phone...**
2. Open the Yoink app → **QR-code scannen**.

The first time Yoink runs, Windows Firewall asks for permission: pick
**Private networks**.

### Use

In X, the browser or any app: **Share → Yoink**. A toast says what happened:

- **Yoinked ✅** — it's in the queue on the PC.
- **Bewaard 📦** — Yoink is closed or you're not on the home wifi. The link is
  kept on the phone and sent automatically once Yoink is open again: Android
  retries about every 15 minutes while on wifi. Opening the app, tapping
  **Nu versturen**, or sharing another link sends right away.

If no folder is chosen yet on the PC, Yoink asks for one as soon as the first
link from the phone arrives.

### How it works

- Yoink on the PC runs a small HTTP server on port `47824` while it's open.
- The QR code holds the PC's IP addresses, the port and a random secret key
  (192 bits). Requests without that key get `401`. **Unpair all phones** in
  the pairing window makes a new key; every phone then has to scan again.
- Traffic is **not encrypted** (plain HTTP). Fine on your own home network,
  not meant for public wifi.

| Method | Path | What |
|---|---|---|
| `GET` | `/api/info` | `{app, pc}` |
| `POST` | `/api/links` | `{urls:[...]}` → `{accepted}` |

All requests need header `X-Yoink-Token`.

## Code

- `yoink.py`, `build.bat` — the PC program. Python + tkinter, standard library
  plus `qrcode` (only for drawing the pairing QR code).
- `android/` — Kotlin, plain Views, no Compose. Only library:
  [zxing-android-embedded](https://github.com/journeyapps/zxing-android-embedded)
  for the QR scanner. Retries use Android's built-in JobScheduler.
- `.github/workflows/build.yml` — builds the `.apk` on every push to `main`
  and puts it in release `latest`.

## Signing key

The APK is signed with a fixed key so updates install over the old version.
It lives in the repo secrets (*Settings → Secrets and variables → Actions*):

- `SIGNING_KEYSTORE_BASE64` — the keystore, base64
- `SIGNING_PASSWORD` — password of both keystore and key (alias `yoink`)

Without them the workflow still compiles, but publishes no release.
