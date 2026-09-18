// TWAB — the "That Was Awesome" button.
//
// A battery ESP32 that spends its life in deep sleep. Press the button and it
// wakes, joins WiFi, POSTs one request to the Shopcam 2000 Controller — which
// flushes every *overwatch* camera's 1-minute pre-trigger buffer into a saved
// clip — and goes back to sleep. Total awake time is dominated by WiFi
// association (~1-3 s with a static IP).
//
// Wake sources and what they do:
//   EXT1 (button, short press)  -> POST TWAB_PRESS_PATH, sleep.
//   EXT1 (button held >3 s)     -> maintenance mode: stays awake for
//                                  TWAB_MAINT_WINDOW_S, mirrors logs over UDP,
//                                  checks the server for a new firmware build
//                                  and OTA-updates if one is published.
//   Timer (every 6 h)           -> heartbeat: POST telemetry (battery, RSSI,
//                                  boot count), then the same OTA check. This
//                                  is how new firmware rolls out without
//                                  anyone touching the button.
//
// Debug surfaces, because there is no USB cable in the field:
//   * every wake POSTs a JSON telemetry blob (wake reason, vbat, RSSI,
//     awake-time, firmware version) — the server log is the monitor;
//   * while awake, all esp_log output is mirrored via UDP to the server
//     (port TWAB_UDP_LOG_PORT) — `nc -ul 5514` and watch;
//   * OTA uses A/B slots with rollback: a new image that fails to complete a
//     telemetry POST is rolled back by the bootloader on next boot.

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cinttypes>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"

#include "driver/gpio.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "lwip/sockets.h"

#include "twab_config.h"

static const char *TAG = "twab";

// Survives deep sleep; zeroed only on power loss / reset button.
RTC_DATA_ATTR static uint32_t s_boot_count = 0;
RTC_DATA_ATTR static uint32_t s_press_count = 0;
RTC_DATA_ATTR static uint32_t s_fail_count = 0;

static EventGroupHandle_t s_net_events;
static constexpr EventBits_t NET_CONNECTED = BIT0;
static constexpr EventBits_t NET_FAILED = BIT1;

// ---------------------------------------------------------------------------
// UDP log mirror — esp_log writes to UART *and* to the server while awake.
// ---------------------------------------------------------------------------

static int s_udp_sock = -1;
static struct sockaddr_in s_udp_dest;
static vprintf_like_t s_prev_vprintf = nullptr;

static int udp_log_vprintf(const char *fmt, va_list args)
{
    // Two consumers of the varargs: vsnprintf must eat a va_copy, because
    // passing an already-consumed va_list to the UART vprintf below is UB
    // (C11 7.16.1p3) — it happens to work on Xtensa's by-value va_list and
    // silently breaks on any ABI where va_list is a mutated pointer.
    char buf[256];
    va_list copy;
    va_copy(copy, args);
    int n = vsnprintf(buf, sizeof buf, fmt, copy);
    va_end(copy);
    if (n > 0 && s_udp_sock >= 0) {
        sendto(s_udp_sock, buf, (n < (int)sizeof buf) ? n : sizeof buf, 0,
               (struct sockaddr *)&s_udp_dest, sizeof s_udp_dest);
    }
    return s_prev_vprintf ? s_prev_vprintf(fmt, args) : n;
}

static void udp_log_start()
{
    // Reuse the server host from TWAB_SERVER_BASE ("http://a.b.c.d:port").
    char host[32] = {0};
    const char *p = strstr(TWAB_SERVER_BASE, "//");
    if (!p) return;
    p += 2;
    size_t i = 0;
    while (p[i] && p[i] != ':' && p[i] != '/' && i < sizeof host - 1) {
        host[i] = p[i];
        i++;
    }
    host[i] = 0;

    // The mirror needs a numeric a.b.c.d; a hostname would silently become
    // INADDR_NONE = 255.255.255.255 and the logs would broadcast (or drop).
    uint32_t addr = inet_addr(host);
    if (addr == INADDR_NONE) {
        ESP_LOGW(TAG, "UDP log mirror disabled: %s is not a numeric IP", host);
        return;
    }

    s_udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (s_udp_sock < 0) return;
    memset(&s_udp_dest, 0, sizeof s_udp_dest);
    s_udp_dest.sin_family = AF_INET;
    s_udp_dest.sin_port = htons(TWAB_UDP_LOG_PORT);
    s_udp_dest.sin_addr.s_addr = addr;
    s_prev_vprintf = esp_log_set_vprintf(udp_log_vprintf);
    ESP_LOGI(TAG, "UDP log mirror -> %s:%d", host, TWAB_UDP_LOG_PORT);
}

// ---------------------------------------------------------------------------
// WiFi
// ---------------------------------------------------------------------------

static void wifi_event_handler(void *, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        static int retries = 0;
        if (retries++ < 4) {
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_net_events, NET_FAILED);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        xEventGroupSetBits(s_net_events, NET_CONNECTED);
    }
}

// Split into start + wait so association can overlap the button-hold
// detection in app_main: the radio associates while we watch the GPIO,
// making awake time max(hold, wifi) instead of hold + wifi.
static void wifi_start()
{
    s_net_events = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *netif = esp_netif_create_default_wifi_sta();

#if TWAB_USE_STATIC_IP
    esp_netif_dhcpc_stop(netif);
    esp_netif_ip_info_t ip = {};
    ip.ip.addr = ipaddr_addr(TWAB_STATIC_IP);
    ip.gw.addr = ipaddr_addr(TWAB_STATIC_GW);
    ip.netmask.addr = ipaddr_addr(TWAB_STATIC_MASK);
    esp_netif_set_ip_info(netif, &ip);
    // No DHCP means no DNS server either; point DNS at the gateway so a
    // hostname in TWAB_SERVER_BASE still resolves instead of failing every
    // request. (The UDP log mirror still wants a numeric IP.)
    esp_netif_dns_info_t dns = {};
    dns.ip.type = ESP_IPADDR_TYPE_V4;
    dns.ip.u_addr.ip4.addr = ip.gw.addr;
    esp_netif_set_dns_info(netif, ESP_NETIF_DNS_MAIN, &dns);
#endif

    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                               &wifi_event_handler, nullptr));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               &wifi_event_handler, nullptr));

    wifi_config_t cfg = {};
    strlcpy((char *)cfg.sta.ssid, TWAB_WIFI_SSID, sizeof cfg.sta.ssid);
    strlcpy((char *)cfg.sta.password, TWAB_WIFI_PASSWORD, sizeof cfg.sta.password);
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    // Persisting config to NVS costs flash writes on every wake; skip it.
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_start());
}

static bool wifi_wait()
{
    EventBits_t bits = xEventGroupWaitBits(s_net_events, NET_CONNECTED | NET_FAILED,
                                           pdFALSE, pdFALSE, pdMS_TO_TICKS(10000));
    bool ok = bits & NET_CONNECTED;
    ESP_LOGI(TAG, "wifi %s", ok ? "connected" : "FAILED");
    return ok;
}

// ---------------------------------------------------------------------------
// Sensors / status
// ---------------------------------------------------------------------------

static int read_vbat_mv()
{
#if TWAB_VBAT_ADC_GPIO >= 0
    adc_oneshot_unit_handle_t unit;
    adc_unit_t unit_id;
    adc_channel_t channel;
    if (adc_oneshot_io_to_channel(TWAB_VBAT_ADC_GPIO, &unit_id, &channel) != ESP_OK) {
        return -1;
    }
    adc_oneshot_unit_init_cfg_t ucfg = {};
    ucfg.unit_id = unit_id;
    if (adc_oneshot_new_unit(&ucfg, &unit) != ESP_OK) return -1;
    adc_oneshot_chan_cfg_t ccfg = {};
    ccfg.atten = ADC_ATTEN_DB_12;      // full-scale ~3.3 V at the pin
    ccfg.bitwidth = ADC_BITWIDTH_12;
    adc_oneshot_config_channel(unit, channel, &ccfg);
    int raw = 0;
    adc_oneshot_read(unit, channel, &raw);
    adc_oneshot_del_unit(unit);
    // Uncalibrated but plenty to watch a battery sag over weeks.
    int pin_mv = raw * 3300 / 4095;
    return (int)(pin_mv * TWAB_VBAT_DIVIDER);
#else
    return -1;
#endif
}

static int wifi_rssi()
{
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

static const char *wake_reason_str(esp_sleep_wakeup_cause_t cause)
{
    switch (cause) {
    case ESP_SLEEP_WAKEUP_EXT1:  return "button";
    case ESP_SLEEP_WAKEUP_TIMER: return "heartbeat";
    default:                     return "power-on";
    }
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

static int build_telemetry(char *out, size_t cap, const char *reason)
{
    uint8_t mac[6] = {};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    const esp_app_desc_t *app = esp_app_get_description();
    return snprintf(out, cap,
        "{\"device\":\"twab-%02x%02x%02x\",\"fw\":\"%s\",\"wake\":\"%s\","
        "\"boot_count\":%" PRIu32 ",\"press_count\":%" PRIu32
        ",\"fail_count\":%" PRIu32 ",\"vbat_mv\":%d,\"rssi\":%d,"
        "\"awake_ms\":%lld}",
        mac[3], mac[4], mac[5], app->version, reason,
        s_boot_count, s_press_count, s_fail_count,
        read_vbat_mv(), wifi_rssi(), esp_timer_get_time() / 1000);
}

static bool http_post_json(const char *path, const char *body)
{
    char url[160];
    snprintf(url, sizeof url, "%s%s", TWAB_SERVER_BASE, path);

    for (int attempt = 1; attempt <= TWAB_HTTP_RETRIES; attempt++) {
        esp_http_client_config_t cfg = {};
        cfg.url = url;
        cfg.method = HTTP_METHOD_POST;
        cfg.timeout_ms = TWAB_HTTP_TIMEOUT_MS;
        esp_http_client_handle_t client = esp_http_client_init(&cfg);
        if (!client) continue;
        esp_http_client_set_header(client, "Content-Type", "application/json");
        esp_http_client_set_post_field(client, body, strlen(body));
        esp_err_t err = esp_http_client_perform(client);
        int status = esp_http_client_get_status_code(client);
        esp_http_client_cleanup(client);
        if (err == ESP_OK && status >= 200 && status < 300) {
            ESP_LOGI(TAG, "POST %s -> %d (attempt %d)", path, status, attempt);
            return true;
        }
        ESP_LOGW(TAG, "POST %s failed: %s / HTTP %d (attempt %d)",
                 path, esp_err_to_name(err), status, attempt);
        // 4xx = the server understood and refused (bad config, empty
        // overwatch set): re-sending the identical request cannot succeed,
        // and /api/twab is not idempotent — retries there re-trigger
        // cameras. Only transport errors and 5xx are worth another attempt.
        if (err == ESP_OK && status >= 400 && status < 500) {
            return false;
        }
        if (attempt < TWAB_HTTP_RETRIES) {
            vTaskDelay(pdMS_TO_TICKS(250 * attempt));
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
// OTA — pull-style: server publishes version.txt + twab.bin; device compares
// and updates itself. Publishing a build == dropping two files on the server.
// ---------------------------------------------------------------------------

static bool fetch_remote_version(char *out, size_t cap)
{
    char url[160];
    snprintf(url, sizeof url, "%s%s", TWAB_SERVER_BASE, TWAB_OTA_MANIFEST);
    esp_http_client_config_t cfg = {};
    cfg.url = url;
    cfg.timeout_ms = TWAB_HTTP_TIMEOUT_MS;
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) return false;

    bool ok = false;
    if (esp_http_client_open(client, 0) == ESP_OK) {
        esp_http_client_fetch_headers(client);
        int n = esp_http_client_read_response(client, out, cap - 1);
        if (esp_http_client_get_status_code(client) == 200 && n > 0) {
            out[n] = 0;
            // trim trailing whitespace/newline
            while (n > 0 && (out[n-1] == '\n' || out[n-1] == '\r' || out[n-1] == ' '))
                out[--n] = 0;
            ok = n > 0;
        }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ok;
}

static void ota_check_and_update()
{
    char remote[32];
    if (!fetch_remote_version(remote, sizeof remote)) {
        ESP_LOGI(TAG, "ota: no version manifest on server, skipping");
        return;
    }
    const char *local = esp_app_get_description()->version;
    if (strcmp(remote, local) == 0) {
        ESP_LOGI(TAG, "ota: up to date (%s)", local);
        return;
    }
    ESP_LOGW(TAG, "ota: %s -> %s, updating", local, remote);

    char url[160];
    snprintf(url, sizeof url, "%s%s", TWAB_SERVER_BASE, TWAB_OTA_BINARY);
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

// ---------------------------------------------------------------------------
// Sleep
// ---------------------------------------------------------------------------

[[noreturn]] static void go_to_sleep()
{
    ESP_LOGI(TAG, "sleeping (boot %" PRIu32 ", awake %lld ms)",
             s_boot_count, esp_timer_get_time() / 1000);
    esp_wifi_stop();
    esp_sleep_enable_ext1_wakeup(1ULL << TWAB_BUTTON_GPIO,
                                 ESP_EXT1_WAKEUP_ANY_HIGH);
    esp_sleep_enable_timer_wakeup(
        (uint64_t)TWAB_HEARTBEAT_HOURS * 3600ULL * 1000000ULL);
    esp_deep_sleep_start();
}

static void led(bool on)
{
#if TWAB_LED_GPIO >= 0
    gpio_set_direction((gpio_num_t)TWAB_LED_GPIO, GPIO_MODE_OUTPUT);
    gpio_set_level((gpio_num_t)TWAB_LED_GPIO, on);
#endif
}

// Distinguish tap from hold at wake time: the press that woke us is either
// already over (tap) or still in progress (potential maintenance hold).
static bool button_held_for(uint32_t ms)
{
    gpio_set_direction((gpio_num_t)TWAB_BUTTON_GPIO, GPIO_MODE_INPUT);
    gpio_set_pull_mode((gpio_num_t)TWAB_BUTTON_GPIO, GPIO_PULLDOWN_ONLY);
    uint32_t held = 0;
    while (gpio_get_level((gpio_num_t)TWAB_BUTTON_GPIO)) {
        vTaskDelay(pdMS_TO_TICKS(50));
        held += 50;
        if (held >= ms) return true;
    }
    return false;
}

// ---------------------------------------------------------------------------

extern "C" void app_main(void)
{
    s_boot_count++;
    esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
    const char *reason = wake_reason_str(cause);
    ESP_LOGI(TAG, "wake #%" PRIu32 ": %s", s_boot_count, reason);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    // Start the radio first: association (~1-3 s) runs while we watch the
    // GPIO for a maintenance hold, instead of after it.
    wifi_start();
    bool maintenance = (cause == ESP_SLEEP_WAKEUP_EXT1) &&
                       button_held_for(TWAB_LONG_PRESS_MS);

    if (!wifi_wait()) {
        s_fail_count++;
        // Nothing we can do without the network; note it and go back to sleep.
        // The failure shows up in the next successful telemetry POST.
        go_to_sleep();
    }
    udp_log_start();

    // Count the press before the telemetry serializes the counters, so the
    // press POST reports the press it belongs to (not one behind).
    bool press = !maintenance && (cause == ESP_SLEEP_WAKEUP_EXT1);
    if (press) s_press_count++;

    char body[320];
    build_telemetry(body, sizeof body, reason);

    if (maintenance) {
        // -------- maintenance mode: long OTA/debug window ------------------
        led(true);
        ESP_LOGW(TAG, "maintenance mode for %d s", TWAB_MAINT_WINDOW_S);
        // Same rule as every other branch: the image proves itself with one
        // successful POST. Marking valid unconditionally here would let a
        // broken image (wrong server address) defeat the bootloader rollback
        // the moment someone holds the button to debug it.
        if (http_post_json(TWAB_TELEMETRY_PATH, body)) {
            esp_ota_mark_app_valid_cancel_rollback();
        }
        int64_t until = esp_timer_get_time() + (int64_t)TWAB_MAINT_WINDOW_S * 1000000;
        while (esp_timer_get_time() < until) {
            ota_check_and_update();          // reboots on success
            vTaskDelay(pdMS_TO_TICKS(30000));
        }
        led(false);
    } else if (press) {
        // -------- the entire point: THAT WAS AWESOME -----------------------
        led(true);
        bool ok = http_post_json(TWAB_PRESS_PATH, body);
        if (ok) {
            esp_ota_mark_app_valid_cancel_rollback();
        } else {
            s_fail_count++;
        }
        led(false);
    } else {
        // -------- heartbeat: telemetry + quiet OTA window ------------------
        bool ok = http_post_json(TWAB_TELEMETRY_PATH, body);
        if (ok) esp_ota_mark_app_valid_cancel_rollback();
        ota_check_and_update();              // reboots on success
    }

    go_to_sleep();
}
