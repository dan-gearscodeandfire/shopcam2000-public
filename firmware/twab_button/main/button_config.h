// twab_button — pins and behaviour. No secrets here (the shared link key and
// the channel live in firmware/common/twab_secrets.h); this file is committed.
#pragma once

// Two boards are supported. The pins differ, nothing else does, so the whole
// difference is the block below and `idf.py set-target`.
//
//   esp32s3  — Seeed XIAO ESP32S3. The current button. ~14 uA in deep sleep
//              because there is no USB-serial chip and the regulator is a
//              2.3 uA-quiescent buck (SGM6029) instead of a 5 mA LDO.
//   esp32    — the original ESP32-WROOM-32 devkit, node d5e8. Kept buildable
//              as a fallback: it works, it just costs 4-20 mA asleep, which is
//              the CP2102 and the AMS1117, not the chip.

#if CONFIG_IDF_TARGET_ESP32S3

// ---- XIAO ESP32S3 -----------------------------------------------------------
// Pad map, USB-C at the top:
//   D0..D6   = GPIO 1,2,3,4,5,6,43      D7..D10 = GPIO 44,7,8,9
//   plus 5V(VUSB), GND, 3V3, and BAT +/- pads on the underside.
//
// Constraints that picked these pins:
//   - deep-sleep wake (ext0) needs an RTC GPIO: on the S3 that is GPIO 0-21,
//     so D0-D5 and D8-D10 qualify and D6/D7 (GPIO 43/44, UART0) do NOT.
//   - GPIO 3 (D2) is the only strapping pin brought out to a pad. Avoided.
//   - ADC1 is GPIO 1-10; ADC2 is unusable while WiFi is up.
//
// 🔴 The XIAO has NO onboard antenna — only the U.FL connector at the end
// opposite the USB-C. Unplugged, ESP-NOW range collapses and it presents as
// the four-fast-blink "master down" pattern. Antenna first, always.
#define TWABB_BOARD           "XIAO ESP32S3 (button D0, LED D1)"
#define TWABB_BUTTON_GPIO     GPIO_NUM_1    // D0
#define TWABB_LED_GPIO        GPIO_NUM_2    // D1  (-1 to disable)
#define TWABB_LED_ACTIVE_LOW  0             // external LED to GND, active high
// The onboard user LED is GPIO 21 and is ACTIVE LOW — swap both lines above to
// GPIO_NUM_21 / 1 to use it for bench work without wiring anything.

// No battery divider. Seeed brings out no battery-sense net and there is no
// onboard divider, so there is nothing to read: the old GPIO 34 setting read a
// floating pin and reported noise (0, 130, 712 mV). Disabled until a divider
// exists — wire one into D3 (GPIO 4, ADC1_CH3) and set it here.
// ⚠️ Size it for microamps: a 2x100k divider draws 18 uA, which alone is more
// than this board's entire deep-sleep budget.
#define TWABB_VBAT_ADC_GPIO   -1
#define TWABB_VBAT_DIVIDER    2.0f

#else

// ---- ESP32-WROOM-32 devkit (the original button, node d5e8) ------------------
// RTC-capable GPIOs on the classic ESP32 are 0, 2, 4, 12-15, 25-27, 32-39.
// GPIO 12 is a strapping pin (held low at boot = 1.8 V flash); don't use it.
#define TWABB_BOARD           "ESP32-WROOM-32 devkit"
#define TWABB_BUTTON_GPIO     GPIO_NUM_33
#define TWABB_LED_GPIO        GPIO_NUM_2    // -1 to disable
#define TWABB_LED_ACTIVE_LOW  0
// Divider from VBAT into an ADC1 pin. Never actually wired on this board.
#define TWABB_VBAT_ADC_GPIO   GPIO_NUM_34
#define TWABB_VBAT_DIVIDER    2.0f

#endif

// ---- Button ------------------------------------------------------------------
// Momentary switch from the GPIO to GND, using the INTERNAL pull-up: idle reads
// high, pressed reads low. Two wires, no external resistor, no cap.
//
// The internal pull-up is ~45k and loosely specified (30-80k). Fine on a short
// run; if a long cable to a wall button starts producing phantom wakes, add an
// external 10k to 3V3 and it stops.
#define TWABB_ACTIVE_LOW      1

// Debounce: N consecutive samples TWABB_SAMPLE_MS apart must agree before a
// level change counts. 5 x 5 ms = 25 ms, which is longer than any switch
// bounces and shorter than any human notices.
// 🔴 This only works because CONFIG_FREERTOS_HZ=1000. At the IDF default of 100
// one tick is 10 ms, pdMS_TO_TICKS(5) rounds to ZERO, and this debounces
// nothing at all. See sdkconfig.defaults.
#define TWABB_SAMPLE_MS       5
#define TWABB_DEBOUNCE_SAMPLES 5
// Ignore a second press inside this window: one enthusiastic double-tap should
// save one moment, not two.
#define TWABB_REPRESS_LOCKOUT_MS 1500

// How long to wait for the button to come back up before deciding it is stuck.
// Longer than any deliberate hold (the 3 s long-press gesture included) and
// short enough that a jammed switch is noticed the same evening.
#define TWABB_RELEASE_TIMEOUT_MS 30000
// When it IS stuck, sleep on the timer alone and re-check this often. Arming
// ext0 against a pin that is already low wakes the chip the instant it sleeps,
// which is a press loop with extra steps.
#define TWABB_STUCK_RECHECK_S    60

// ---- Master ------------------------------------------------------------------
// The master prints its MAC at boot ("==== twab master ... MAC ... ===="). Put
// it here: unicast ESP-NOW gets link-layer retries and a delivery callback,
// broadcast gets neither.
// Leave as all-zeros to run in broadcast mode — it works, and it is the right
// setting for the very first power-up before you know the MAC.
//
// >>> YOUR MAC GOES HERE <<<  Flash twab_master first, read the MAC off its
// boot banner, and paste it between the quotes, e.g. "24:6f:28:aa:bb:cc".
// The button's own MAC (and so its node id) needs no entry anywhere: the master
// adopts a new node on its first authenticated frame, with no pairing step.
#define TWABB_MASTER_MAC      "00:00:00:00:00:00"

// ---- Power ------------------------------------------------------------------
// 1 = deep sleep between presses (battery). 0 = always awake (USB power);
// easier to bench-test and the only mode that can respond to a CMD frame.
// ⚠️ On the S3 the USB console is the chip itself, so deep sleep makes the COM
// port vanish and re-enumerate on every wake.
//
// Don't edit this to bench-test — build with TWABB_BENCH=1 in the environment
// and main/CMakeLists.txt defines it to 0 for you. An edit you have to remember
// to put back is an edit that ships.
#ifndef TWABB_DEEP_SLEEP
#define TWABB_DEEP_SLEEP      1
#endif
// Timer wake for a telemetry heartbeat (battery, counters). 0 to disable.
#define TWABB_HEARTBEAT_HOURS 12

// ---- Feedback ---------------------------------------------------------------
// The LED is the entire user interface, so the vocabulary matters:
//   1 short blink        sent, master heard it
//   1 long solid         saved — the Controller triggered the cameras
//   2 medium blinks      master heard it, but the save failed or never confirmed
//   4 fast blinks        nobody answered at all (master down / out of range /
//                        antenna not plugged in)
// Wiring: GPIO -> LED (long leg) -> 330 ohm -> GND.

// ---- Timing ------------------------------------------------------------------
#define TWABB_LINK_ACK_MS     400    // wait for the master's "heard you"
// ⚠️ The one press-to-verdict measurement we have is 765 ms — but that was with
// a SINGLE camera on Watch. All nine are on Watch now and the Controller
// triggers them sequentially under a lock, so the real ceiling is unmeasured.
// This is the largest consumer of awake time per press. Re-measure before
// cutting it; don't guess it down.
#define TWABB_RESULT_ACK_MS   9000   // then for the Controller's verdict
#define TWABB_SEND_RETRIES    3
// Hold this long and the button announces itself instead (HELLO): use it to
// register a new node with the master and to prove range from a new spot.
#define TWABB_LONG_PRESS_MS   3000
