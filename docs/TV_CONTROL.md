# Samsung TV control (phase 2 stubs): power and input

Scope: `jarvis/tv/samsung.py` (`SamsungTVController`) and `jarvis/tv/wol.py`
(Wake-on-LAN). They give you **power off, power on, and input switching** for the
QN75QN800DFXZA through the TV's WebSocket remote API using
[`samsungtvws`](https://pypi.org/project/samsungtvws/). They are **stubs in the sense
that nothing here has been run against the real TV yet**: the code is tested with a
mocked library, and the checklist at the bottom lists what must be confirmed on
hardware. Playback of library files stays with the `samsung-tv-root` hook; named
inputs, app launching, scheduled power rules and voice intents are phase 3
([PHASE3_DESIGN.md](PHASE3_DESIGN.md)).

## TV settings (do this first)

On the TV: **Settings > General > Network > Expert Settings**

1. **Power On with Mobile**: **On**. Without it the TV ignores the Wake-on-LAN packet in standby.
2. **IP Remote**: **On**. Without it the WebSocket remote API on port 8002 is not served.

Also:

- **Use Ethernet** for the TV if you can. A TV on Wi-Fi often drops the Wi-Fi radio when it
  is "off", so Wake-on-LAN becomes unreliable; wired standby is dependable.
- Give the TV a DHCP reservation (like the hub) so `tv.ip` never changes.
- Put the hub and the TV on the same subnet. Wake-on-LAN is a broadcast and does not cross routers
  unless you set `tv.broadcast` to a directed broadcast such as `192.168.1.255`.

Find the TV's MAC in **Settings > General > Network > Network Status > IP Settings** (or **Support >
About This TV**). Use the wired MAC if it is connected by Ethernet.

## Configure

In `config.json`:

```json
"tv": {
  "controller": "samsungtvws",
  "ip": "192.168.1.60",
  "mac": "aa:bb:cc:dd:ee:ff",
  "token_file": "data/tv/samsung_token.txt",
  "port": 8002,
  "name": "JarvisHub",
  "broadcast": "255.255.255.255",
  "controller_options": {
    "direct_hdmi_keys": false,
    "playback": {}
  }
}
```

| Key | Meaning |
| --- | ------- |
| `tv.controller` | `samsungtvws` enables this controller. `null` (default) keeps everything off. |
| `tv.ip` | TV address. Required for off and input. If empty the controller reports "not set" and does nothing. |
| `tv.mac` | TV MAC. Required for power on (Wake-on-LAN). |
| `tv.token_file` | Where the library stores the pairing token (relative to the project folder). |
| `tv.port` | `8002` = secure WebSocket (wss). Use this on 2024 sets. |
| `tv.broadcast` | Wake-on-LAN broadcast address. |
| `tv.controller_options.direct_hdmi_keys` | `true` allows `hdmi1`..`hdmi4` (KEY_HDMI1..4). Off by default, see below. |
| `tv.controller_options.playback` | Optional `{ "command": ..., "endpoint": ... }` samsung-tv-root hook, so playback still works while this controller handles power/input. |

Install the library into the project venv, then pair once:

```powershell
cd C:\JarvisHub
.\.venv\Scripts\python.exe -m pip install -r requirements-tv.txt
Stop-ScheduledTask JarvisHub; Start-ScheduledTask JarvisHub     # hub reads config at start
.\.venv\Scripts\python.exe -m jarvis.tv.samsung --pair      # accept "Allow" on the TV with the remote
```

The token is saved in `data\tv\samsung_token.txt` and reused. If the TV says "Allow" again or the token
is rejected, delete that file and pair again.

## Try it from the command line

```powershell
.\.venv\Scripts\python.exe -m jarvis.tv.samsung --off               # KEY_POWER
.\.venv\Scripts\python.exe -m jarvis.tv.samsung --on                # Wake-on-LAN packet to tv.mac
.\.venv\Scripts\python.exe -m jarvis.tv.samsung --input next        # KEY_HDMI: cycle inputs
.\.venv\Scripts\python.exe -m jarvis.tv.samsung --input source      # KEY_SOURCE: open the source menu
.\.venv\Scripts\python.exe -m jarvis.tv.samsung --key KEY_MUTE      # any remote key
```

Each prints a JSON result and exits non-zero on failure.

## HTTP API

Disabled (HTTP 501 with an explanation) unless `tv.controller` is `samsungtvws`. Same auth as the other
`/api` routes (`X-Hub-Token` or `?token=` when tokens are configured).

```powershell
Invoke-RestMethod http://localhost:8765/api/tv/status
Invoke-RestMethod -Method Post http://localhost:8765/api/tv/power -ContentType application/json -Body '{"action":"on"}'
Invoke-RestMethod -Method Post http://localhost:8765/api/tv/power -ContentType application/json -Body '{"action":"off"}'
Invoke-RestMethod -Method Post http://localhost:8765/api/tv/input -ContentType application/json -Body '{"input":"next","times":1}'
```

`input` is `next` (alias `hdmi`, `cycle`), `source` (opens the source menu), or `hdmi1`..`hdmi4` only when
`direct_hdmi_keys` is true. Failures return HTTP 502 with `error` and a `hint`.

## Why power on is Wake-on-LAN and input is a cycle

- **Power on**: `KEY_POWER` over the WebSocket cannot wake a TV in standby, because the TV is not serving
  port 8002 then. A magic packet (6 x `FF` + the MAC 16 times, UDP broadcast, port 9, sent three times) is
  what wakes it. `jarvis/tv/wol.py` builds and sends it with the standard library only.
- **Power off**: `KEY_POWER` toggles, so it turns the TV off when it is on. If the TV is already off it
  fails to connect and the API returns an error instead of waking it.
- **Input**: on 2024 Samsung models the direct keys `KEY_HDMI1`..`KEY_HDMI4` are unreliable (they often do
  nothing or open a menu). The dependable commands are `KEY_HDMI` (jumps to the next input) and `KEY_SOURCE`
  (opens the source list). Cycling is not "go to HDMI 2", so reaching a named input is phase 3 (calibrate
  the cycle order once on the QN75QN800D).

## Not verified without the real TV

See the checklist in [STATUS.md](STATUS.md). In short: pairing token prompt, `KEY_POWER` off, wake from
standby on Ethernet and on Wi-Fi, what `KEY_HDMI` and `KEY_SOURCE` do on this firmware, and whether
`KEY_HDMI1`..`4` do anything.
