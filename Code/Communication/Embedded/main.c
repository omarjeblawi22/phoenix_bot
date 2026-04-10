/*
 * FTM Initiator – XIAO ESP32-S3
 *
 * Outputs two line types to serial:
 *
 *   Session summary (one per FTM session):
 *     FTM_S,<seq>,<timestamp_ms>,<rtt_raw_ns>,<rtt_est_ns>,<dist_cm>,<n_frames>,<status>
 *
 *   Per-frame detail (one per valid frame inside each session):
 *     FTM_F,<seq>,<frame_idx>,<rtt_ps>,<t1_ps>,<t2_ps>,<t3_ps>,<t4_ps>,<rssi>
 *
 * NOTE on units from esp_wifi_types_generic.h:
 *   wifi_ftm_report_entry_t.rtt  -> Round Trip Time in pSec (picoseconds)
 *   wifi_ftm_report_entry_t.t1/t2/t3/t4 -> all in pSec
 *   wifi_event_ftm_report_t.rtt_raw / rtt_est -> Nano-Seconds
 *
 * The Python logger converts rtt_ps -> ns by dividing by 1000.
 */

#include <stdio.h>
#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "nvs_flash.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"

/* ── Configuration ──────────────────────────────────────────────────────────── */
#define WIFI_SSID           "FTM_AP"
#define WIFI_PASS           "12345678"
#define WIFI_CHANNEL        6

/*
 * frm_count allowed values (from header): 0(No pref), 16, 24, 32, 64
 * These map to actual frame counts of: AP decides, 8, 12, 16, 32
 * Use 64 for maximum frames (32 actual frames per burst).
 */
#define FTM_FRMS_PER_BURST  64
#define FTM_BURST_PERIOD    0
#define MEASURE_INTERVAL_MS 500
#define MAX_RETRY           5

/* Maximum frames to store from one session */
#define MAX_FRAME_ENTRIES   64

/* ── Event-group bits ───────────────────────────────────────────────────────── */
#define WIFI_CONNECTED_BIT  BIT0
#define WIFI_FAIL_BIT       BIT1
#define FTM_DONE_BIT        BIT2

static EventGroupHandle_t s_wifi_evt_group;
static EventGroupHandle_t s_ftm_evt_group;

static const char *TAG       = "FTM_INITIATOR";
static int         s_retry_num = 0;
static uint32_t    s_seq       = 0;

static uint8_t  s_ap_bssid[6] = {0};
static uint8_t  s_ap_channel  = 0;

/*
 * Per-session snapshot.
 * wifi_ftm_status_t  is the correct type  (from esp_wifi_types_generic.h)
 * FTM_STATUS_*       are the correct enum values (no WIFI_ prefix)
 */
typedef struct {
    wifi_ftm_status_t            status;
    uint32_t                     rtt_raw;   /* ns */
    uint32_t                     rtt_est;   /* ns */
    uint32_t                     dist_est;  /* cm */
    uint8_t                      n_entries;
    wifi_ftm_report_entry_t      entries[MAX_FRAME_ENTRIES];
} ftm_snapshot_t;

static ftm_snapshot_t s_snapshot;

/* ── Event handler ──────────────────────────────────────────────────────────── */
static void event_handler(void *arg, esp_event_base_t event_base,
                          int32_t event_id, void *event_data)
{
    if (event_base == WIFI_EVENT) {
        switch (event_id) {
            case WIFI_EVENT_STA_START:
                esp_wifi_connect();
                break;

            case WIFI_EVENT_STA_DISCONNECTED:
                if (s_retry_num < MAX_RETRY) {
                    esp_wifi_connect();
                    s_retry_num++;
                    ESP_LOGW(TAG, "Retrying connection (%d/%d)...", s_retry_num, MAX_RETRY);
                } else {
                    xEventGroupSetBits(s_wifi_evt_group, WIFI_FAIL_BIT);
                    ESP_LOGE(TAG, "Failed to connect to AP");
                }
                break;

            case WIFI_EVENT_FTM_REPORT: {
                wifi_event_ftm_report_t *report = (wifi_event_ftm_report_t *)event_data;

                s_snapshot.status   = report->status;
                s_snapshot.rtt_raw  = report->rtt_raw;
                s_snapshot.rtt_est  = report->rtt_est;
                s_snapshot.dist_est = report->dist_est;
                s_snapshot.n_entries = 0;

                /*
                 * Field name from header: ftm_report_data (pointer)
                 * Count field:            ftm_report_num_entries
                 *
                 * IMPORTANT: this pointer is only valid inside this callback.
                 * We deep-copy the array before returning.
                 * use_get_report_api must be false (default) to use this pointer.
                 */
                if (report->ftm_report_num_entries > 0 &&
                    report->ftm_report_data != NULL)
                {
                    uint8_t n = (uint8_t)report->ftm_report_num_entries;
                    if (n > MAX_FRAME_ENTRIES) n = MAX_FRAME_ENTRIES;
                    memcpy(s_snapshot.entries,
                           report->ftm_report_data,
                           n * sizeof(wifi_ftm_report_entry_t));
                    s_snapshot.n_entries = n;
                }

                xEventGroupSetBits(s_ftm_evt_group, FTM_DONE_BIT);
                break;
            }

            default:
                break;
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        s_retry_num = 0;

        wifi_ap_record_t ap_info;
        if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
            memcpy(s_ap_bssid, ap_info.bssid, 6);
            s_ap_channel = ap_info.primary;
            char mac_str[18];
            snprintf(mac_str, sizeof(mac_str), "%02x:%02x:%02x:%02x:%02x:%02x",
                     s_ap_bssid[0], s_ap_bssid[1], s_ap_bssid[2],
                     s_ap_bssid[3], s_ap_bssid[4], s_ap_bssid[5]);
            ESP_LOGI(TAG, "AP MAC: %s  channel: %d", mac_str, s_ap_channel);
        } else {
            ESP_LOGW(TAG, "esp_wifi_sta_get_ap_info failed");
        }

        xEventGroupSetBits(s_wifi_evt_group, WIFI_CONNECTED_BIT);
    }
}

/* ── Wi-Fi init ─────────────────────────────────────────────────────────────── */
static bool wifi_init_sta(void)
{
    s_wifi_evt_group = xEventGroupCreate();

    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, NULL));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid        = WIFI_SSID,
            .password    = WIFI_PASS,
            .channel     = WIFI_CHANNEL,
            .scan_method = WIFI_FAST_SCAN,
        },
    };

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "Connecting to %s...", WIFI_SSID);

    EventBits_t bits = xEventGroupWaitBits(s_wifi_evt_group,
                                           WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(15000));

    if (bits & WIFI_CONNECTED_BIT) {
        ESP_LOGI(TAG, "Connected to AP");
        return true;
    }
    ESP_LOGE(TAG, "Could not connect to AP");
    return false;
}

/* ── FTM measurement task ───────────────────────────────────────────────────── */
static void ftm_measure_task(void *pvParam)
{
    s_ftm_evt_group = xEventGroupCreate();

    printf("# FTM_S_HEADER: seq,timestamp_ms,rtt_raw_ns,rtt_est_ns,dist_cm,n_frames,status\n");
    printf("# FTM_F_HEADER: seq,frame_idx,rtt_ps,t1_ps,t2_ps,t3_ps,t4_ps,rssi\n");
    fflush(stdout);

    while (true) {
        wifi_ftm_initiator_cfg_t ftm_cfg = {
            .resp_mac        = {0},
            .channel         = s_ap_channel,
            .frm_count       = FTM_FRMS_PER_BURST,
            .burst_period    = FTM_BURST_PERIOD,
            /*
             * use_get_report_api = false  → driver writes entries into
             * ftm_report_data inside the event, which we copy in the handler.
             * use_get_report_api = true   → entries must be fetched via
             * esp_wifi_ftm_get_report() instead (more complex, not needed here).
             */
            .use_get_report_api = false,
        };
        memcpy(ftm_cfg.resp_mac, s_ap_bssid, 6);

        esp_err_t err = esp_wifi_ftm_initiate_session(&ftm_cfg);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_wifi_ftm_initiate_session failed: %s", esp_err_to_name(err));
            vTaskDelay(pdMS_TO_TICKS(MEASURE_INTERVAL_MS));
            continue;
        }

        EventBits_t bits = xEventGroupWaitBits(s_ftm_evt_group, FTM_DONE_BIT,
                                               pdTRUE, pdFALSE,
                                               pdMS_TO_TICKS(5000));
        if (!(bits & FTM_DONE_BIT)) {
            ESP_LOGW(TAG, "FTM session timed out");
            esp_wifi_ftm_end_session();
            vTaskDelay(pdMS_TO_TICKS(MEASURE_INTERVAL_MS));
            continue;
        }

        int64_t ts_ms = esp_timer_get_time() / 1000LL;

        const char *status_str = "UNKNOWN";
        switch (s_snapshot.status) {
            case FTM_STATUS_SUCCESS:       status_str = "SUCCESS";     break;
            case FTM_STATUS_UNSUPPORTED:   status_str = "UNSUPPORTED"; break;
            case FTM_STATUS_CONF_REJECTED: status_str = "REJECTED";    break;
            case FTM_STATUS_NO_RESPONSE:   status_str = "NO_RESPONSE"; break;
            case FTM_STATUS_FAIL:          status_str = "FAIL";        break;
            default:                                                     break;
        }

        /* Session summary */
        printf("FTM_S,%" PRIu32 ",%" PRId64 ",%" PRIu32 ",%" PRIu32 ",%" PRIu32 ",%u,%s\n",
               s_seq,
               ts_ms,
               s_snapshot.rtt_raw,
               s_snapshot.rtt_est,
               s_snapshot.dist_est,
               s_snapshot.n_entries,
               status_str);

        /*
         * Per-frame lines.
         * All values (rtt, t1, t2, t3, t4) are in picoseconds per the header.
         * Python logger divides rtt by 1000 to get nanoseconds,
         * and verifies: rtt_ps == (t4-t1) - (t3-t2)
         */
        for (uint8_t i = 0; i < s_snapshot.n_entries; i++) {
            wifi_ftm_report_entry_t *e = &s_snapshot.entries[i];
            printf("FTM_F,%" PRIu32 ",%u,%" PRIu32
                   ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%d\n",
                   s_seq,
                   i,
                   e->rtt,
                   e->t1,
                   e->t2,
                   e->t3,
                   e->t4,
                   (int)e->rssi);
        }

        fflush(stdout);
        s_seq++;

        vTaskDelay(pdMS_TO_TICKS(MEASURE_INTERVAL_MS));
    }
}

/* ── app_main ───────────────────────────────────────────────────────────────── */
void app_main(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    if (wifi_init_sta()) {
        xTaskCreate(ftm_measure_task, "ftm_measure", 4096, NULL, 5, NULL);
    } else {
        ESP_LOGE(TAG, "Halting - could not connect to FTM AP");
    }
}