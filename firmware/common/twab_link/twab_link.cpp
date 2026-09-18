#include "twab_link.h"

#include <string.h>
#include <stdio.h>

#include "mbedtls/md.h"

// ---------------------------------------------------------------------------
// Key + HMAC
// ---------------------------------------------------------------------------

static uint8_t s_key[TWAB_LINK_KEY_LEN];
static bool s_have_key = false;

void twab_link_set_key(const uint8_t key[TWAB_LINK_KEY_LEN])
{
    memcpy(s_key, key, TWAB_LINK_KEY_LEN);
    s_have_key = true;
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool twab_link_key_from_hex(const char *hex, uint8_t out[TWAB_LINK_KEY_LEN])
{
    if (!hex || strlen(hex) != TWAB_LINK_KEY_LEN * 2) return false;
    for (size_t i = 0; i < TWAB_LINK_KEY_LEN; i++) {
        int hi = hex_nibble(hex[i * 2]);
        int lo = hex_nibble(hex[i * 2 + 1]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return true;
}

// HMAC-SHA256 over the whole frame with the tag field zeroed, truncated to 8
// bytes. Covering the header (not just the payload) is what stops an attacker
// editing msg/seq/node_id on a captured frame.
static void compute_tag(const uint8_t *frame, size_t len, uint8_t out[TWAB_LINK_TAG_LEN])
{
    memset(out, 0, TWAB_LINK_TAG_LEN);
    if (!s_have_key || len < TWAB_LINK_HDR_LEN) return;

    const size_t tag_off = offsetof(twab_hdr_t, tag);
    static const uint8_t zeros[TWAB_LINK_TAG_LEN] = {0};

    const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_md_context_t ctx;
    mbedtls_md_init(&ctx);
    if (mbedtls_md_setup(&ctx, info, 1) == 0 &&
        mbedtls_md_hmac_starts(&ctx, s_key, TWAB_LINK_KEY_LEN) == 0) {
        mbedtls_md_hmac_update(&ctx, frame, tag_off);
        mbedtls_md_hmac_update(&ctx, zeros, TWAB_LINK_TAG_LEN);
        mbedtls_md_hmac_update(&ctx, frame + tag_off + TWAB_LINK_TAG_LEN,
                               len - tag_off - TWAB_LINK_TAG_LEN);
        uint8_t full[32];
        if (mbedtls_md_hmac_finish(&ctx, full) == 0) {
            memcpy(out, full, TWAB_LINK_TAG_LEN);
        }
    }
    mbedtls_md_free(&ctx);
}

// ---------------------------------------------------------------------------
// Build / verify
// ---------------------------------------------------------------------------

size_t twab_link_build(uint8_t *out, size_t cap,
                       twab_msg_t msg, twab_dev_t dev,
                       uint16_t node_id, uint32_t session,
                       uint32_t seq, uint32_t ref_seq,
                       const char *payload)
{
    size_t plen = payload ? strlen(payload) : 0;
    if (plen > TWAB_LINK_MAX_PAYLOAD) return 0;
    size_t total = TWAB_LINK_HDR_LEN + plen;
    if (cap < total) return 0;

    twab_hdr_t hdr = {};
    hdr.magic = TWAB_LINK_MAGIC;
    hdr.version = TWAB_LINK_VERSION;
    hdr.msg = (uint8_t)msg;
    hdr.dev = (uint8_t)dev;
    hdr.payload_len = (uint8_t)plen;
    hdr.node_id = node_id;
    hdr.session = session;
    hdr.seq = seq;
    hdr.ref_seq = ref_seq;

    memcpy(out, &hdr, TWAB_LINK_HDR_LEN);
    if (plen) memcpy(out + TWAB_LINK_HDR_LEN, payload, plen);

    uint8_t tag[TWAB_LINK_TAG_LEN];
    compute_tag(out, total, tag);
    memcpy(out + offsetof(twab_hdr_t, tag), tag, TWAB_LINK_TAG_LEN);
    return total;
}

bool twab_link_verify(const uint8_t *buf, size_t len)
{
    if (!buf || len < TWAB_LINK_HDR_LEN || len > TWAB_LINK_MAX_FRAME) return false;

    twab_hdr_t hdr;
    memcpy(&hdr, buf, TWAB_LINK_HDR_LEN);       // the wire buffer may be unaligned
    if (hdr.magic != TWAB_LINK_MAGIC) return false;
    if (hdr.version != TWAB_LINK_VERSION) return false;
    if (hdr.payload_len > TWAB_LINK_MAX_PAYLOAD) return false;
    // A payload_len that disagrees with the received length is either a bug or
    // a truncation attempt; either way the frame is not what it claims to be.
    if (TWAB_LINK_HDR_LEN + hdr.payload_len != len) return false;

    uint8_t want[TWAB_LINK_TAG_LEN];
    compute_tag(buf, len, want);

    // Constant-time: a byte-at-a-time early return leaks the tag one byte per
    // ~256 forgeries, which is exactly the attack an 8-byte tag can't afford.
    uint8_t diff = 0;
    for (size_t i = 0; i < TWAB_LINK_TAG_LEN; i++) diff |= (uint8_t)(want[i] ^ hdr.tag[i]);
    return diff == 0;
}

const twab_hdr_t *twab_link_hdr(const uint8_t *buf)
{
    return (const twab_hdr_t *)buf;
}

const char *twab_link_payload(const uint8_t *buf, size_t *out_len)
{
    twab_hdr_t hdr;
    memcpy(&hdr, buf, TWAB_LINK_HDR_LEN);
    if (out_len) *out_len = hdr.payload_len;
    return (const char *)(buf + TWAB_LINK_HDR_LEN);
}

// ---------------------------------------------------------------------------
// Replay / duplicate suppression
// ---------------------------------------------------------------------------

typedef struct {
    uint16_t node_id;
    uint32_t session;
    uint32_t seq;
    bool     used;
} seen_t;

static seen_t s_seen[TWAB_LINK_MAX_NODES];

bool twab_link_dedupe(uint16_t node_id, uint32_t session, uint32_t seq)
{
    seen_t *slot = NULL;
    seen_t *free_slot = NULL;
    for (size_t i = 0; i < TWAB_LINK_MAX_NODES; i++) {
        if (s_seen[i].used && s_seen[i].node_id == node_id) { slot = &s_seen[i]; break; }
        if (!s_seen[i].used && !free_slot) free_slot = &s_seen[i];
    }
    if (!slot) {
        // Table full: fail open rather than silently ignoring a real button
        // press. A dropped press is a lost moment; a duplicated one costs a
        // second clip. The asymmetry decides it.
        if (!free_slot) return true;
        slot = free_slot;
        slot->used = true;
        slot->node_id = node_id;
        slot->session = session;
        slot->seq = seq;
        return true;
    }
    if (slot->session != session) {       // node rebooted: adopt its new session
        slot->session = session;
        slot->seq = seq;
        return true;
    }
    if (seq > slot->seq) {                // in-session progress
        slot->seq = seq;
        return true;
    }
    return false;                         // repeat or out-of-order retransmit
}

// ---------------------------------------------------------------------------
// Names / helpers
// ---------------------------------------------------------------------------

const char *twab_msg_name(uint8_t msg)
{
    switch (msg) {
    case TWAB_MSG_HELLO:     return "hello";
    case TWAB_MSG_PRESS:     return "press";
    case TWAB_MSG_TELEMETRY: return "telemetry";
    case TWAB_MSG_ACK:       return "ack";
    case TWAB_MSG_CMD:       return "cmd";
    case TWAB_MSG_EVENT:     return "event";
    default:                 return "unknown";
    }
}

const char *twab_dev_name(uint8_t dev)
{
    switch (dev) {
    case TWAB_DEV_BUTTON: return "button";
    case TWAB_DEV_LIGHT:  return "light";
    case TWAB_DEV_SENSOR: return "sensor";
    case TWAB_DEV_MASTER: return "master";
    default:              return "device";
    }
}

uint16_t twab_node_id_from_mac(const uint8_t mac[6])
{
    // Low two bytes are unique enough across a handful of boards, and the
    // third is folded in so two boards from the same reel don't collide.
    return (uint16_t)(((uint16_t)mac[4] << 8) | mac[5]) ^ (uint16_t)(mac[3] << 3);
}

void twab_link_mac_str(const uint8_t mac[6], char *out, size_t cap)
{
    snprintf(out, cap, "%02x:%02x:%02x:%02x:%02x:%02x",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

bool twab_link_mac_parse(const char *str, uint8_t out[6])
{
    if (!str) return false;
    unsigned v[6];
    if (sscanf(str, "%x:%x:%x:%x:%x:%x", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5]) != 6) {
        return false;
    }
    for (int i = 0; i < 6; i++) {
        if (v[i] > 0xff) return false;
        out[i] = (uint8_t)v[i];
    }
    return true;
}
