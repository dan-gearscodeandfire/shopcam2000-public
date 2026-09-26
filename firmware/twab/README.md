# TWAB - the "That Was Awesome" button

A battery-powered ESP32 that lives in deep sleep. Press it and it wakes, joins
WiFi, tells the Shopcam 2000 Controller to save the last minute from every
**overwatch** camera (the per-camera "Watch" toggle in the Controller UI -
independent of arming), and goes back to sleep. Designed for the world where
overwatch cameras hold a 1-minute pre-trigger buffer in Blue Iris (Mode B in
`docs/` / the vault's `blue-iris-two-mode-recording-setup`).

## Behaviour

| Wake | Trigger | What happens |
|---|---|---|
| Button tap | GPIO EXT1, any-high | POST `/api/twab` with telemetry, sleep. |
| Button hold ≥3 s | same, still held after boot | **Maintenance mode**: stays awake 10 min, mirrors logs over UDP, checks for + applies OTA updates. |
| Timer (every 6 h) | heartbeat | POST `/api/twab/telemetry` (battery, RSSI, counters), then a quiet OTA check. This is how firmware rolls out with no hands. |

Every wake reports: `device`, `fw` version, `wake` reason, `boot_count`,
`press_count`, `fail_count`, `vbat_mv`, `rssi`, `awake_ms`. The server log *is*
the monitor.

## Server contract (implemented in `server.py`)

- `POST /api/twab` - save-the-moment. Fires the Blue Iris `trigger` command
  on every **overwatch** camera (flushes each 60 s pre-roll into a clip;
  Break time = post-roll) and logs the telemetry body. Status codes are the
  firmware's contract: **200** = at least one camera triggered (including
  partial failure - retrying would re-trigger the cameras that saved; the
  body's `failures` map tells the truth); **409** = no overwatch cameras
  selected (config state - the firmware treats 4xx as final, no retry);
  **502** = cameras selected but nothing triggered (safe to retry).
- `POST /api/twab/telemetry` - log-only heartbeat. 200 = success.
- `GET /firmware/twab/version.txt` - plain-text version string (e.g. `1.0.1`).
  Compared against the running image's `esp_app_desc` version.
- `GET /firmware/twab/twab.bin` - the OTA binary (must be signed; see
  Build). The server serves `/firmware/*` from `var/firmware/`, so
  publishing an update = dropping both files in `var/firmware/twab/`.

## Build (on ok-llm-box, ESP-IDF already installed)

```powershell
# in an "ESP-IDF PowerShell" (or after . .\export.ps1 in the IDF dir)
cd firmware\twab
copy main\twab_config.h.example main\twab_config.h
# edit main\twab_config.h: WiFi creds, server IP, pins  (gitignored — NEVER commit)
# one-time: generate the OTA signing key (gitignored via *.pem — BACK IT UP;
# OTA images are rejected unless signed with it, and losing it means the
# next update needs a USB reflash)
espsecure.py generate_signing_key --version 1 secure_boot_signing_key.pem
idf.py set-target esp32
idf.py build
```

First flash must be over USB (no OTA partition table on a blank board):

```powershell
idf.py -p COMx flash monitor
```

After that, updates go over the air: bump `version` (CMake `project(twab)`
takes it from git describe by default, or set `CONFIG_APP_PROJECT_VER`),
`idf.py build` (signs automatically with the key above), then copy
`build/twab.bin` + a `version.txt` into the Controller's `var/firmware/twab/`
and either wait for a heartbeat (≤6 h) or hold the button 3 s. The device
verifies the signature before booting the new image; an unsigned or
foreign-signed binary is rejected.

## Wiring

- **Button**: momentary switch from `TWAB_BUTTON_GPIO` (default GPIO 33) to
  3V3, with a ~100 k pulldown to GND. Must be an RTC-capable GPIO
  (0, 2, 4, 12–15, 25–27, 32–39) for deep-sleep wake.
- **LED** (optional): GPIO 2, lights while awake/posting.
- **Battery sense** (optional): divider (e.g. 2×100 k for 2:1) from VBAT to
  GPIO 34 (ADC1). Set `TWAB_VBAT_DIVIDER` to the ratio.

## Debug surfaces

- **UDP log mirror**: while awake, all `esp_log` output is also sent to the
  server host, UDP port 5514. Listen with `nc -ul 5514` (or a tiny listener in
  the Controller). Deep sleep means there is nothing to connect to otherwise -
  the device is off between presses.
- **Telemetry counters**: `boot_count`/`press_count`/`fail_count` live in RTC
  RAM and survive sleep (reset on power loss). A rising `fail_count` with a
  healthy server means WiFi trouble; presses with no server log entry mean the
  press never left the device.
- **OTA rollback**: `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE` - a new image must
  complete one successful POST before it's marked valid; otherwise the
  bootloader falls back to the previous slot on reboot. A bad OTA cannot brick
  the button.

## Design notes

- Static IP by default: DHCP costs ~1 s of battery every press.
- WiFi config kept in RAM (`WIFI_STORAGE_RAM`) - no NVS flash wear per wake.
- The h264/mjpeg/NVENC bridge work is unrelated to this device; TWAB only ever
  speaks HTTP to the Controller, never to Blue Iris directly, so BI credentials
  stay off the microcontroller.
