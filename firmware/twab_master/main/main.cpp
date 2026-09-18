// twab_master — the shop's ESP-NOW receiver.
//
// It is deliberately the dumbest of the three boards: it never joins WiFi,
// never holds a credential, and never talks to Blue Iris. It listens on one
// fixed ESP-NOW channel, authenticates every frame, and hands the survivors to
// the bridge over UART as newline-delimited JSON. Anything the bridge sends
// back it turns into an ESP-NOW frame aimed at the right node.
//
// Why this is a separate chip from the bridge:
//   ESP-NOW and station mode share one radio, and a station is pinned to its
//   AP's channel. Running both on one ESP32 means every node must follow the
//   AP's channel — and re-follow it when the AP roams or the router picks a new
//   one after a power cut, which is exactly the failure that makes a shop
//   button "work for a month and then not". Splitting them lets the ESP-NOW
//   side own a fixed channel forever and costs one $4 board.
//
// Flow for one press:
//   button --ESP-NOW--> master        (verify HMAC, drop duplicates)
//   master --ESP-NOW--> button        ACK stage=link  (fast, "I heard you")
//   master --UART----->  bridge       {"t":"press",...}
//   bridge --HTTP---->  Controller    POST /api/twab -> Blue Iris trigger
//   bridge --UART---->  master        {"t":"ack","code":200}
//   master --ESP-NOW--> button        ACK stage=done  (button flashes success)

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cinttypes>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "master_config.h"
#include "twab_link.h"
#include "twab_secrets.h"

static const char *TAG = "master";

static const uint8_t BROADCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

typedef struct {
    uint8_t  mac[6];
    uint16_t len;
    int8_t   rssi;
    uint8_t  data[TWAB_LINK_MAX_FRAME];
} rx_item_t;

typedef struct {
    bool     used;
    uint8_t  mac[6];
    uint16_t node_id;
    uint8_t  dev;
    int64_t  last_us;
    uint32_t rx_count;
} peer_slot_t;

static QueueHandle_t     s_rx_queue;
static SemaphoreHandle_t s_peers_lock;
static peer_slot_t       s_peers[TWABM_MAX_PEERS];

static uint16_t s_node_id;        // the master's own id, for frames it sends
static uint32_t s_session;        // random per boot
static uint32_t s_seq = 0;
static int64_t  s_bridge_last_us = 0;
static uint32_t s_forwarded = 0, s_dropped = 0;

// ---------------------------------------------------------------------------
// LED
// ---------------------------------------------------------------------------

static void led_init()
{
#if TWABM_LED_GPIO >= 0
    gpio_reset_pin((gpio_num_t)TWABM_LED_GPIO);
    gpio_set_direction((gpio_num_t)TWABM_LED_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)TWABM_LED_GPIO, 0);
#endif
}

static void led_blink(int times, int on_ms)
{
#if TWABM_LED_GPIO >= 0
    for (int i = 0; i < times; i++) {
        gpio_set_level((gpio_num_t)TWABM_LED_GPIO, 1);
        vTaskDelay(pdMS_TO_TICKS(on_ms));
        gpio_set_level((gpio_num_t)TWABM_LED_GPIO, 0);
        if (i + 1 < times) vTaskDelay(pdMS_TO_TICKS(on_ms));
    }
#else
    (void)times; (void)on_ms;
#endif
}

// ---------------------------------------------------------------------------
// Peer table
// ---------------------------------------------------------------------------

// ESP-NOW will not unicast to an address that isn't a registered peer, so a
// node has to be added before the master can ack it. Registration happens on
// the first authenticated frame: nothing to pair, nothing to configure, and a
// forged MAC can't get in because it would have to pass the HMAC first.
static void ensure_espnow_peer(const uint8_t mac[6])
{
    if (esp_now_is_peer_exist(mac)) return;
    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, mac, 6);
    peer.channel = 0;                 // 0 = whatever channel we're already on
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;             // authenticity is twab_link's job
    esp_err_t err = esp_now_add_peer(&peer);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "add_peer failed: %s", esp_err_to_name(err));
    }
}

static void peer_seen(const uint8_t mac[6], uint16_t node_id, uint8_t dev)
{
    xSemaphoreTake(s_peers_lock, portMAX_DELAY);
    peer_slot_t *slot = nullptr, *oldest = nullptr;
    for (int i = 0; i < TWABM_MAX_PEERS; i++) {
        if (s_peers[i].used && s_peers[i].node_id == node_id) { slot = &s_peers[i]; break; }
        if (!s_peers[i].used) { if (!slot) slot = &s_peers[i]; }
        else if (!oldest || s_peers[i].last_us < oldest->last_us) oldest = &s_peers[i];
    }
    if (!slot) slot = oldest;         // full: evict least-recently-heard
    if (slot) {
        if (!slot->used || slot->node_id != node_id) {
            ESP_LOGI(TAG, "node %04x (%s) joined", node_id, twab_dev_name(dev));
            slot->rx_count = 0;
        }
        slot->used = true;
        memcpy(slot->mac, mac, 6);
        slot->node_id = node_id;
        slot->dev = dev;
        slot->last_us = esp_timer_get_time();
        slot->rx_count++;
    }
    xSemaphoreGive(s_peers_lock);
}

static bool peer_mac(uint16_t node_id, uint8_t out[6])
{
    bool found = false;
    xSemaphoreTake(s_peers_lock, portMAX_DELAY);
    for (int i = 0; i < TWABM_MAX_PEERS; i++) {
        if (s_peers[i].used && s_peers[i].node_id == node_id) {
            memcpy(out, s_peers[i].mac, 6);
            found = true;
            break;
        }
    }
    xSemaphoreGive(s_peers_lock);
    return found;
}

static int peer_count()
{
    int n = 0;
    xSemaphoreTake(s_peers_lock, portMAX_DELAY);
    for (int i = 0; i < TWABM_MAX_PEERS; i++) if (s_peers[i].used) n++;
    xSemaphoreGive(s_peers_lock);
    return n;
}

// ---------------------------------------------------------------------------
// ESP-NOW
// ---------------------------------------------------------------------------

// IDF v5.5 changed the send callback's first argument from the peer MAC to a
// wifi_tx_info_t. Nothing here uses it either way, but the signature has to
// match exactly or esp_now_register_send_cb won't compile.
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
static void on_espnow_sent(const wifi_tx_info_t *, esp_now_send_status_t status)
#else
static void on_espnow_sent(const uint8_t *, esp_now_send_status_t status)
#endif
{
    if (status != ESP_NOW_SEND_SUCCESS) {
        ESP_LOGD(TAG, "espnow tx not acked (node asleep or out of range)");
    }
}

static void on_espnow_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len)
{
    // Runs in the WiFi task. Copy and get out — anything slow here stalls the
    // radio and drops the next frame.
    if (len <= 0 || (size_t)len > TWAB_LINK_MAX_FRAME) return;
    rx_item_t item;
    memcpy(item.mac, info->src_addr, 6);
    item.len = (uint16_t)len;
    item.rssi = info->rx_ctrl ? (int8_t)info->rx_ctrl->rssi : 0;
    memcpy(item.data, data, len);
    if (xQueueSend(s_rx_queue, &item, 0) != pdTRUE) {
        s_dropped++;
    }
}

static void espnow_send(const uint8_t mac[6], twab_msg_t msg, uint32_t ref_seq,
                        const char *payload)
{
    uint8_t frame[TWAB_LINK_MAX_FRAME];
    size_t n = twab_link_build(frame, sizeof frame, msg, TWAB_DEV_MASTER,
                               s_node_id, s_session, ++s_seq, ref_seq, payload);
    if (!n) {
        ESP_LOGW(TAG, "payload too long for one frame, dropping");
        return;
    }
    ensure_espnow_peer(mac);
    esp_err_t err = esp_now_send(mac, frame, n);
    if (err != ESP_OK) ESP_LOGW(TAG, "espnow_send: %s", esp_err_to_name(err));
}

// ---------------------------------------------------------------------------
// UART (newline-delimited JSON, both directions)
// ---------------------------------------------------------------------------
//
// NDJSON rather than a packed binary struct: the link runs at 115200 with a
// few frames a day on it, so nothing is gained by packing, and everything is
// gained by being able to clip a USB-serial adapter onto the wire and read the
// traffic — or drive either half by hand from a terminal while the other half
// is unplugged.

static SemaphoreHandle_t s_uart_lock;

static void uart_send_line(const char *line)
{
    xSemaphoreTake(s_uart_lock, portMAX_DELAY);
    uart_write_bytes(TWABM_UART_PORT, line, strlen(line));
    uart_write_bytes(TWABM_UART_PORT, "\n", 1);
    xSemaphoreGive(s_uart_lock);
}

static void uart_send_json(cJSON *root)
{
    char *text = cJSON_PrintUnformatted(root);
    if (text) {
        uart_send_line(text);
        cJSON_free(text);
    }
    cJSON_Delete(root);
}

static void uart_init()
{
    uart_config_t cfg = {};
    cfg.baud_rate = TWABM_UART_BAUD;
    cfg.data_bits = UART_DATA_8_BITS;
    cfg.parity = UART_PARITY_DISABLE;
    cfg.stop_bits = UART_STOP_BITS_1;
    cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    cfg.source_clk = UART_SCLK_DEFAULT;
    ESP_ERROR_CHECK(uart_driver_install(TWABM_UART_PORT, 2048, 2048, 0, nullptr, 0));
    ESP_ERROR_CHECK(uart_param_config(TWABM_UART_PORT, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(TWABM_UART_PORT, TWABM_UART_TX_GPIO, TWABM_UART_RX_GPIO,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    s_uart_lock = xSemaphoreCreateMutex();
}

// ---------------------------------------------------------------------------
// ESP-NOW -> UART
// ---------------------------------------------------------------------------

static void forward_task(void *)
{
    rx_item_t item;
    for (;;) {
        if (xQueueReceive(s_rx_queue, &item, portMAX_DELAY) != pdTRUE) continue;

        if (!twab_link_verify(item.data, item.len)) {
            // Either 2.4 GHz noise or a node flashed with a different key.
            // Warn, don't spam: this is the first thing to check when a new
            // board "doesn't work".
            s_dropped++;
            ESP_LOGW(TAG, "frame failed auth (wrong key or corrupt), %d bytes", item.len);
            continue;
        }

        const twab_hdr_t *hdr = twab_link_hdr(item.data);
        size_t plen = 0;
        const char *payload = twab_link_payload(item.data, &plen);

        peer_seen(item.mac, hdr->node_id, hdr->dev);
        ensure_espnow_peer(item.mac);

        // Ack first, and ack duplicates too. A node retries a press when its
        // ack went missing, reusing the same seq on purpose — so the retry
        // must be answered (or it retries forever) and must NOT be forwarded
        // (or one press saves two clips). Acking above the dedupe check is
        // what makes a press idempotent end to end.
        //
        // HELLO is acked for the same reason a press is: the node blocks on
        // this ack and reads its absence as "nobody answered". A cold boot and
        // the 3 s long-press both send HELLO, so acking only PRESS meant a
        // perfectly wired button flashed the master-is-dead pattern every time
        // it powered up. Nothing acks TELEMETRY — that one is fire-and-forget
        // on purpose, so a heartbeat costs the battery no extra awake time.
        if (hdr->msg == TWAB_MSG_PRESS || hdr->msg == TWAB_MSG_HELLO) {
            espnow_send(item.mac, TWAB_MSG_ACK, hdr->seq, "{\"stage\":\"link\"}");
        }

        if (!twab_link_dedupe(hdr->node_id, hdr->session, hdr->seq)) {
            ESP_LOGI(TAG, "duplicate seq %" PRIu32 " from %04x, acked not forwarded",
                     hdr->seq, hdr->node_id);
            continue;
        }

        char node_hex[8], mac_str[18];
        snprintf(node_hex, sizeof node_hex, "%04x", hdr->node_id);
        twab_link_mac_str(item.mac, mac_str, sizeof mac_str);

        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "t", twab_msg_name(hdr->msg));
        cJSON_AddStringToObject(root, "node", node_hex);
        cJSON_AddStringToObject(root, "dev", twab_dev_name(hdr->dev));
        cJSON_AddNumberToObject(root, "seq", (double)hdr->seq);
        cJSON_AddStringToObject(root, "mac", mac_str);
        cJSON_AddNumberToObject(root, "rssi", item.rssi);

        // The node's payload is passed through, not re-interpreted: the master
        // has no business knowing what a light node puts in its telemetry.
        char pbuf[TWAB_LINK_MAX_PAYLOAD + 1];
        memcpy(pbuf, payload, plen);
        pbuf[plen] = 0;
        cJSON *data = plen ? cJSON_Parse(pbuf) : nullptr;
        if (data) cJSON_AddItemToObject(root, "data", data);
        else if (plen) cJSON_AddStringToObject(root, "data", pbuf);

        uart_send_json(root);
        s_forwarded++;
        led_blink(1, 40);

        ESP_LOGI(TAG, "%s from %04x (%s) seq %" PRIu32 " rssi %d -> bridge",
                 twab_msg_name(hdr->msg), hdr->node_id, twab_dev_name(hdr->dev),
                 hdr->seq, item.rssi);
    }
}

// ---------------------------------------------------------------------------
// UART -> ESP-NOW
// ---------------------------------------------------------------------------

static void handle_bridge_line(const char *line)
{
    cJSON *root = cJSON_Parse(line);
    if (!root) {
        ESP_LOGW(TAG, "unparseable line from bridge: %.64s", line);
        return;
    }
    s_bridge_last_us = esp_timer_get_time();

    const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "t");
    const char *type = cJSON_IsString(t) ? t->valuestring : "";

    if (strcmp(type, "ack") == 0) {
        // The bridge finished the HTTP round trip. Relay the verdict to the
        // node that pressed, so the button can show success or failure rather
        // than just "sent".
        const cJSON *node = cJSON_GetObjectItemCaseSensitive(root, "node");
        const cJSON *seq  = cJSON_GetObjectItemCaseSensitive(root, "seq");
        const cJSON *ok   = cJSON_GetObjectItemCaseSensitive(root, "ok");
        const cJSON *code = cJSON_GetObjectItemCaseSensitive(root, "code");
        if (cJSON_IsString(node)) {
            uint16_t id = (uint16_t)strtoul(node->valuestring, nullptr, 16);
            uint8_t mac[6];
            if (peer_mac(id, mac)) {
                char payload[64];
                snprintf(payload, sizeof payload, "{\"stage\":\"done\",\"ok\":%d,\"code\":%d}",
                         cJSON_IsTrue(ok) ? 1 : 0,
                         cJSON_IsNumber(code) ? (int)code->valuedouble : 0);
                espnow_send(mac, TWAB_MSG_ACK,
                            cJSON_IsNumber(seq) ? (uint32_t)seq->valuedouble : 0,
                            payload);
            } else {
                ESP_LOGW(TAG, "ack for unknown node %s", node->valuestring);
            }
        }
    } else if (strcmp(type, "cmd") == 0) {
        // Downstream path, unused today and the reason it exists: a future
        // WLED node registers itself with a HELLO, and the Controller can then
        // POST a command that lands here and goes out over ESP-NOW.
        const cJSON *node = cJSON_GetObjectItemCaseSensitive(root, "node");
        const cJSON *data = cJSON_GetObjectItemCaseSensitive(root, "data");
        char *payload = data ? cJSON_PrintUnformatted(data) : nullptr;
        const char *body = payload ? payload : "{}";
        if (strlen(body) > TWAB_LINK_MAX_PAYLOAD) {
            ESP_LOGW(TAG, "cmd payload %d bytes > %d, dropped",
                     (int)strlen(body), TWAB_LINK_MAX_PAYLOAD);
        } else if (cJSON_IsString(node) && strcmp(node->valuestring, "ffff") == 0) {
            espnow_send(BROADCAST, TWAB_MSG_CMD, 0, body);
            ESP_LOGI(TAG, "cmd broadcast");
        } else if (cJSON_IsString(node)) {
            uint16_t id = (uint16_t)strtoul(node->valuestring, nullptr, 16);
            uint8_t mac[6];
            if (peer_mac(id, mac)) {
                espnow_send(mac, TWAB_MSG_CMD, 0, body);
                ESP_LOGI(TAG, "cmd -> %04x", id);
            } else {
                ESP_LOGW(TAG, "cmd for unknown node %s", node->valuestring);
            }
        }
        if (payload) cJSON_free(payload);
    } else if (strcmp(type, "hb") == 0) {
        ESP_LOGD(TAG, "bridge heartbeat");
    } else {
        ESP_LOGW(TAG, "unknown message type from bridge: %s", type);
    }
    cJSON_Delete(root);
}

static void uart_rx_task(void *)
{
    static char line[TWABM_LINE_MAX];
    size_t used = 0;
    bool overlong = false;
    uint8_t chunk[128];

    for (;;) {
        int n = uart_read_bytes(TWABM_UART_PORT, chunk, sizeof chunk, pdMS_TO_TICKS(200));
        for (int i = 0; i < n; i++) {
            char c = (char)chunk[i];
            if (c == '\n' || c == '\r') {
                if (overlong) {
                    ESP_LOGW(TAG, "dropped oversize line from bridge");
                } else if (used) {
                    line[used] = 0;
                    handle_bridge_line(line);
                }
                used = 0;
                overlong = false;
            } else if (used < sizeof line - 1) {
                line[used++] = c;
            } else {
                // Keep consuming to the newline so one bad line doesn't
                // corrupt the next one.
                overlong = true;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Heartbeat
// ---------------------------------------------------------------------------

static void heartbeat_task(void *)
{
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(TWABM_HEARTBEAT_MS));

        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "t", "hb");
        cJSON_AddStringToObject(root, "role", "master");
        cJSON_AddStringToObject(root, "fw", esp_app_get_description()->version);
        cJSON_AddNumberToObject(root, "up_ms", (double)(esp_timer_get_time() / 1000));
        cJSON_AddNumberToObject(root, "peers", peer_count());
        cJSON_AddNumberToObject(root, "forwarded", (double)s_forwarded);
        cJSON_AddNumberToObject(root, "dropped", (double)s_dropped);
        uart_send_json(root);

        bool bridge_up = s_bridge_last_us &&
            (esp_timer_get_time() - s_bridge_last_us) < (int64_t)TWABM_BRIDGE_TIMEOUT_MS * 1000;
        if (!bridge_up) {
            ESP_LOGW(TAG, "no traffic from bridge in %d s — check the UART wiring",
                     TWABM_BRIDGE_TIMEOUT_MS / 1000);
            led_blink(2, 60);
        }
    }
}

// ---------------------------------------------------------------------------

extern "C" void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    uint8_t key[TWAB_LINK_KEY_LEN];
    if (!twab_link_key_from_hex(TWAB_LINK_KEY_HEX, key)) {
        // Refuse to run rather than come up authenticating nothing: a master
        // with a broken key silently drops every press, which looks exactly
        // like a dead button.
        ESP_LOGE(TAG, "TWAB_LINK_KEY_HEX is not 64 hex chars — fix twab_secrets.h");
        abort();
    }
    twab_link_set_key(key);

    led_init();
    uart_init();
    s_rx_queue = xQueueCreate(TWABM_RX_QUEUE_DEPTH, sizeof(rx_item_t));
    s_peers_lock = xSemaphoreCreateMutex();
    s_session = esp_random();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Never associates, so nothing else will ever move this off the fleet
    // channel. Power save off: a listener that dozes misses frames, and the
    // board is on shop mains.
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    ESP_ERROR_CHECK(esp_wifi_set_channel(TWAB_ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE));
#if TWAB_ESPNOW_LONG_RANGE
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_STA,
        WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR));
#endif

    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_espnow_recv));
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_espnow_sent));
    ensure_espnow_peer(BROADCAST);    // needed to *send* broadcasts

    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    s_node_id = twab_node_id_from_mac(mac);
    char mac_str[18];
    twab_link_mac_str(mac, mac_str, sizeof mac_str);

    // The one number every other board needs. Print it loudly.
    ESP_LOGW(TAG, "==== twab master %04x  MAC %s  channel %d ====",
             s_node_id, mac_str, TWAB_ESPNOW_CHANNEL);
    ESP_LOGW(TAG, "==== put this MAC in the button's TWABB_MASTER_MAC ====");

    cJSON *boot = cJSON_CreateObject();
    cJSON_AddStringToObject(boot, "t", "boot");
    cJSON_AddStringToObject(boot, "role", "master");
    cJSON_AddStringToObject(boot, "mac", mac_str);
    cJSON_AddStringToObject(boot, "fw", esp_app_get_description()->version);
    cJSON_AddNumberToObject(boot, "channel", TWAB_ESPNOW_CHANNEL);
    uart_send_json(boot);

    xTaskCreate(forward_task,   "twab_fwd",  4096, nullptr, 6, nullptr);
    xTaskCreate(uart_rx_task,   "twab_uart", 4096, nullptr, 5, nullptr);
    xTaskCreate(heartbeat_task, "twab_hb",   3072, nullptr, 3, nullptr);
    led_blink(3, 80);
}
