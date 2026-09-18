// twab_button — the physical "That Was Awesome" button. An ESP-NOW node.
//
// One GPIO, one LED, no WiFi credentials, no IP stack. It wakes on the button,
// sends one authenticated ESP-NOW frame to the master, waits for two acks, and
// goes back to sleep:
//
//   ack 1 ("link", a few ms)  the master heard the press      -> short blink
//   ack 2 ("done", ~1-3 s)    the Controller triggered Blue   -> long solid
//                             Iris and the moment is on disk
//
// The two-stage ack is the whole reason this feels like a button rather than a
// switch you hope did something. Waiting for a single ack means standing in the
// shop for three seconds wondering; the first blink comes back instantly and
// says "heard you", and the second closes the loop.
//
// Retries reuse the SAME sequence number deliberately. The master acks
// duplicates but forwards only the first, so a lost ack costs another radio
// frame and never a second clip.
//
// The button holds no secret except the shared link key — lose it in a snowbank
// and nothing on the shop network is exposed.

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cinttypes>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"

#include "driver/gpio.h"
#include "driver/rtc_io.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_now.h"
#include "esp_random.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "nvs_flash.h"

#include "button_config.h"
#include "twab_link.h"
#include "twab_secrets.h"

static const char *TAG = "button";

static const uint8_t BROADCAST[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

// Survives deep sleep, cleared by power loss — which is exactly the lifetime
// the (session, seq) pair needs: a new battery means a new session, and the
// master adopts it without treating the counter reset as a replay.
RTC_DATA_ATTR static uint32_t s_session = 0;
RTC_DATA_ATTR static uint32_t s_seq = 0;
RTC_DATA_ATTR static uint32_t s_boot_count = 0;
RTC_DATA_ATTR static uint32_t s_press_count = 0;
RTC_DATA_ATTR static uint32_t s_fail_count = 0;

static EventGroupHandle_t s_events;
static constexpr EventBits_t EV_TX_DONE  = BIT0;   // MAC-layer delivery result
static constexpr EventBits_t EV_TX_FAIL  = BIT1;
static constexpr EventBits_t EV_ACK_LINK = BIT2;   // master heard us
static constexpr EventBits_t EV_ACK_DONE = BIT3;   // Controller answered

static volatile uint32_t s_awaiting_seq = 0;
static volatile bool     s_result_ok = false;
static volatile int      s_result_code = 0;

static uint16_t s_node_id;
static uint8_t  s_master_mac[6];
static bool     s_have_master = false;

// ---------------------------------------------------------------------------
// LED — the entire user interface.
//
// The whole vocabulary, and the rule behind it: a pattern's SPEED says what
// kind of problem it is, so you never have to count blinks in a dim shop.
//
//   solid 600 ms            SAVED — the moment is on disk
//   3 x 400 ms (slow)       nothing on Watch (409) — fix it at the PC
//   2 x 150 ms              the save itself failed — Blue Iris said no
//   long, then 3 x 70 ms    no verdict — bridge or Controller is down
//   4 x 70 ms (fastest)     the master never heard the press — radio
//
// Slow = a setting. Fast = a fault. The faster it stutters, the further from
// the disk the press died.
// ---------------------------------------------------------------------------

static void led_init()
{
#if TWABB_LED_GPIO >= 0
    gpio_reset_pin((gpio_num_t)TWABB_LED_GPIO);
    gpio_set_direction((gpio_num_t)TWABB_LED_GPIO, GPIO_MODE_OUTPUT);
#endif
}

static void led(bool on)
{
#if TWABB_LED_GPIO >= 0
    gpio_set_level((gpio_num_t)TWABB_LED_GPIO, TWABB_LED_ACTIVE_LOW ? !on : on);
#else
    (void)on;
#endif
}

static void led_blink(int times, int ms)
{
    for (int i = 0; i < times; i++) {
        led(true);
        vTaskDelay(pdMS_TO_TICKS(ms));
        led(false);
        if (i + 1 < times) vTaskDelay(pdMS_TO_TICKS(ms));
    }
}

static void led_saved()      { led(true); vTaskDelay(pdMS_TO_TICKS(600)); led(false); }
static void led_unconfirmed(){ led_blink(2, 150); }
static void led_nobody_home(){ led_blink(4, 70); }

// Nothing is on Watch (HTTP 409). The press was heard, understood, and refused
// — by a checkbox on the PC, not by anything wrong out here. Deliberately the
// SLOWEST pattern in the vocabulary: every other failure is a stutter, this one
// is three long deliberate pulses. Slow means "go look at the GUI", and it is
// the one failure you can fix without a meter or a serial cable.
//
// 2026-08-03: this case used to share led_unconfirmed() with a genuine save
// failure. An empty Watch list then looked identical to Blue Iris refusing the
// clip, and it cost a debugging session — the button was blamed for a setting.
static void led_not_watching(){ led_blink(3, 400); }

// Heard by the master, then silence: no verdict inside TWABB_RESULT_ACK_MS, so
// the bridge or the Controller is down. A long "heard you" followed by a fast
// stutter — the SHAPE says the press got somewhere and then fell off a cliff,
// which is what separates it from led_nobody_home()'s pure stutter (the master
// never heard it at all).
static void led_no_verdict()
{
    led(true);
    vTaskDelay(pdMS_TO_TICKS(400));
    led(false);
    vTaskDelay(pdMS_TO_TICKS(200));
    led_blink(3, 70);
}

// ---------------------------------------------------------------------------
// Button — internal pull-up, active low, sampled debounce.
// ---------------------------------------------------------------------------

static void button_init()
{
    gpio_config_t cfg = {};
    cfg.pin_bit_mask = 1ULL << TWABB_BUTTON_GPIO;
    cfg.mode = GPIO_MODE_INPUT;
    cfg.pull_up_en = TWABB_ACTIVE_LOW ? GPIO_PULLUP_ENABLE : GPIO_PULLUP_DISABLE;
    cfg.pull_down_en = TWABB_ACTIVE_LOW ? GPIO_PULLDOWN_DISABLE : GPIO_PULLDOWN_ENABLE;
    cfg.intr_type = GPIO_INTR_DISABLE;
    ESP_ERROR_CHECK(gpio_config(&cfg));
}

static bool button_raw()
{
    int level = gpio_get_level((gpio_num_t)TWABB_BUTTON_GPIO);
    return TWABB_ACTIVE_LOW ? (level == 0) : (level != 0);
}

// True once TWABB_DEBOUNCE_SAMPLES consecutive samples agree with `want`.
// Returns false as soon as one disagrees — a bounce, or a wake caused by
// electrical noise on a long button run rather than by a finger.
static bool button_stable(bool want)
{
    for (int i = 0; i < TWABB_DEBOUNCE_SAMPLES; i++) {
        if (button_raw() != want) return false;
        vTaskDelay(pdMS_TO_TICKS(TWABB_SAMPLE_MS));
    }
    return button_raw() == want;
}

// Waits for a debounced release so holding the button down can never queue a
// second press when the chip wakes again. Returns false if it gave up.
//
// Bounded, because "held forever" is a real state: a jammed switch, a shorted
// run of button wire, or a jumper left across the pins on the bench. Unbounded
// this spun the main task until the task watchdog reset the chip, which re-read
// the pin as pressed and fired another press — a press every five seconds for
// as long as the contact stayed closed.
static bool button_wait_release()
{
    int64_t give_up = esp_timer_get_time() + (int64_t)TWABB_RELEASE_TIMEOUT_MS * 1000;
    while (!button_stable(false)) {
        if (esp_timer_get_time() > give_up) {
            ESP_LOGW(TAG, "button still down after %d ms — treating it as stuck",
                     TWABB_RELEASE_TIMEOUT_MS);
            return false;
        }
        vTaskDelay(pdMS_TO_TICKS(TWABB_SAMPLE_MS));
    }
    return true;
}

// ---------------------------------------------------------------------------
// Battery
// ---------------------------------------------------------------------------

static int read_vbat_mv()
{
#if TWABB_VBAT_ADC_GPIO >= 0
    adc_oneshot_unit_handle_t unit;
    adc_unit_t unit_id;
    adc_channel_t channel;
    if (adc_oneshot_io_to_channel(TWABB_VBAT_ADC_GPIO, &unit_id, &channel) != ESP_OK) return -1;
    adc_oneshot_unit_init_cfg_t ucfg = {};
    ucfg.unit_id = unit_id;
    if (adc_oneshot_new_unit(&ucfg, &unit) != ESP_OK) return -1;
    adc_oneshot_chan_cfg_t ccfg = {};
    ccfg.atten = ADC_ATTEN_DB_12;
    ccfg.bitwidth = ADC_BITWIDTH_12;
    adc_oneshot_config_channel(unit, channel, &ccfg);
    int raw = 0;
    adc_oneshot_read(unit, channel, &raw);
    adc_oneshot_del_unit(unit);
    // Uncalibrated, but plenty to watch a cell sag over weeks.
    return (int)((raw * 3300 / 4095) * TWABB_VBAT_DIVIDER);
#else
    return -1;
#endif
}

// ---------------------------------------------------------------------------
// ESP-NOW
// ---------------------------------------------------------------------------

// IDF v5.5 changed this callback's first argument from the peer MAC to a
// wifi_tx_info_t; the signature must match exactly or registration won't build.
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
static void on_sent(const wifi_tx_info_t *, esp_now_send_status_t status)
#else
static void on_sent(const uint8_t *, esp_now_send_status_t status)
#endif
{
    xEventGroupSetBits(s_events,
        status == ESP_NOW_SEND_SUCCESS ? EV_TX_DONE : EV_TX_FAIL);
}

static void on_recv(const esp_now_recv_info_t *, const uint8_t *data, int len)
{
    if (len <= 0 || !twab_link_verify(data, (size_t)len)) return;
    const twab_hdr_t *hdr = twab_link_hdr(data);
    if (hdr->msg != TWAB_MSG_ACK) return;
    if (hdr->ref_seq != s_awaiting_seq) return;   // an ack for someone else's press

    size_t plen = 0;
    const char *payload = twab_link_payload(data, &plen);
    char buf[TWAB_LINK_MAX_PAYLOAD + 1];
    memcpy(buf, payload, plen);
    buf[plen] = 0;

    // Deliberately not pulling a JSON parser onto this board for two fields.
    if (strstr(buf, "\"stage\":\"done\"")) {
        s_result_ok = strstr(buf, "\"ok\":1") != nullptr;
        const char *c = strstr(buf, "\"code\":");
        s_result_code = c ? atoi(c + 7) : 0;
        xEventGroupSetBits(s_events, EV_ACK_DONE);
    } else {
        xEventGroupSetBits(s_events, EV_ACK_LINK);
    }
}

static void espnow_start()
{
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    // No association, so nothing will move us off the fleet channel — and
    // nothing costs the ~1-3 s a WiFi join would put between the press and the
    // save. This is the entire reason the button speaks ESP-NOW.
    ESP_ERROR_CHECK(esp_wifi_set_channel(TWAB_ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE));
#if TWAB_ESPNOW_LONG_RANGE
    ESP_ERROR_CHECK(esp_wifi_set_protocol(WIFI_IF_STA,
        WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N | WIFI_PROTOCOL_LR));
#endif

    ESP_ERROR_CHECK(esp_now_init());
    ESP_ERROR_CHECK(esp_now_register_send_cb(on_sent));
    ESP_ERROR_CHECK(esp_now_register_recv_cb(on_recv));

    esp_now_peer_info_t peer = {};
    peer.channel = 0;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;

    s_have_master = twab_link_mac_parse(TWABB_MASTER_MAC, s_master_mac) &&
                    memcmp(s_master_mac, "\0\0\0\0\0\0", 6) != 0;
    if (s_have_master) {
        memcpy(peer.peer_addr, s_master_mac, 6);
        ESP_ERROR_CHECK(esp_now_add_peer(&peer));
    } else {
        ESP_LOGW(TAG, "no master MAC configured — broadcasting. Set "
                      "TWABB_MASTER_MAC for link-layer retries.");
    }
    memcpy(peer.peer_addr, BROADCAST, 6);
    ESP_ERROR_CHECK(esp_now_add_peer(&peer));      // fallback path, always armed
}

// One send attempt. `to` is the master or the broadcast address.
static void send_frame(const uint8_t *to, twab_msg_t msg, uint32_t seq, const char *payload)
{
    uint8_t frame[TWAB_LINK_MAX_FRAME];
    size_t n = twab_link_build(frame, sizeof frame, msg, TWAB_DEV_BUTTON,
                               s_node_id, s_session, seq, 0, payload);
    if (!n) { ESP_LOGE(TAG, "payload too long"); return; }
    esp_err_t err = esp_now_send(to, frame, n);
    if (err != ESP_OK) ESP_LOGW(TAG, "esp_now_send: %s", esp_err_to_name(err));
}

static void build_payload(char *out, size_t cap)
{
    snprintf(out, cap,
             "{\"fw\":\"%s\",\"vbat_mv\":%d,\"boot\":%" PRIu32
             ",\"press\":%" PRIu32 ",\"fail\":%" PRIu32 "}",
             esp_app_get_description()->version, read_vbat_mv(),
             s_boot_count, s_press_count, s_fail_count);
}

// Send one message and wait for the master's "heard you". Returns true if the
// master acknowledged. The seq is fixed by the caller and reused across
// retries so the master can dedupe.
static bool send_and_confirm(twab_msg_t msg, uint32_t seq, const char *payload)
{
    for (int attempt = 1; attempt <= TWABB_SEND_RETRIES; attempt++) {
        xEventGroupClearBits(s_events, EV_TX_DONE | EV_TX_FAIL | EV_ACK_LINK);

        // Last attempt falls back to broadcast: if the master was reflashed
        // its MAC may have changed, and a button that only ever unicasts to a
        // stale address is a button that is simply broken.
        const uint8_t *to = (s_have_master && attempt < TWABB_SEND_RETRIES)
                          ? s_master_mac : BROADCAST;
        send_frame(to, msg, seq, payload);

        EventBits_t bits = xEventGroupWaitBits(s_events, EV_ACK_LINK,
                                               pdFALSE, pdFALSE,
                                               pdMS_TO_TICKS(TWABB_LINK_ACK_MS));
        if (bits & EV_ACK_LINK) {
            ESP_LOGI(TAG, "%s seq %" PRIu32 " acked by master (attempt %d)",
                     twab_msg_name(msg), seq, attempt);
            return true;
        }
        ESP_LOGW(TAG, "no ack for %s seq %" PRIu32 " (attempt %d)",
                 twab_msg_name(msg), seq, attempt);
    }
    return false;
}

// ---------------------------------------------------------------------------
// A press, start to finish.
// ---------------------------------------------------------------------------

static void do_press()
{
    s_press_count++;
    uint32_t seq = ++s_seq;
    s_awaiting_seq = seq;
    s_result_ok = false;
    s_result_code = 0;
    // The verdict bit outlives the press that set it. Deep-sleep builds get a
    // fresh event group every wake and never notice, but in always-awake mode
    // — the bench mode — the second press would read the first one's bit,
    // return from the wait instantly, and report a failure that never
    // happened. Clear it here rather than in send_and_confirm's retry loop, so
    // a verdict that arrives early still counts.
    xEventGroupClearBits(s_events, EV_ACK_DONE);

    char payload[TWAB_LINK_MAX_PAYLOAD];
    build_payload(payload, sizeof payload);

    if (!send_and_confirm(TWAB_MSG_PRESS, seq, payload)) {
        s_fail_count++;
        ESP_LOGE(TAG, "master never answered");
        led_nobody_home();
        return;
    }
    led_blink(1, 60);

    // The master relays the Controller's verdict once Blue Iris has been
    // triggered on every overwatch camera, which is a few seconds of work.
    EventBits_t bits = xEventGroupWaitBits(s_events, EV_ACK_DONE, pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(TWABB_RESULT_ACK_MS));
    if ((bits & EV_ACK_DONE) && s_result_ok) {
        ESP_LOGI(TAG, "SAVED (HTTP %d)", s_result_code);
        led_saved();
    } else if (bits & EV_ACK_DONE) {
        s_fail_count++;
        // 409 is the Controller's "no overwatch cameras selected". It is a
        // configuration answer, not a fault, and it gets its own pattern so the
        // person standing at the button knows to walk to the PC rather than
        // start suspecting the radio.
        if (s_result_code == 409) {
            ESP_LOGE(TAG, "NOTHING ON WATCH (HTTP 409) — put cameras on Watch in the GUI");
            led_not_watching();
        } else {
            ESP_LOGE(TAG, "save FAILED (HTTP %d)", s_result_code);
            led_unconfirmed();
        }
    } else {
        s_fail_count++;
        ESP_LOGW(TAG, "no verdict — bridge or Controller is down");
        led_no_verdict();
    }
}

// Long hold: announce this node to the master. Registers it in the peer table
// and, more usefully, proves radio range from wherever you're standing.
static void do_hello()
{
    uint32_t seq = ++s_seq;
    s_awaiting_seq = seq;
    char payload[TWAB_LINK_MAX_PAYLOAD];
    build_payload(payload, sizeof payload);
    bool ok = send_and_confirm(TWAB_MSG_HELLO, seq, payload);
    ESP_LOGI(TAG, "hello %s", ok ? "acked" : "unanswered");
    if (ok) led_blink(3, 100); else led_nobody_home();
}

static void do_telemetry()
{
    uint32_t seq = ++s_seq;
    s_awaiting_seq = seq;
    char payload[TWAB_LINK_MAX_PAYLOAD];
    build_payload(payload, sizeof payload);
    send_frame(s_have_master ? s_master_mac : BROADCAST, TWAB_MSG_TELEMETRY, seq, payload);
    vTaskDelay(pdMS_TO_TICKS(200));    // let the radio finish before sleeping
}

// ---------------------------------------------------------------------------
// Sleep
// ---------------------------------------------------------------------------

#if TWABB_DEEP_SLEEP
[[noreturn]] static void go_to_sleep()
{
    ESP_LOGI(TAG, "sleeping (boot %" PRIu32 ", awake %lld ms)",
             s_boot_count, esp_timer_get_time() / 1000);
    led(false);
    esp_wifi_stop();

    // The internal pull-up has to be re-armed in the RTC domain: the normal
    // GPIO pull-ups are powered down in deep sleep, and without this the pin
    // floats and the board wakes on nothing at all — or on everything.
    rtc_gpio_pullup_en((gpio_num_t)TWABB_BUTTON_GPIO);
    rtc_gpio_pulldown_dis((gpio_num_t)TWABB_BUTTON_GPIO);
    esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);

    // Arming ext0 against a pin that is ALREADY at the wake level wakes the
    // chip the instant it sleeps — press, sleep, wake, press, forever, on a
    // battery. So a stuck contact gets the timer instead, and re-checks in a
    // minute rather than waiting out the heartbeat.
    bool still_down = button_raw();
    if (!still_down) {
        esp_sleep_enable_ext0_wakeup((gpio_num_t)TWABB_BUTTON_GPIO, TWABB_ACTIVE_LOW ? 0 : 1);
#if TWABB_HEARTBEAT_HOURS > 0
        esp_sleep_enable_timer_wakeup((uint64_t)TWABB_HEARTBEAT_HOURS * 3600ULL * 1000000ULL);
#endif
    } else {
        ESP_LOGW(TAG, "button still down at sleep — timer only, re-checking in %d s",
                 TWABB_STUCK_RECHECK_S);
        esp_sleep_enable_timer_wakeup((uint64_t)TWABB_STUCK_RECHECK_S * 1000000ULL);
    }
    esp_deep_sleep_start();
}
#endif

// ---------------------------------------------------------------------------

extern "C" void app_main(void)
{
    s_boot_count++;
    esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
    (void)cause;                       // unused in always-awake builds

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ESP_ERROR_CHECK(nvs_flash_init());
    }

    uint8_t key[TWAB_LINK_KEY_LEN];
    if (!twab_link_key_from_hex(TWAB_LINK_KEY_HEX, key)) {
        ESP_LOGE(TAG, "TWAB_LINK_KEY_HEX is not 64 hex chars — fix twab_secrets.h");
        abort();
    }
    twab_link_set_key(key);

    if (s_session == 0) s_session = esp_random();   // fresh battery, fresh session

    s_events = xEventGroupCreate();
    led_init();
    button_init();
    espnow_start();

    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    s_node_id = twab_node_id_from_mac(mac);
    char mac_str[18];
    twab_link_mac_str(mac, mac_str, sizeof mac_str);
    ESP_LOGW(TAG, "==== twab button %04x  MAC %s  channel %d  wake #%" PRIu32 " ====",
             s_node_id, mac_str, TWAB_ESPNOW_CHANNEL, s_boot_count);
    // Print the pins. button_config.h selects them on the build target, and a
    // wrong-target build does not fail loudly: GPIO 33 exists on the S3 too
    // (it is OPI PSRAM territory), so the classic-ESP32 block would compile
    // clean and quietly wire the button to nothing. Cheaper to read it off the
    // banner than to infer it from the build.
    ESP_LOGW(TAG, "     %s - button GPIO %d, LED GPIO %d, vbat GPIO %d",
             TWABB_BOARD, (int)TWABB_BUTTON_GPIO, (int)TWABB_LED_GPIO,
             (int)TWABB_VBAT_ADC_GPIO);

#if TWABB_DEEP_SLEEP
    if (cause == ESP_SLEEP_WAKEUP_TIMER) {
        do_telemetry();
        go_to_sleep();
    }

    // A wake is a claim, not a press: confirm with the debouncer before
    // spending radio time. Contact bounce and a long run of button wire both
    // produce wakes that no human caused.
    if (cause == ESP_SLEEP_WAKEUP_EXT0 && !button_stable(true)) {
        ESP_LOGW(TAG, "spurious wake, back to sleep");
        go_to_sleep();
    }
    if (cause != ESP_SLEEP_WAKEUP_EXT0 && !button_stable(true)) {
        // Cold boot with the button not held: announce and sleep.
        do_hello();
        go_to_sleep();
    }

    do_press();

    // Still held after the press? Treat it as the long-press gesture.
    int64_t held_until = esp_timer_get_time() + (int64_t)TWABB_LONG_PRESS_MS * 1000;
    bool still = true;
    while (esp_timer_get_time() < held_until && still) {
        still = button_raw();
        vTaskDelay(pdMS_TO_TICKS(TWABB_SAMPLE_MS * 4));
    }
    if (still && button_stable(true)) do_hello();

    button_wait_release();
    vTaskDelay(pdMS_TO_TICKS(TWABB_REPRESS_LOCKOUT_MS));
    go_to_sleep();

#else
    // ---- always-awake mode (USB power) -------------------------------------
    // Same behaviour, polled. Costs ~80 mA but survives a bench session with a
    // serial monitor attached, which deep sleep does not.
    do_hello();
    bool was_down = false;
    int64_t next_hb = esp_timer_get_time() + (int64_t)TWABB_HEARTBEAT_HOURS * 3600000000LL;
    for (;;) {
        bool down = button_raw();
        if (down && !was_down && button_stable(true)) {
            do_press();
            int64_t held_until = esp_timer_get_time() + (int64_t)TWABB_LONG_PRESS_MS * 1000;
            bool still = true;
            while (esp_timer_get_time() < held_until && still) {
                still = button_raw();
                vTaskDelay(pdMS_TO_TICKS(TWABB_SAMPLE_MS * 4));
            }
            if (still && button_stable(true)) do_hello();
            bool released = button_wait_release();
            vTaskDelay(pdMS_TO_TICKS(TWABB_REPRESS_LOCKOUT_MS));
            // Stay latched down if it never came up: clearing was_down against
            // a contact that is still closed re-fires the press immediately,
            // which is the same runaway the deep-sleep path guards against.
            was_down = !released;
            continue;
        }
        was_down = down;
#if TWABB_HEARTBEAT_HOURS > 0
        if (esp_timer_get_time() > next_hb) {
            do_telemetry();
            next_hb = esp_timer_get_time() + (int64_t)TWABB_HEARTBEAT_HOURS * 3600000000LL;
        }
#endif
        vTaskDelay(pdMS_TO_TICKS(TWABB_SAMPLE_MS));
    }
#endif
}
