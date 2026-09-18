// twab_link — the wire format spoken over ESP-NOW by every node in the shop.
//
// One header, three consumers: the button (and any future ESP-NOW client)
// builds frames, the master verifies and forwards them, and both directions
// share the same authentication so a node cannot be spoofed by anything else
// on 2.4 GHz.
//
// Design constraints that shaped this:
//   * ESP-NOW caps a frame at 250 bytes (ESP_NOW_MAX_DATA_LEN), so the header
//     is packed and the payload is bounded.
//   * ESP-NOW's *built-in* encryption is limited to 6 encrypted peers on ESP32
//     and cannot cover broadcast, which would put a hard ceiling on a fleet
//     that is meant to grow (WLED strips, sensors). So the transport stays
//     unencrypted and authenticity is done here, at the application layer,
//     with a truncated HMAC-SHA256 over a shared key. Unlimited peers, works
//     with broadcast, and the key never leaves twab_secrets.h.
//   * Authenticated != confidential. A sniffer can read "someone pressed the
//     button". That is the correct trade for this threat model; what matters
//     is that a stranger cannot *cause* a trigger or replay one.

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TWAB_LINK_MAGIC        0x4157u   // 'WA' little-endian
#define TWAB_LINK_VERSION      1
#define TWAB_LINK_MAX_PAYLOAD  160
#define TWAB_LINK_TAG_LEN      8
#define TWAB_LINK_KEY_LEN      32
#define TWAB_LINK_MAX_NODES    16        // replay table depth

// Message types. Node -> master unless noted.
typedef enum {
    TWAB_MSG_HELLO     = 1,   // I exist: dev type, firmware, name
    TWAB_MSG_PRESS     = 2,   // THAT WAS AWESOME
    TWAB_MSG_TELEMETRY = 3,   // battery / rssi / counters
    TWAB_MSG_ACK       = 4,   // master -> node: result of an earlier frame
    TWAB_MSG_CMD       = 5,   // master -> node: generic command (future WLED)
    TWAB_MSG_EVENT     = 6,   // generic event, routed by payload
} twab_msg_t;

// Device classes. New kinds of node get a number here and nothing else in the
// master has to change — it forwards by type and the bridge routes by name.
typedef enum {
    TWAB_DEV_BUTTON = 1,
    TWAB_DEV_LIGHT  = 2,      // future: WLED strip controller
    TWAB_DEV_SENSOR = 3,
    TWAB_DEV_MASTER = 255,
} twab_dev_t;

// 28 bytes. Wire order is little-endian (both ends are Xtensa/RISC-V ESP32s;
// this is not a cross-architecture protocol and does not pretend to be).
typedef struct __attribute__((packed)) {
    uint16_t magic;
    uint8_t  version;
    uint8_t  msg;             // twab_msg_t
    uint8_t  dev;             // twab_dev_t
    uint8_t  payload_len;     // <= TWAB_LINK_MAX_PAYLOAD
    uint16_t node_id;         // stable per node (derived from its MAC)
    uint32_t session;         // random per boot — see twab_link_dedupe()
    uint32_t seq;             // increments per frame within a session
    uint32_t ref_seq;         // ACK: the seq being acknowledged, else 0
    uint8_t  tag[TWAB_LINK_TAG_LEN];  // HMAC-SHA256(key, frame with tag=0)[0:8]
} twab_hdr_t;

#define TWAB_LINK_HDR_LEN   ((size_t)sizeof(twab_hdr_t))
#define TWAB_LINK_MAX_FRAME (TWAB_LINK_HDR_LEN + TWAB_LINK_MAX_PAYLOAD)

// Install the shared secret. Must be called before build/verify; every node
// on the link uses the same key (TWAB_LINK_KEY_HEX in twab_secrets.h).
void twab_link_set_key(const uint8_t key[TWAB_LINK_KEY_LEN]);

// Parse 64 hex chars into a 32-byte key. Returns false on any non-hex char or
// wrong length — a silently-truncated key would authenticate nothing.
bool twab_link_key_from_hex(const char *hex, uint8_t out[TWAB_LINK_KEY_LEN]);

// Serialize a frame into `out`. `payload` may be NULL. Returns the frame
// length, or 0 if it would not fit.
size_t twab_link_build(uint8_t *out, size_t cap,
                       twab_msg_t msg, twab_dev_t dev,
                       uint16_t node_id, uint32_t session,
                       uint32_t seq, uint32_t ref_seq,
                       const char *payload);

// Check magic, version, declared length and HMAC. Constant-time tag compare.
bool twab_link_verify(const uint8_t *buf, size_t len);

// Views into a frame that already passed twab_link_verify().
const twab_hdr_t *twab_link_hdr(const uint8_t *buf);
// Payload is NOT NUL-terminated on the wire; `out_len` gives its length.
const char *twab_link_payload(const uint8_t *buf, size_t *out_len);

// Replay / duplicate suppression, keyed on (node_id, session, seq).
//
// Returns true if this frame is fresh and should be acted on, false if it is a
// repeat. A node that reboots picks a new random `session`, which resets the
// counter for that node — the honest cost of not having persistent state on a
// battery device. It stops the case that actually happens (ESP-NOW retries and
// a captured frame replayed in the same session), not a determined attacker
// with a recording and a power-cycle.
bool twab_link_dedupe(uint16_t node_id, uint32_t session, uint32_t seq);

// Lowercase names for logging and for the UART/JSON layer.
const char *twab_msg_name(uint8_t msg);
const char *twab_dev_name(uint8_t dev);

// Stable 16-bit id from the low three MAC bytes — the same value the device
// prints at boot, so a node in a log can be matched to a board on the bench.
uint16_t twab_node_id_from_mac(const uint8_t mac[6]);

// "aa:bb:cc:dd:ee:ff" into `out` (>= 18 bytes).
void twab_link_mac_str(const uint8_t mac[6], char *out, size_t cap);
// Inverse; returns false unless all six octets parse.
bool twab_link_mac_parse(const char *str, uint8_t out[6]);

#ifdef __cplusplus
}
#endif
