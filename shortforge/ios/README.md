# ShortForge for iOS

A SwiftUI client for a ShortForge dashboard. It talks to the same HTTP API the
web interface uses and signs in with the same dashboard password.

## What it does

- **Clip** — overview counters, the job list with live progress, a composer for
  new batches (count, length preset, aspect, captions), and a detail screen with
  the generated shorts, inline playback and virality signals.
- **Editor** — open any rendered short and trim it on a thumbnail filmstrip with
  draggable in/out handles, change speed, mute, then export to Photos or a share
  sheet. The clip is downloaded first and edited locally, so scrubbing stays
  instant even when the tunnel is slow.
- **Copy** — source channels, their fetched shorts, and a refresh action.
- **Cloud** — the VPS's desktop, streamed into the app over noVNC, with
  fullscreen, several sessions, the VNC password, and a button to launch a
  browser on it. This is where you sign in to Google, TikTok or NordVPN, since
  those flows need a real browser.
- **Activity** — the Tool Logs feed, filterable by clips / translations /
  publishing.
- **Settings** — full parity with the web dashboard: server address, queue
  pause and clear, Gemini key and model, Whisper model, TTS engine, ngrok token,
  watermark (with a live preview), default caption style and background music,
  YouTube cookie behaviour, VPN status / rotation / country switching, connected
  publishing accounts, and pipeline counters.

The only things that stay web-only are the flows that genuinely need a desktop
browser — and the Cloud tab gives you one.

## Building locally

```bash
brew install xcodegen
cd shortforge/ios
xcodegen generate
open ShortForge.xcodeproj
```

The `.xcodeproj` is generated from `project.yml` and deliberately not committed:
a checked-in `project.pbxproj` conflicts on every branch and drifts from the
file tree.

## The unsigned IPA

Every push that touches `shortforge/ios/**` runs
`.github/workflows/shortforge-ios.yml`, which archives without code signing and
uploads `shortforge-ios-unsigned-ipa` as a build artifact. You can also start it
by hand from the Actions tab (`workflow_dispatch`).

**An unsigned IPA will not launch on a stock iPhone.** iOS refuses to run a
bundle with no valid signature. To actually install it you need one of:

- **Sideloadly / AltStore** — signs the bundle with your own Apple ID on the way
  in. Free accounts get a 7-day validity and 3 apps.
- **TrollStore** — permanent installs, on the iOS versions it supports.
- **Your own certificate** — re-sign the uploaded `.xcarchive` with a paid
  developer account and export normally.

The workflow also uploads the raw `.xcarchive` for that last route.

## Server address

The sign-in screen is prefilled with the address in
`APIClient.defaultServer`, and Settings → *Change server address* overrides it
at runtime. Changing it signs you out, because the session cookie belongs to the
old host.

An ngrok tunnel gets a new hostname on every restart unless you reserve a
domain, so expect to change it.
