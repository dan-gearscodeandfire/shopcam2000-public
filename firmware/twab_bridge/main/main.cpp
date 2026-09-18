// twab_bridge — the only board in the fleet that touches the LAN.
//
// Reads newline-delimited JSON from the master over UART, turns each message
// into an HTTP POST to the Shopcam 2000 Controller, and reports the result
// back over the same wire so the master can tell the button whether the moment
// was actually saved.
//
//   master --UART--> bridge --HTTP--> Controller --> Blue Iris trigger
//   master <--UART-- bridge            (ack: ok + status code)
//
// It also carries the fleet's only OTA path. The ESP-NOW nodes have no route
// to the network and are USB-flashed on the bench; this board is the one that
// will actually accumulate changes (routing rules, endpoints, a new node type)
// and it is the one bolted somewhere inconvenient, so it updates itself.
//
// Everything it knows about the shop network lives in bridge_config.h, which
// is gitignored. Nothing in this file is a secret.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cinttypes>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/event_groups.h"

#include "cJSON.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_http_server.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "lwip/ip_addr.h"

#include "bridge_config.h"

static const char *TAG = "bridge";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

typedef struct {
    char     path[64];
    char     node[8];
    uint32_t seq;
    bool     want_ack;      // only presses get a verdict relayed to the node
    char     body[TWABR_LINE_MAX];
} job_t;

static QueueHandle_t     s_jobs;
static SemaphoreHandle_t s_uart_lock;
static EventGroupHandle_t s_net;
static constexpr EventBits_t NET_UP = BIT0;

static esp_netif_t *s_netif = nullptr;
static int64_t  s_master_last_us = 0;
static int64_t  s_last_press_us = 0;
static uint32_t s_posts_ok = 0, s_posts_fail = 0;
static bool     s_app_validated = false;

// ---------------------------------------------------------------------------
// LED
// ---------------------------------------------------------------------------

static void led_init()
{
#if TWABR_LED_GPIO >= 0
    gpio_reset_pin((gpio_num_t)TWABR_LED_GPIO);
    gpio_set_direction((gpio_num_t)TWABR_LED_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)TWABR_LED_GPIO, 0);
#endif
}

static void led(bool on)
{
#if TWABR_LED_GPIO >= 0
    gpio_set_level((gpio_num_t)TWABR_LED_GPIO, on);
#else
    (void)on;
#endif
}

// ---------------------------------------------------------------------------
// WiFi — reconnects forever. This board is mains powered and has one job;
// giving up is never the right answer, but hammering the AP isn't either, so
// the backoff grows to 30 s and stays there.
// ---------------------------------------------------------------------------

static volatile bool s_want_reconnect = false;
static int s_backoff_ms = 500;

static void on_wifi_event(void *, esp_event_base_t base, int32_t id, void *)
{
    // Handlers run on the system event task. Never sleep here — the backoff
    // belongs in wifi_supervisor_task, or a 30 s wait would also stall the IP
    // events and every other subsystem hanging off this loop.
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        xEventGroupClearBits(s_net, NET_UP);
        s_want_reconnect = true;
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_backoff_ms = 500;
        s_want_reconnect = false;
        xEventGroupSetBits(s_net, NET_UP);
        ESP_LOGI(TAG, "wifi up");
    }
}

static void wifi_supervisor_task(void *)
{
    for (;;) {
        if (s_want_reconnect) {
            ESP_LOGW(TAG, "wifi down, retry in %d ms", s_backoff_ms);
            vTaskDelay(pdMS_TO_TICKS(s_backoff_ms));
            s_backoff_ms = s_backoff_ms < 30000 ? s_backoff_ms * 2 : 30000;
            if (s_want_reconnect) esp_wifi_connect();
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

static void wifi_start()
{
    s_net = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_netif = esp_netif_create_default_wifi_sta();

#if TWABR_USE_STATIC_IP
    esp_netif_dhcpc_stop(s_netif);
    esp_netif_ip_info_t ip = {};
    ip.ip.addr = ipaddr_addr(TWABR_STATIC_IP);
    ip.gw.addr = ipaddr_addr(TWABR_STATIC_GW);
    ip.netmask.addr = ipaddr_addr(TWABR_STATIC_MASK);
    esp_netif_set_ip_info(s_netif, &ip);
    // No DHCP means no DNS server either; point DNS at the gateway so a
    // hostname in TWABR_SERVER_BASE still resolves instead of failing forever.
    esp_netif_dns_info_t dns = {};
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4.addr = ip.gw.addr;
    esp_netif_set_dns_info(s_netif, ESP_NETIF_DNS_MAIN, &dns);
#endif

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               &on_wifi_event, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &on_wifi_event, nullptr));

    wifi_config_t cfg = {};
    strlcpy((char *)cfg.sta.ssid, TWABR_WIFI_SSID, sizeof cfg.sta.ssid);
    strlcpy((char *)cfg.sta.password, TWABR_WIFI_PASSWORD, sizeof cfg.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_start());
    // Modem sleep would add up to a beacon interval of latency to the POST
    // that saves the shot. Mains power; keep the radio awake.
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
}

static bool net_up()
{
    return (xEventGroupGetBits(s_net) & NET_UP) != 0;
}

static void ip_str(char *out, size_t cap)
{
    esp_netif_ip_info_t info = {};
    if (s_netif && esp_netif_get_ip_info(s_netif, &info) == ESP_OK) {
        snprintf(out, cap, IPSTR, IP2STR(&info.ip));
    } else {
        snprintf(out, cap, "0.0.0.0");
    }
}

static int wifi_rssi()
{
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

// ---------------------------------------------------------------------------
// UART to the master
// ---------------------------------------------------------------------------

static void uart_send_line(const char *line)
{
    xSemaphoreTake(s_uart_lock, portMAX_DELAY);
    uart_write_bytes(TWABR_UART_PORT, line, strlen(line));
    uart_write_bytes(TWABR_UART_PORT, "\n", 1);
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
    cfg.baud_rate = TWABR_UART_BAUD;
    cfg.data_bits = UART_DATA_8_BITS;
    cfg.parity = UART_PARITY_DISABLE;
    cfg.stop_bits = UART_STOP_BITS_1;
    cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    cfg.source_clk = UART_SCLK_DEFAULT;
    ESP_ERROR_CHECK(uart_driver_install(TWABR_UART_PORT, 2048, 2048, 0, nullptr, 0));
    ESP_ERROR_CHECK(uart_param_config(TWABR_UART_PORT, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(TWABR_UART_PORT, TWABR_UART_TX_GPIO, TWABR_UART_RX_GPIO,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    s_uart_lock = xSemaphoreCreateMutex();
}

static void send_ack(const char *node, uint32_t seq, bool ok, int code)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "t", "ack");
    cJSON_AddStringToObject(root, "node", node);
    cJSON_AddNumberToObject(root, "seq", (double)seq);
    cJSON_AddBoolToObject(root, "ok", ok);
    cJSON_AddNumberToObject(root, "code", code);
    uart_send_json(root);
}

// ---------------------------------------------------------------------------
// HTTP out
// ---------------------------------------------------------------------------

// Returns the HTTP status, or 0 for a transport failure.
static int http_post_json(const char *path, const char *body)
{
    char url[192];
    snprintf(url, sizeof url, "%s%s", TWABR_SERVER_BASE, path);

    for (int attempt = 1; attempt <= TWABR_HTTP_RETRIES; attempt++) {
        esp_http_client_config_t cfg = {};
        cfg.url = url;
        cfg.method = HTTP_METHOD_POST;
        cfg.timeout_ms = TWABR_HTTP_TIMEOUT_MS;
        esp_http_client_handle_t client = esp_http_client_init(&cfg);
        if (!client) continue;
        esp_http_client_set_header(client, "Content-Type", "application/json");
        esp_http_client_set_post_field(client, body, strlen(body));
        esp_err_t err = esp_http_client_perform(client);
        int status = esp_http_client_get_status_code(client);
        esp_http_client_cleanup(client);

        if (err == ESP_OK && status >= 200 && status < 300) {
            ESP_LOGI(TAG, "POST %s -> %d (attempt %d)", path, status, attempt);
            return status;
        }
        ESP_LOGW(TAG, "POST %s: %s / HTTP %d (attempt %d)",
                 path, esp_err_to_name(err), status, attempt);
        // The Controller's contract, inherited from the original TWAB button:
        // 200 = at least one camera triggered (retrying would re-trigger the
        // ones that saved), 409 = no overwatch cameras selected, 502 = nothing
        // triggered and a retry is safe. So: 4xx is final, 5xx and transport
        // errors are worth another go.
        if (err == ESP_OK && status >= 400 && status < 500) return status;
        if (attempt < TWABR_HTTP_RETRIES) vTaskDelay(pdMS_TO_TICKS(250 * attempt));
    }
    return 0;
}

static void http_worker_task(void *)
{
    job_t job;
    for (;;) {
        if (xQueueReceive(s_jobs, &job, portMAX_DELAY) != pdTRUE) continue;

        if (!net_up()) {
            // Waiting it out is pointless: the whole value of a press is that
            // it lands while the pre-roll buffer still holds the moment. Fail
            // fast and tell the button, so the human knows to try again.
            ESP_LOGE(TAG, "dropping %s — no network", job.path);
            s_posts_fail++;
            if (job.want_ack) send_ack(job.node, job.seq, false, 0);
            continue;
        }

        led(true);
        int status = http_post_json(job.path, job.body);
        led(false);

        bool ok = status >= 200 && status < 300;
        if (ok) {
            s_posts_ok++;
            // Proof the new image can do the job. Until this happens the
            // bootloader will roll back on the next reboot.
            if (!s_app_validated) {
                esp_ota_mark_app_valid_cancel_rollback();
                s_app_validated = true;
            }
        } else {
            s_posts_fail++;
        }
        if (job.want_ack) send_ack(job.node, job.seq, ok, status);
    }
}

// ---------------------------------------------------------------------------
// Routing: master's NDJSON -> an endpoint on the Controller
// ---------------------------------------------------------------------------

static void handle_master_line(const char *line)
{
    cJSON *root = cJSON_Parse(line);
    if (!root) {
        ESP_LOGW(TAG, "unparseable line from master: %.64s", line);
        return;
    }
    s_master_last_us = esp_timer_get_time();

    const cJSON *t = cJSON_GetObjectItemCaseSensitive(root, "t");
    const char *type = cJSON_IsString(t) ? t->valuestring : "";

    if (strcmp(type, "hb") == 0 || strcmp(type, "boot") == 0) {
        // Master liveness only; nothing goes upstream. Log the boot line,
        // because it carries the master's MAC and channel.
        if (strcmp(type, "boot") == 0) ESP_LOGW(TAG, "master booted: %s", line);
        cJSON_Delete(root);
        return;
    }

    job_t job = {};
    bool is_press = strcmp(type, "press") == 0;
    if (is_press) {
        strlcpy(job.path, TWABR_PRESS_PATH, sizeof job.path);
        job.want_ack = true;
        s_last_press_us = esp_timer_get_time();
    } else if (strcmp(type, "telemetry") == 0 || strcmp(type, "hello") == 0) {
        strlcpy(job.path, TWABR_TELEMETRY_PATH, sizeof job.path);
    } else {
        // Unknown type from a node the bridge has never heard of: give it a
        // predictable URL and let the Controller decide. Adding a node type
        // becomes a server-side change, not a firmware change.
        snprintf(job.path, sizeof job.path, "%s/%s", TWABR_GENERIC_PREFIX, type);
    }

    const cJSON *node = cJSON_GetObjectItemCaseSensitive(root, "node");
    const cJSON *seq  = cJSON_GetObjectItemCaseSensitive(root, "seq");
    strlcpy(job.node, cJSON_IsString(node) ? node->valuestring : "0000", sizeof job.node);
    job.seq = cJSON_IsNumber(seq) ? (uint32_t)seq->valuedouble : 0;

    // Re-wrap rather than forward verbatim: the Controller logs this line and
    // it should say where the press came in from, not just that it happened.
    char ip[16];
    ip_str(ip, sizeof ip);
    cJSON_AddStringToObject(root, "source", "espnow");
    cJSON *bridge = cJSON_CreateObject();
    cJSON_AddStringToObject(bridge, "fw", esp_app_get_description()->version);
    cJSON_AddStringToObject(bridge, "ip", ip);
    cJSON_AddNumberToObject(bridge, "rssi", wifi_rssi());
    cJSON_AddNumberToObject(bridge, "up_ms", (double)(esp_timer_get_time() / 1000));
    cJSON_AddItemToObject(root, "bridge", bridge);

    char *body = cJSON_PrintUnformatted(root);
    if (body) {
        if (strlen(body) < sizeof job.body) {
            strlcpy(job.body, body, sizeof job.body);
            if (xQueueSend(s_jobs, &job, 0) != pdTRUE) {
                ESP_LOGE(TAG, "job queue full, dropping %s", type);
                if (job.want_ack) send_ack(job.node, job.seq, false, 0);
            }
        } else {
            ESP_LOGE(TAG, "body %d bytes exceeds job buffer", (int)strlen(body));
        }
        cJSON_free(body);
    }
    cJSON_Delete(root);
}

static void uart_rx_task(void *)
{
    static char line[TWABR_LINE_MAX];
    size_t used = 0;
    bool overlong = false;
    uint8_t chunk[128];

    for (;;) {
        int n = uart_read_bytes(TWABR_UART_PORT, chunk, sizeof chunk, pdMS_TO_TICKS(200));
        for (int i = 0; i < n; i++) {
            char c = (char)chunk[i];
            if (c == '\n' || c == '\r') {
                if (overlong) ESP_LOGW(TAG, "dropped oversize line from master");
                else if (used) { line[used] = 0; handle_master_line(line); }
                used = 0;
                overlong = false;
            } else if (used < sizeof line - 1) {
                line[used++] = c;
            } else {
                overlong = true;   // keep eating to the newline
            }
        }
    }
}

// ---------------------------------------------------------------------------
// OTA — same pull model as the original TWAB button: the Controller publishes
// version.txt + a signed .bin, the device compares and updates itself.
// ---------------------------------------------------------------------------

static bool fetch_remote_version(char *out, size_t cap)
{
    char url[192];
    snprintf(url, sizeof url, "%s%s", TWABR_SERVER_BASE, TWABR_OTA_MANIFEST);
    esp_http_client_config_t cfg = {};
    cfg.url = url;
    cfg.timeout_ms = TWABR_HTTP_TIMEOUT_MS;
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) return false;

    bool ok = false;
    if (esp_http_client_open(client, 0) == ESP_OK) {
        esp_http_client_fetch_headers(client);
        int n = esp_http_client_read_response(client, out, cap - 1);
        if (esp_http_client_get_status_code(client) == 200 && n > 0) {
            out[n] = 0;
            while (n > 0 && (out[n-1] == '\n' || out[n-1] == '\r' || out[n-1] == ' '))
                out[--n] = 0;
            ok = n > 0;
        }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ok;
}

static void ota_check()
{
    char remote[32];
    if (!fetch_remote_version(remote, sizeof remote)) {
        ESP_LOGI(TAG, "ota: no manifest on server, skipping");
        return;
    }
    // Reaching the Controller is itself proof this image works well enough to
    // keep. Without this, a bridge that boots at 2 a.m. with nobody pressing
    // the button would roll back a perfectly good update.
    if (!s_app_validated) {
        esp_ota_mark_app_valid_cancel_rollback();
        s_app_validated = true;
    }

    const char *local = esp_app_get_description()->version;
    if (strcmp(remote, local) == 0) {
        ESP_LOGI(TAG, "ota: up to date (%s)", local);
        return;
    }
    ESP_LOGW(TAG, "ota: %s -> %s, updating", local, remote);

    char url[192];
    snprintf(url, sizeof url, "%s%s", TWABR_SERVER_BASE, TWABR_OTA_BINARY);
    esp_http_client_config_t http = {};
    http.url = url;
    http.timeout_ms = 15000;
    http.keep_alive_enable = true;
    esp_https_ota_config_t ota = {};
    ota.http_config = &http;

    esp_err_t err = esp_https_ota(&ota);
    if (err == ESP_OK) {
        ESP_LOGW(TAG, "ota: success, rebooting into new image");
        esp_restart();
    }
    ESP_LOGE(TAG, "ota: failed: %s", esp_err_to_name(err));
}

static void ota_task(void *)
{
    // Let WiFi settle before the first check.
    xEventGroupWaitBits(s_net, NET_UP, pdFALSE, pdTRUE, portMAX_DELAY);
    vTaskDelay(pdMS_TO_TICKS(15000));
    for (;;) {
        ota_check();                       // reboots on success
        if (TWABR_OTA_CHECK_MIN <= 0) break;
        vTaskDelay(pdMS_TO_TICKS((uint32_t)TWABR_OTA_CHECK_MIN * 60000));
    }
    vTaskDelete(nullptr);
}

// ---------------------------------------------------------------------------
// Status / command server — the only way to see the link without a cable.
// ---------------------------------------------------------------------------

#if TWABR_STATUS_SERVER

static esp_err_t status_get(httpd_req_t *req)
{
    char ip[16];
    ip_str(ip, sizeof ip);
    bool master_up = s_master_last_us &&
        (esp_timer_get_time() - s_master_last_us) < (int64_t)TWABR_MASTER_TIMEOUT_MS * 1000;

    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "role", "twab_bridge");
    cJSON_AddStringToObject(root, "fw", esp_app_get_description()->version);
    cJSON_AddStringToObject(root, "ip", ip);
    cJSON_AddNumberToObject(root, "rssi", wifi_rssi());
    cJSON_AddBoolToObject(root, "wifi", net_up());
    cJSON_AddBoolToObject(root, "master_link", master_up);
    cJSON_AddNumberToObject(root, "up_ms", (double)(esp_timer_get_time() / 1000));
    cJSON_AddNumberToObject(root, "posts_ok", (double)s_posts_ok);
    cJSON_AddNumberToObject(root, "posts_fail", (double)s_posts_fail);
    cJSON_AddNumberToObject(root, "last_press_ms_ago",
        s_last_press_us ? (double)((esp_timer_get_time() - s_last_press_us) / 1000) : -1);

    char *text = cJSON_PrintUnformatted(root);
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, text ? text : "{}");
    if (text) cJSON_free(text);
    cJSON_Delete(root);
    return ESP_OK;
}

// POST /cmd  {"node":"a3f1","data":{...}}   node "ffff" = broadcast.
// Forwarded verbatim to the master, which puts it on the air. Nothing uses
// this yet; it is the hook a WLED node will hang off.
static esp_err_t cmd_post(httpd_req_t *req)
{
    char buf[TWABR_LINE_MAX];
    int total = req->content_len;
    if (total <= 0 || total >= (int)sizeof buf) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad length");
        return ESP_OK;
    }
    int got = 0;
    while (got < total) {
        int n = httpd_req_recv(req, buf + got, total - got);
        if (n <= 0) {
            httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "recv failed");
            return ESP_OK;
        }
        got += n;
    }
    buf[got] = 0;

    cJSON *in = cJSON_Parse(buf);
    if (!in) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "not json");
        return ESP_OK;
    }
    const cJSON *node = cJSON_GetObjectItemCaseSensitive(in, "node");
    const cJSON *data = cJSON_GetObjectItemCaseSensitive(in, "data");
    cJSON *out = cJSON_CreateObject();
    cJSON_AddStringToObject(out, "t", "cmd");
    cJSON_AddStringToObject(out, "node", cJSON_IsString(node) ? node->valuestring : "ffff");
    cJSON_AddItemToObject(out, "data", data ? cJSON_Duplicate(data, 1) : cJSON_CreateObject());
    uart_send_json(out);
    cJSON_Delete(in);

    // 202: handed to the master. ESP-NOW delivery to a sleeping node is not
    // something this endpoint can promise.
    httpd_resp_set_status(req, "202 Accepted");
    httpd_resp_set_type(req, "application/json");
    httpd_resp_sendstr(req, "{\"queued\":true}");
    return ESP_OK;
}

static void status_server_start()
{
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = TWABR_STATUS_PORT;
    cfg.lru_purge_enable = true;
    httpd_handle_t server = nullptr;
    if (httpd_start(&server, &cfg) != ESP_OK) {
        ESP_LOGW(TAG, "status server failed to start");
        return;
    }
    httpd_uri_t status_uri = {};
    status_uri.uri = "/status";
    status_uri.method = HTTP_GET;
    status_uri.handler = status_get;
    httpd_register_uri_handler(server, &status_uri);

    httpd_uri_t cmd_uri = {};
    cmd_uri.uri = "/cmd";
    cmd_uri.method = HTTP_POST;
    cmd_uri.handler = cmd_post;
    httpd_register_uri_handler(server, &cmd_uri);

    ESP_LOGI(TAG, "status server on :%d (/status, /cmd)", TWABR_STATUS_PORT);
}

#endif  // TWABR_STATUS_SERVER

// ---------------------------------------------------------------------------

static void heartbeat_task(void *)
{
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(TWABR_HEARTBEAT_MS));
        char ip[16];
        ip_str(ip, sizeof ip);
        cJSON *root = cJSON_CreateObject();
        cJSON_AddStringToObject(root, "t", "hb");
        cJSON_AddStringToObject(root, "role", "bridge");
        cJSON_AddBoolToObject(root, "wifi", net_up());
        cJSON_AddStringToObject(root, "ip", ip);
        uart_send_json(root);

        bool master_up = s_master_last_us &&
            (esp_timer_get_time() - s_master_last_us) < (int64_t)TWABR_MASTER_TIMEOUT_MS * 1000;
        if (!master_up) {
            ESP_LOGW(TAG, "no traffic from master in %d s — check the UART wiring "
                          "(TX/RX crossed? shared ground?)", TWABR_MASTER_TIMEOUT_MS / 1000);
        }
    }
}

extern "C" void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    led_init();
    uart_init();
    s_jobs = xQueueCreate(TWABR_JOB_QUEUE_DEPTH, sizeof(job_t));
    wifi_start();

    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    ESP_LOGW(TAG, "==== twab bridge  fw %s  MAC %02x:%02x:%02x:%02x:%02x:%02x ====",
             esp_app_get_description()->version,
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    xTaskCreate(wifi_supervisor_task, "twab_wifi", 3072, nullptr, 4, nullptr);
    xTaskCreate(http_worker_task, "twab_http", 6144, nullptr, 5, nullptr);
    xTaskCreate(uart_rx_task,     "twab_uart", 4096, nullptr, 6, nullptr);
    xTaskCreate(heartbeat_task,   "twab_hb",   3072, nullptr, 3, nullptr);
    xTaskCreate(ota_task,         "twab_ota",  8192, nullptr, 2, nullptr);

#if TWABR_STATUS_SERVER
    xEventGroupWaitBits(s_net, NET_UP, pdFALSE, pdTRUE, pdMS_TO_TICKS(20000));
    status_server_start();
#endif
}
