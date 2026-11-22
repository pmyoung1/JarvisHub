# TV microphone probe

A diagnostic page, not a dependency. Phones, the hub PC microphone, and
the Echo Show remain the primary ears. The QN75QN800DFXZA is treated as
display-only unless this probe records live audio on that set.

## How to run it

1. On the Samsung TV, open the built-in **Internet** app.
2. Go to `http://<hub-ip>:8765/tv/mic-probe` (use `https://` if you have
   already enabled the hub's self-signed certificate).
3. Press **Enter** or the **Run probe** tile. The page prints every
   step on screen and POSTs a JSON report to the hub.
4. On the Hub PC, read the latest reports:

   ```powershell
   curl http://127.0.0.1:8765/api/mic-probe
   curl http://127.0.0.1:8765/api/health
   ```

   `GET /api/health` includes a short `mic_probe` summary:
   `{last_at, tizen_api: ok|fail|absent, getusermedia: ok|fail|absent|insecure, live_audio}`.
   Full step traces live in `data/mic_probe.json` and in `data/logs/hub.log`.

## What the page tries

### 1. Tizen Samsung Product API (Microphone)

Samsung documents this API at
[Microphone API](https://developer.samsung.com/smarttv/develop/api-references/samsung-product-api-references/microphone-api.html)
(Samsung Product API, since 2.3, TV/B2B).

Packaged Tizen apps load the library with:

```html
<script type="text/javascript" src="$WEBAPIS/webapis/webapis.js"></script>
```

as shown in the Product API overview and in Samsung's own samples
(for example the Checkout guide's `$WEBAPIS/webapis/webapis.js` include).
The `$WEBAPIS` token is resolved by the Tizen runtime for a **packaged**
app; it is not a real URL in a normal browser, so this script is
expected to 404 in the TV Internet app and on a desktop.

If `window.webapis.microphone` exists, the probe calls, in order:

- `webapis.microphone.getVersion()` (when present)
- `webapis.microphone.getMicrophones(onsuccess, onerror)`
- `webapis.microphone.isConnected(uid)` for each device
- `device.enableDevice(webapis.microphone.MICROPHONE_FORMAT_SIGNED_16BIT_LITTLE_ENDIAN, webapis.microphone.MICROPHONE_SAMPLE_RATE_48000)`

Those constant names and the `enableDevice(format, sampleRate)`
signature are taken from the official API reference:

- `MicrophoneDeviceAudioFormat.MICROPHONE_FORMAT_SIGNED_16BIT_LITTLE_ENDIAN = 0`
- `MicrophoneSampleRateValue.MICROPHONE_SAMPLE_RATE_48000 = 48000` (“48 kHz, CD quality. Default format for audio input device.”)

An older Samsung “Creating a Microphone Application” tutorial used the
name `MICROPHONE_FRAMERATE_48000`. The probe falls back to that name
only if the current `MICROPHONE_SAMPLE_RATE_48000` constant is missing.

Every exception records `name`, `message`, and `code` (WebAPIException
uses `code`).

Privilege (same document): **Public** level,
`http://developer.samsung.com/privilege/microphone`, declared in a
packaged app's `config.xml` as:

```xml
<tizen:privilege name="http://developer.samsung.com/privilege/microphone" />
```

Without that privilege, methods raise `SecurityError`. A page opened in
the built-in Internet app is not a packaged widget, has no
`config.xml`, and typically cannot see `webapis` at all.

Separately, Samsung's app-registration guide warns that
`smartcontroller.microphone` is restricted to pre-consulted
applications (partner policy). That is a different privilege from the
Product Microphone API, but it is another reason not to assume the
far-field array is available to third-party software.

### 2. Standard browser capture

The page also tries:

- `navigator.mediaDevices.getUserMedia({audio: true})`
- legacy `navigator.getUserMedia` / `webkitGetUserMedia`
- `mediaDevices.enumerateDevices()`
- if a stream is returned, a Web Audio `AnalyserNode` for about three
  seconds of time-domain data, looking for a non-zero amplitude

`window.isSecureContext` is recorded. `getUserMedia` is specified to
work in a [secure context](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)
(HTTPS or localhost). A TV Internet app opening `http://<lan-ip>:8765`
is usually **not** a secure context, so this path is likely to fail
even if a microphone device exists.

## Expected outcome (before the real TV has been tested)

This has **not** been run on the QN75QN800D. The expected result, based
on the documents above, is:

| Path | Likely result in the Internet app |
| ---- | --------------------------------- |
| `$WEBAPIS/webapis/webapis.js` | Fails to load (`tizen_api: absent`) |
| `webapis.microphone.*` | Missing, or `SecurityError` if some firmware exposes a stub |
| `getUserMedia` over HTTP | Fail / insecure (`NotAllowedError` or API absent) |
| `getUserMedia` over trusted HTTPS | Possible in theory; still may be denied or have no device |
| Live non-zero amplitude | Unlikely |

Treat any other result from the real set as the source of truth.

## Decision rule

- If **no** path reports `live_audio: true`, the TV is **display-only**
  in the multi-node listening web. Keep using the hub PC microphone
  (voice agent), phones, and the Echo Show as ears.
- If a path *does* return live audio, note which one (`tizen_api` vs
  `getusermedia`), whether HTTPS was required, and only then consider a
  later packaged Tizen app or a browser-based TV listener. A packaged
  app would still need the microphone privilege and a Samsung
  certificate; that work is out of scope for this probe.
