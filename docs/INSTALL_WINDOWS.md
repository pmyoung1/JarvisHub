# Install on the Windows Hub PC (phase 2)

Upgrade an existing phase 1 install at `C:\JarvisHub`, or do a fresh install.
Everything below is copy-paste PowerShell. The scheduled task is named
**`JarvisHub`** (see `scripts\install_service.ps1`).

You need: Windows 10/11, **Python 3.10-3.13** from python.org with the `py`
launcher (Windows x64 wheels exist for every package on those versions), and
internet for the one-time model downloads. After that the listener, speech
recognition and talkback run offline.

The zip is `JarvisHub-phase2.zip`. Its files sit at the zip root, so extracting
it into `C:\JarvisHub` puts `README.md`, `jarvis\`, `scripts\` directly there.
It never contains `config.json`, `data\`, `.venv\`, or `certs\*.pem`, so
`Expand-Archive -Force` cannot overwrite your settings, index, or certificates.
(Phase 1 files that phase 2 removed or renamed are not deleted; none were.)

## A. Admin PowerShell: replace the service

Open **PowerShell as Administrator**, then paste each block. Change `$Zip` if
you saved the download somewhere else.

```powershell
# A1. Where things are
Set-Location C:\JarvisHub
$Zip = "$env:USERPROFILE\Downloads\JarvisHub-phase2.zip"
Test-Path $Zip     # must print True

# A2. Remove the phase 1 boot task (this also removes the old firewall rule; step A8 re-adds it)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\uninstall_service.ps1

# A3. Stop any Jarvis Python still running (unregistering a task does not stop its process)
Get-ScheduledTask -TaskName JarvisHubVoice -ErrorAction SilentlyContinue | Stop-ScheduledTask
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -match '^(python|pythonw|pyw|py)\.exe$' -and $_.CommandLine -match 'jarvis\.(launcher|server|voice)|wakeword\.py' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "Stopped PID $($_.ProcessId)" }

# A4. Back up your config, then extract the zip over the folder
if (Test-Path .\config.json) { Copy-Item .\config.json ".\config.backup-$(Get-Date -Format yyyyMMdd-HHmmss).json" }
Expand-Archive -Path $Zip -DestinationPath C:\JarvisHub -Force

# A5. First install only: create config.json (an existing one is kept; new keys fall back to defaults)
if (-not (Test-Path .\config.json)) { Copy-Item .\config.example.json .\config.json }
Start-Process notepad .\config.json -Wait     # set media_paths (e.g. "D:\\Movies"), save, close Notepad

# A6. Python environment (the hub itself is stdlib-only; the .venv is for voice, talkback, TV control)
#     Use 'py -3.12' (or 3.11/3.10) instead of 'py -3' if your default Python is not 3.10-3.13.
if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-wakeword.txt -r requirements-tv.txt
```

Want the full voice agent with speaker verification instead of the lightweight
listener? Also run
`.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt`
(it adds torch and SpeechBrain, several GB). See "Which listener" in the README.

```powershell
# A7. One-time downloads (needs internet; offline afterwards)
#  - the openWakeWord models (includes "hey jarvis")
#  - the speech-to-text model (faster-whisper "small", ~0.5 GB)
.\.venv\Scripts\python.exe -c "import openwakeword; openwakeword.utils.download_models()"
.\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('stt model ready')"

#  - a Piper talkback voice: en_US "lessac" (medium), the config default, ~63 MB
#    Browse: https://huggingface.co/rhasspy/piper-voices/tree/main/en/en_US/lessac/medium
$ProgressPreference = 'SilentlyContinue'
$Voices = "C:\JarvisHub\data\voices"
New-Item -ItemType Directory -Force -Path $Voices | Out-Null
$Base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
Invoke-WebRequest "$Base/en_US-lessac-medium.onnx"      -OutFile "$Voices\en_US-lessac-medium.onnx"
Invoke-WebRequest "$Base/en_US-lessac-medium.onnx.json" -OutFile "$Voices\en_US-lessac-medium.onnx.json"
Get-ChildItem $Voices
```

The voice files go in **`C:\JarvisHub\data\voices\`** (config `voice.tts_voices_dir`,
default `data/voices`, with `voice.tts_voice` = `en_US-lessac-medium`). To use a voice
stored elsewhere, set `voice.tts_model_path` in `config.json` to the full `.onnx` path
(the matching `.onnx.json` must sit next to it). Any other model from
[rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices/tree/main) works the
same way, for example `en/en_US/amy/medium/en_US-amy-medium.onnx`.

```powershell
# A8. Re-install the boot task. It now prefers .venv\Scripts\pythonw.exe so the hub can use Piper for /api/tts.
#     Add -User "$env:USERDOMAIN\$env:USERNAME" if your movies are on a drive SYSTEM cannot read.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_service.ps1

# A9. Start it and check it
Start-ScheduledTask -TaskName JarvisHub
Start-Sleep -Seconds 4
Invoke-RestMethod http://localhost:8765/api/health
(Invoke-RestMethod http://localhost:8765/api/status).tts      # piper_package / piper_voice_present should be True
```

If the hub is running as SYSTEM and the task will not start with the `.venv`
Python, Python was probably installed "for this user only" under `%LOCALAPPDATA%`,
which SYSTEM cannot read. Re-run A8 with `-User "$env:USERDOMAIN\$env:USERNAME"`
(it asks for your password so Windows can start the task with nobody logged in), or
reinstall Python "for all users".

Quick talkback test from the admin shell (writes a WAV and plays it):

```powershell
Invoke-WebRequest "http://localhost:8765/api/tts?text=Hello%20Pat,%20Jarvis%20is%20online" -OutFile "$env:TEMP\jarvis-tts.wav"
(New-Object Media.SoundPlayer "$env:TEMP\jarvis-tts.wav").PlaySync()
```

## B. Normal (non-admin) PowerShell: the wake word listener

The microphone is only available in your **logged-in session**; Windows gives the
SYSTEM boot task (Session 0) no microphone. So the listener starts at your logon.
Open a **normal** PowerShell (not "as administrator") in the same Windows account
you use at the Hub PC:

```powershell
Set-Location C:\JarvisHub

# B1. See your microphone (note the index of the one you want)
.\.venv\Scripts\python.exe scripts\wakeword.py --list-devices
#     If the default is wrong, put the index in config.json: "voice": { "input_device": "3" }

# B2. Test in this window. Say "hey jarvis", then "what time is it". Ctrl+C to stop.
#     --dry-run prints what it heard and sends nothing to the hub.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_wakeword.ps1 -Foreground --dry-run

# B3. Same, but really sending commands to the hub
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_wakeword.ps1 -Foreground

# B4. Start it automatically at every logon (adds a shortcut in shell:startup), and start it now
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_wakeword_startup.ps1
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','C:\JarvisHub\scripts\run_wakeword.ps1'

# B5. Check it
Get-Content C:\JarvisHub\logs\wakeword.log -Tail 20
Invoke-RestMethod http://localhost:8765/api/voice/status
```

(You can also open the Startup folder yourself with `shell:startup` and see
`Jarvis Wakeword.lnk` there.) If you set `auth_token` or `auth_tokens.voice`
in `config.json`, the listener reads it from the config automatically; or pass
`--token`. Remove it later with
`.\scripts\uninstall_wakeword_startup.ps1 -StopRunning`.

### Optional: the full voice agent (speaker verification) instead

Only if you installed `requirements-voice.txt`. Pick **one** listener; they share a
lock, so a second one exits instead of answering twice.

```powershell
.\scripts\uninstall_wakeword_startup.ps1 -StopRunning
.\scripts\enroll_voice.ps1                       # records 3 clips of your voice
.\scripts\install_voice_agent.ps1                # At-logon task "JarvisHubVoice"
Start-ScheduledTask -TaskName JarvisHubVoice
```

To go back: `.\scripts\uninstall_voice_agent.ps1`, then
`.\scripts\install_wakeword_startup.ps1`. (`install_wakeword_startup.ps1
-ReplaceVoiceAgent` disables the voice-agent task for you.)

## C. Phones, the TV, and the orbs

1. Phone: open `http://<hub-ip>:8765`. Tap **Talkback: off** once to turn it on (browsers
   need a tap before they allow sound); replies to commands you send from that phone are
   spoken through Piper and the orange orb pulses. Tap **Mic orb** to show your own voice
   level (blue orb); this needs HTTPS or localhost, otherwise the blue orb follows the hub
   microphone instead.
2. TV: open `http://<hub-ip>:8765/tv`. The orbs and a **Talkback** tile are on the page.
3. TV mic check: `http://<hub-ip>:8765/tv/mic-probe`, then
   `Invoke-RestMethod http://localhost:8765/api/health` and read `mic_probe`.
4. TV power and input (optional): see [TV_CONTROL.md](TV_CONTROL.md). Short version: turn on
   **Power On with Mobile** and **IP Remote** on the TV, set `tv.controller` to
   `samsungtvws` plus `tv.ip`, `tv.mac`, restart the task, then
   `.\.venv\Scripts\python.exe -m jarvis.tv.samsung --pair` and accept the prompt on the TV.

## D. Troubleshooting

| Symptom | Fix |
| ------- | --- |
| `running scripts is disabled on this system` | Use the `-ExecutionPolicy Bypass` form shown above, or `Unblock-File .\scripts\*.ps1`. |
| `.venv not found` from `run_wakeword.ps1` | Re-run step A6. |
| Listener log: `Wake word engine unavailable` | `.\.venv\Scripts\python.exe -m pip install -r requirements-wakeword.txt`, then A7. |
| Listener log: `Another Jarvis microphone listener ... already running` | Only one of the wake-word listener and the voice agent may run. Stop the other. |
| `cublas64_12.dll not found` | The listener falls back to CPU by itself. To skip the retry, set `"stt_device": "cpu"` under `voice` in `config.json`. |
| `GET /api/tts` returns 503 | Read the `error` text. Usually the Piper voice files are not in `data\voices`, or the task is not using the `.venv` Python (re-run A8). |
| The task shows Running but nothing answers | `Get-Content C:\JarvisHub\data\logs\hub.log -Tail 40` |
