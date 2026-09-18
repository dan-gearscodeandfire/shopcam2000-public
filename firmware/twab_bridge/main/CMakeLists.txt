idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_wifi esp_netif esp_event esp_timer esp_app_format
             driver json lwip
             esp_http_client esp_http_server esp_https_ota app_update
)
