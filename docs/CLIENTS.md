# Thin clients (phase 2)

The hub is still the only brain. Phones, the Samsung TV Internet app, a
laptop browser, an Echo Show 15, and a Fire TV all open a page on the
LAN. None of them talks to the TV or to Grok directly.

## Phone

Open `http://<hub-ip>:8765` (or `/phone`). Chat, library, drag-and-drop
transfers, and a mute toggle for the hub microphone. The in-page mic
button still needs HTTPS or localhost; the keyboard dictation key does
not. Add to Home Screen uses the existing web manifest.

## Samsung TV (QN75QN800DFXZA)

Open `http://<hub-ip>:8765/tv` in the built-in Internet app and bookmark
it. Playback of a library title now builds an HTTP Range stream URL and
hands it to the configured `TVController`. Until
`tv.controller_options` points at samsung-tv-root, the reply stays
`would_play` and the stream URL is ready. Incoming phone uploads appear
in the Incoming files row.

The TV's own microphones are **not** assumed to work in this browser.
Run the diagnostic at `http://<hub-ip>:8765/tv/mic-probe` and read
[TV_MIC_PROBE.md](TV_MIC_PROBE.md). If that page cannot get live audio,
treat the set as display-only.

## Laptop

Open `http://<hub-ip>:8765/laptop` (same page as the phone). On the hub
PC, `scripts\install_laptop_shortcut.ps1` drops a desktop shortcut.
Optional mute helper, standard-library only:

```powershell
py -3 -m jarvis.tray
```

## Echo Show 15

Use Silk (the Fire tablet / Echo Show browser) to open
`http://<hub-ip>:8765` or `/echo`. This is a thin client, not an Alexa
skill. Echo Show 15 idle / screen-off behavior can drop a long-lived
Silk tab; bookmark the page and reopen it if the live connection goes
away. A custom Alexa skill is **not** included: it would need a public
HTTPS endpoint on port 443, which this LAN-only hub does not expose.

## Fire TV

Use the Silk browser, or a sideloaded WebView, to open
`http://<hub-ip>:8765/tv` (also aliased as `/fire`). A packaged APK is
not required for phase 2 and is not built here (no Android SDK in this
repo). If you later sideload a wrapper, point it at that same URL.
