# Shop firmware — the TWAB fleet

## Where your own values go (read this first)

Three files take values that are yours and nobody else's. Nothing else in this
tree needs editing to get a working button.

| File | What you enter | Where it comes from |
|---|---|---|
| `common/twab_secrets.h` (copy from `.example`) | `TWAB_LINK_KEY_HEX` — the shared HMAC key; `TWAB_ESPNOW_CHANNEL` | `python -c "import secrets; print(secrets.token_hex(32))"`; a channel at least 5 away from your WiFi AP's |
| `twab_button/main/button_config.h` | **`TWABB_MASTER_MAC`** — the master's MAC address | Printed on the master's serial console at boot: `==== twab master … MAC xx:xx:xx:xx:xx:xx …` |
| `twab_bridge/main/bridge_config.h` (copy from `.example`) | WiFi SSID + password, the bridge's static IP, and the Controller's `http://<ip>:8787` | Your network |

**Order matters:** build and flash the **master** first (it needs only the
secret), read its MAC from the boot banner, paste that MAC into the **button**'s
`button_config.h`, then build the **bridge** with your WiFi details. The
master and the bridge never need a MAC entered anywhere: the master learns
each node from its first authenticated frame, and the bridge talks to the master
over a UART cable, not radio.

The addresses in the `.example` files (`192.0.2.x`) are documentation
placeholders and will not route on any real network. Replace every one.

`twab_secrets.h`, `bridge_config.h`, `twab_config.h`, `sdkconfig`, `build/` and
`*.pem` are gitignored: they hold your key, your WiFi, and your generated build.
Keep them out of any fork you publish.

Four ESP-IDF projects. Three of them are the ESP-NOW fleet; the fourth is the
original single-chip button, kept because it still works.

```
   twab_button                twab_master              twab_bridge
   (battery, deep sleep)      (mains, no WiFi)         (mains, WiFi + OTA)
   ┌──────────────┐           ┌──────────────┐         ┌──────────────┐
   │ D0/GPIO1 ─┐  │  ESP-NOW  │              │  UART   │              │  HTTP
   │  pull-up  ├──┼──────────>│  verify HMAC ├────────>│  route+POST  ├──────>
   │  to GND   │  │  ch. 1    │  dedupe      │  NDJSON │  retry       │
   │           │  │<──────────┤  ack         │<────────┤  ack         │<──────
   │  LED ─────┘  │  ack x2   │              │         │  OTA         │
   └──────────────┘           └──────────────┘         └──────────────┘
                                     ^                          │
                              future WLED /                     v
                              sensor nodes            Shopcam 2000 Controller
                                                      POST /api/twab
                                                              │
                                                              v
                                                      Blue Iris `trigger` on
                                                      every overwatch camera
                                                      → last 60 s to disk
```

| Project | Board | Role |
|---|---|---|
| `twab_button/` | **XIAO ESP32S3**, battery | ESP-NOW node. Debounced GPIO, deep sleep, two-stage LED feedback. Still builds for the original ESP32 devkit — see below. |
| `twab_master/` | ESP32, mains | ESP-NOW receiver for the whole shop. No WiFi, no credentials. UART to the bridge. |
| `twab_bridge/` | ESP32, mains | WiFi client. UART from the master, HTTP to the Controller. **The only OTA-capable board.** |
| `common/twab_link/` | — | Shared wire format + HMAC auth. Built into the master and every node. |
| `twab/` | ESP32, battery | The original one-chip button (WiFi + HTTP directly). Superseded by the three above, still functional. |

## Why three boards instead of one

ESP-NOW and station mode share a single radio, and a station is pinned to its
AP's channel. Doing both on one chip means every ESP-NOW node has to track the
AP's channel — including after a router reboot picks a different one. That is
the failure mode where the button works for a month and then quietly doesn't,
which is the worst possible behaviour for a device whose entire job is "catch
the thing that just happened".

Splitting them costs one $4 board and buys:

- the ESP-NOW side owns a **fixed channel forever**, and nothing renegotiates it;
- the button and every future node hold **no WiFi credential** — the bridge is
  the only board on the LAN;
- press-to-trigger latency has no WiFi association in it (~10 ms of radio
  instead of 1–3 s of DHCP and DHCP-adjacent sadness);
- the master is a ~300-line program that never needs to change, so the board
  that can't be updated over the air is also the one that never needs it.

## Build order

You need ESP-IDF **v5.0 or newer** (the ESP-NOW receive callback signature
changed at 5.0, and the code compiles against the new one). Everything below is
in an "ESP-IDF PowerShell" on whichever PC has the toolchain installed.

### 0. One-time: the shared secret

```powershell
cd firmware\common
copy twab_secrets.h.example twab_secrets.h
python -c "import secrets; print(secrets.token_hex(32))"   # paste into TWAB_LINK_KEY_HEX
```

`twab_secrets.h` is gitignored and must be **identical on the master and every
node**. A node built with a different key is invisible: its frames fail the HMAC
check and the master logs `frame failed auth`. That is the first thing to check
when a new board "doesn't work".

Pick `TWAB_ESPNOW_CHANNEL` in the same file — at least 5 away from the shop AP's
channel (AP on 6 → use 1 or 11). The master and the bridge sit next to each
other with two live radios; same-channel puts ESP-NOW into contention with all
the LAN traffic and adjacent-channel is worse than either.

### 1. Master (flash first — you need its MAC)

```powershell
cd firmware\twab_master
idf.py set-target esp32
idf.py -p COMx flash monitor
```

At boot it prints the line everything else needs:

```
==== twab master a3f1  MAC 24:6f:28:aa:bb:cc  channel 1 ====
```

### 2. Button

The button is a **Seeed XIAO ESP32S3**. `button_config.h` carries pin blocks for
both it and the original ESP32-WROOM-32 devkit, selected by the build target, so
the old board stays flashable as a fallback.

```powershell
cd firmware\twab_button
# edit main\button_config.h: paste the master's MAC into TWABB_MASTER_MAC
Remove-Item sdkconfig, build -Recurse -Force   # see below — not optional
idf.py set-target esp32s3                      # or esp32 for the old devkit
idf.py -p COMx flash monitor
```

🔴 **Delete `sdkconfig` when you change target or touch `sdkconfig.defaults`.**
It is only consulted when `sdkconfig` does not exist, so otherwise you keep the
previous board's config — including `CONFIG_FREERTOS_HZ`, which is what made the
debounce silently stop debouncing on 2026-07-28. Confirm afterwards:

```powershell
Select-String sdkconfig -Pattern '^CONFIG_FREERTOS_HZ=|^CONFIG_IDF_TARGET='
```

⚠️ **The XIAO has no onboard antenna** — only the U.FL connector at the end
opposite the USB-C, and the antenna ships loose in the box. Unplugged, ESP-NOW
range collapses and it presents as the four-fast-blink "master down" pattern.
Plug it in before you conclude anything about the link.

⚠️ **The XIAO's USB console is the chip itself** — no CP2102. Deep sleep makes
the COM port vanish and re-enumerate on every wake, so `idf.py monitor` drops
each cycle. Bench with `TWABB_DEEP_SLEEP 0`. If flashing ever fails outright,
hold `B` (BOOT) and tap `R` (RESET) to force download mode.

Leaving `TWABB_MASTER_MAC` at all-zeros runs in broadcast mode. It works — it's
the right setting for a first power-up — but broadcast gets no link-layer
retries and no delivery callback, so set the MAC before the button goes on a
wall.

A new board means a new MAC and so a new node id — the master registers it on
its first authenticated frame with no pairing step, so nothing needs changing on
the master or the bridge.

### 3. Bridge

```powershell
cd firmware\twab_bridge
copy main\bridge_config.h.example main\bridge_config.h
# edit: WiFi creds, static IP, Controller address    (gitignored — NEVER commit)
copy ..\twab\secure_boot_signing_key.pem .          # reuse the existing OTA key
idf.py set-target esp32
idf.py -p COMx flash monitor
```

First flash must be over USB (a blank board has no OTA partition table). After
that: bump the version, `idf.py build` (signs automatically), and drop
`build/twab_bridge.bin` + a `version.txt` into the Controller's
`var/firmware/twab_bridge/`. The bridge checks hourly and on boot.

**Back up `secure_boot_signing_key.pem`.** Unsigned or foreign-signed images are
rejected; losing the key means the next bridge update needs a USB cable and a
ladder.

## Wiring

**Button** (XIAO ESP32S3) — two wires and nothing else:

```
D0 (GPIO 1) ──── momentary switch ──── GND
```

Internal pull-up, active low. No external resistor, no debounce cap; debounce is
5 samples × 5 ms in software. The internal pull-up is ~45 kΩ and loosely
specified (30–80 kΩ) — fine on a short run; if a long cable to a wall button
starts producing phantom wakes, an external 10 kΩ to 3V3 stops it.

**LED** — `D1 (GPIO 2) ──── LED (long leg) ──── 330 Ω ──── GND`, active high.
The onboard user LED is GPIO 21 and is **active low**; it needs no wiring and is
the easy choice for bench work.

**Battery** — a 1S LiPo on the `B+` / `B−` pads on the underside. Onboard charge
management (50 mA fast / 3.8 mA trickle) tops it up whenever USB-C is connected.
Solder a JST pigtail rather than the cell directly: the cell is always live, the
two pads are adjacent, and you will want it disconnectable for current
measurements. Use a **protected** cell — the charge IC manages charging, not
over-discharge.

Pin constraints if you move any of this: deep-sleep wake needs an RTC GPIO,
which on the S3 means GPIO 0–21, so `D0`–`D5` and `D8`–`D10` qualify and
`D6`/`D7` (GPIO 43/44, UART0) do not. `D2` (GPIO 3) is the only strapping pin
brought out to a pad. ADC1 is GPIO 1–10; ADC2 is unusable while WiFi is up.

There is **no battery-sense divider** on the XIAO and no net brought out for
one, so `TWABB_VBAT_ADC_GPIO` is `-1` and telemetry reports `vbat_mv: -1`. To
get a real reading, wire a divider into `D3` (GPIO 4, ADC1_CH3) and set it —
sized for microamps, because a 2×100 kΩ divider draws 18 µA, more than this
board's entire deep-sleep budget.

*(Original ESP32-WROOM-32 devkit, for the fallback build: button on GPIO 33, LED
on GPIO 2. RTC-capable pins there are 0, 2, 4, 12–15, 25–27, 32–39; avoid GPIO
12, a strapping pin that sets 1.8 V flash if held low at boot.)*

**Master ↔ bridge** — cross the UART and share a ground:

```
master TX (GPIO 17) ───── RX (GPIO 16) bridge
master RX (GPIO 16) ───── TX (GPIO 17) bridge
master GND          ───── GND          bridge
```

The shared ground is not optional. Without it the link works on the bench (both
boards grounded through the same laptop) and fails the moment they're on
separate supplies — which is the kind of bug that eats an evening.

## What the LED says

The button's LED is the whole user interface:

| Pattern | Meaning |
|---|---|
| 1 short blink | Master heard the press. |
| 1 long solid (~0.6 s) | **Saved.** The Controller triggered Blue Iris. |
| 2 medium blinks | Master heard it, but the save failed or never came back. |
| 4 fast blinks | Nobody answered — master down, out of range, or wrong key. |
| 3 blinks after a 3 s hold | Long-press announce (HELLO) acked. Use it to prove range from a new spot. |

## Protocol

**ESP-NOW frame** (`common/twab_link/include/twab_link.h`): 28-byte packed
header + ≤160-byte ASCII payload, authenticated with a truncated HMAC-SHA256
over the whole frame. Not encrypted — a sniffer can tell that someone pressed
the button, and cannot cause or replay a press. ESP-NOW's built-in crypto was
passed over because it caps at 6 encrypted peers on ESP32 and can't cover
broadcast, which would put a ceiling on a fleet that is meant to grow.

**UART** is newline-delimited JSON in both directions, because at 115200 with a
few frames a day nothing is gained by packing bytes and everything is gained by
being able to clip a USB-serial adapter on and read the traffic — or drive
either half by hand from a terminal while the other half is unplugged.

Master → bridge:

```json
{"t":"press","node":"a3f1","dev":"button","seq":12,"mac":"24:6f:28:aa:bb:cc","rssi":-61,
 "data":{"fw":"1.0.0","vbat_mv":3980,"boot":42,"press":17,"fail":0}}
{"t":"hb","role":"master","up_ms":91000,"peers":1,"forwarded":17,"dropped":0}
```

Bridge → master:

```json
{"t":"ack","node":"a3f1","seq":12,"ok":true,"code":200}
{"t":"cmd","node":"a3f1","data":{"anything":"you like"}}
```

Routing on the bridge: `press` → `/api/twab`, `telemetry` and `hello` →
`/api/twab/telemetry`, anything else → `/api/espnow/<type>`. The bridge re-wraps
the message with `source`, and a `bridge` block (its firmware, IP, RSSI,
uptime), so the Controller's log says where a press came in from.

Status codes follow the Controller's existing contract: **200** = at least one
camera triggered (retrying would re-trigger the ones that saved), **409** = no
overwatch cameras selected, **502** = nothing triggered and a retry is safe. The
bridge retries transport errors and 5xx; any 4xx is final.

**Retries reuse the same sequence number.** The master acks duplicates but
forwards only the first, so a lost ack costs one more radio frame and never a
second clip.

## Bridge status server

```
GET  http://<bridge-ip>/status
POST http://<bridge-ip>/cmd    {"node":"a3f1","data":{...}}
```

`/status` is the only way to see the link without a serial cable: firmware, IP,
RSSI, whether the master is still talking, POST counters, and how long ago the
last press was. `/cmd` hands a payload to the master, which puts it on the air —
nothing uses it yet, it's the hook the next section hangs off.

## Adding a WLED strip (or anything else) later

Nothing in the master or the bridge knows what a button is. To add a node:

1. Give it a number in `twab_dev_t` (`TWAB_DEV_LIGHT` is already there).
2. Flash it with the same `twab_secrets.h` and the same channel.
3. It sends `TWAB_MSG_HELLO` once and `TWAB_MSG_EVENT` whenever it likes; the
   master registers it on the first authenticated frame — no pairing step — and
   forwards everything to the bridge, which POSTs it to `/api/espnow/event`.
4. To *drive* it, POST to the bridge's `/cmd` with that node's id; the master
   unicasts a `TWAB_MSG_CMD` frame to it. `"node":"ffff"` broadcasts to all.

Two things to know before wiring lights in:

- **Stock WLED firmware doesn't speak this protocol.** Its own ESP-NOW support
  ("WLED Sync", the wireless remote) is a different, unauthenticated format. A
  strip joins this fleet either by running a small ESP-NOW→WLED-JSON shim on its
  ESP32, or by being on WiFi and driven by the Controller over HTTP like any
  other device. The shim is the interesting option and the reason the CMD path
  exists.
- **Turn off `TWAB_ESPNOW_LONG_RANGE` if any node isn't running this firmware.**
  LR mode is ESP32-to-ESP32 only and both ends must have it.

## Troubleshooting

| Symptom | Look at |
|---|---|
| Button: 4 fast blinks, always | **Antenna not plugged into the XIAO's U.FL connector** — check this first, it has no onboard antenna. Then: wrong `twab_secrets.h` key, wrong channel, or the master is down. Master logs `frame failed auth` for a key mismatch and says nothing at all for a channel mismatch. |
| Button's COM port keeps disappearing | Not a fault. The XIAO's USB console is the ESP32 itself, so deep sleep un-enumerates it. Build with `TWABB_DEEP_SLEEP 0` for bench work. |
| Timing behaves nothing like the constants say | `CONFIG_FREERTOS_HZ` reverted to 100. `sdkconfig.defaults` is ignored while `sdkconfig` exists — delete it and rebuild. |
| Button: 2 blinks, always | Master is fine; the bridge or the Controller isn't. Check `GET /status` on the bridge. |
| Master logs nothing, bridge warns "no traffic from master" | UART: TX/RX not crossed, or no shared ground. |
| Presses land twice | A node built from older firmware that increments `seq` per retry. Both ends must be current. |
| Bridge reboots in a loop after an update | Rollback working as designed — it couldn't reach the Controller. It will come back on the old image. |

## Blue Iris side

The firmware only guarantees the trigger *fires*. Whether it saves the previous
minute is a Blue Iris setting, and the default is 5 seconds: the pre-trigger
buffer is capped by the badly-named `movieroll` registry field (deciseconds,
default 50), not by `rectime` alone. Both need to be 600 for a 60-second
pre-roll. A perfectly working button with `movieroll` at its default saves five
seconds of the moment and none of the run-up.
