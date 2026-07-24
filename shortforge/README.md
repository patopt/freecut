# ⚡ ShortForge

A self-hosted, OpusClip-style shorts generator you run on your own Ubuntu VPS.
Paste a long YouTube URL in a password-protected web dashboard → ShortForge
downloads it, transcribes it, asks **Gemini** to pick the best moments, and
renders vertical **9:16 shorts** with animated word-by-word captions and
face-centred cropping — all automatically in the background. Watch progress
live, browse the generated clips per video, play them, and download them.

Mobile-first dashboard. Single user, single password. Optional public URL via
ngrok.

> This is a standalone tool that lives in the FreeCut repo but does **not**
> depend on the FreeCut editor. Server-side, headless short generation is done
> with `ffmpeg` + Whisper + Gemini (the same approach as open-source OpusClip
> alternatives like SamurAIGPT's *AI-Youtube-Shorts-Generator* and *ClipsAI*),
> which is far more robust on a headless server than driving a browser engine.

---

## Two modes

The dashboard has two tabs:

- **✂️ Clip** — the OpusClip-style generator: paste a long YouTube URL, get
  vertical shorts with captions (everything described below).
- **🎙️ Copy** — channel dubbing: add a YouTube **channel**, ShortForge lists all
  its shorts. Tap any short → **Translate** → pick a language → it downloads the
  original, transcribes it, translates each segment with Gemini, and generates a
  **time-aligned voiceover** (Kokoro-82M, realistic & offline; edge-tts
  fallback) that replaces the original narration —
  each translated segment starts at the same timestamp as the original, sped up
  to fit its slot so the dub stays in sync with the picture. Finished dubs play
  and download right from the short. A **⟳ refresh** button re-scans the channel
  for new shorts. Tap any dub chip to open a **detail view** with live progress,
  the full log (so failures are visible), and the retrieved + translated title,
  description and tags. Under **📺 Mes chaînes** you create your own named
  channels and send translated videos to them, so each dub lands, ready, in the
  destination channel you picked when translating.

## What it does (Clip mode)

```
YouTube URL
   │  yt-dlp
   ▼
source.mp4 ──► faster-whisper ──► word-level transcript
                                        │  Gemini (highlight selection)
                                        ▼
                              N best moments (start/end/title/score)
                                        │  per clip
                                        ▼
        ffmpeg: cut → 9:16 crop (face-centred) → burn animated captions
                                        ▼
                              short_01.mp4 … short_NN.mp4
```

- **Highlight detection** — Gemini reads the timestamped transcript and returns
  the most viral-worthy, self-contained moments (with a title + score). No key?
  It still works with an even-spacing fallback.
- **Auto vertical reframe** — samples frames, finds the dominant face with
  OpenCV, and centres a 1080×1920 crop on it (per clip). Falls back to a centre
  crop. *(This is static per-clip reframing, not full active-speaker tracking.)*
- **Animated captions** — burned-in, uppercase, highlighted word-by-word in
  sync with speech (ASS karaoke).
- **Background jobs** — a worker thread processes one job at a time; the
  dashboard shows live progress + a log via Server-Sent Events.

## Requirements

- Ubuntu/Debian VPS (or any Linux with `apt`). **Python 3.10–3.12** — the ML
  wheels have no binaries for 3.13+ yet, so `setup.sh` auto-installs 3.12 if
  your system Python is newer. **No GPU required** (runs fully on CPU).
- `ffmpeg` (installed automatically by `setup.sh`).
- A **Google Gemini API key** (for the smart highlight selection).
- CPU is fine (Whisper runs int8). A GPU speeds up transcription if present.

## Install (on your VPS)

```bash
git clone <your-repo-url> shortforge      # or copy this folder over
cd shortforge
./setup.sh                                # installs ffmpeg, venv, deps, writes .env
nano .env                                 # set DASHBOARD_PASSWORD (and API keys if you like)
```

## Run

```bash
./run.sh                # serves on http://<vps-ip>:8000
./run.sh --tunnel       # also opens a public https URL via ngrok and prints it
```

Then open the dashboard, sign in with your password, and open **Settings ⚙️**
to paste your Gemini API key (and ngrok token). You can also set these in
`.env` before first launch.

### Run it as a background service (optional)

```bash
# Keep it running after you log out:
nohup ./run.sh --tunnel > shortforge.log 2>&1 &

# …or install a systemd unit (recommended for a real VPS):
sudo tee /etc/systemd/system/shortforge.service >/dev/null <<EOF
[Unit]
Description=ShortForge
After=network.target
[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/run.sh
Restart=always
User=$(whoami)
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now shortforge
```

## Using the dashboard

1. Paste a YouTube URL, choose the number of clips, reframe mode, and whether to
   burn captions.
2. Hit **Generate shorts**. You jump to the job view and watch it run live
   (download → transcribe → analyze → render).
3. When done, each short shows as a thumbnail — tap to play, or **Download**.
4. The home screen lists every video you've processed; tap one to see its shorts.

## Configuration

Everything in **Settings** is stored in the local SQLite DB and can also be
seeded from `.env` on first boot:

| Setting | What |
|---|---|
| Gemini API key | Google AI Studio key used for highlight selection |
| Gemini model | e.g. `gemini-2.5-pro` — set whatever your key can access |
| Whisper model | `tiny`→`large-v3` (bigger = better + slower) |
| ngrok token | only needed for `./run.sh --tunnel` |
| Dashboard password | change it anytime |

All data (source videos, shorts, DB) lives under `./data/` — back that up or
delete it to reclaim space. Deleting a video from the dashboard removes its
files too.

## Limitations & honest notes

- **Reframing is static per clip** (median face position), not a moving
  active-speaker camera like OpusClip's premium reframe.
- **Highlight quality = model quality.** Gemini picks good moments but there's no
  trained "virality" model behind it.
- **First run downloads Whisper weights** (cached afterward).
- Long videos take a while on CPU — mostly transcription and encoding.

## Troubleshooting

**YouTube download fails ("not available on this app" / "sign in to confirm").**
YouTube blocks many downloads from datacenter/VPS IPs. Two fixes:

1. Make sure yt-dlp is current: `pip install -U yt-dlp` (in the venv).
2. Provide cookies from a logged-in account. Export your YouTube cookies as a
   Netscape `cookies.txt` (e.g. the "Get cookies.txt LOCALLY" browser
   extension), then place the file at `data/cookies.txt` — ShortForge picks it
   up automatically. This is the reliable fix for login/bot-check gating.

## ⚖️ Legal

Only download and repurpose videos you **own** or are licensed/authorised to
use. Downloading third-party YouTube content may violate YouTube's Terms of
Service and copyright law. You are responsible for how you use this tool.

## Tech

FastAPI · SQLite · yt-dlp · faster-whisper · google-genai · Kokoro-82M (ONNX) ·
edge-tts · OpenCV · ffmpeg · vanilla-JS mobile dashboard.
