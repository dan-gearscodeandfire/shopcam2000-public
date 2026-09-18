idf_component_register(
    SRCS "main.cpp"
    INCLUDE_DIRS "."
    REQUIRES nvs_flash esp_wifi esp_netif esp_event esp_timer esp_adc esp_app_format
             driver twab_link
)

# Bench build: set TWABB_BENCH=1 in the environment for an always-awake image.
#
# Deep sleep un-enumerates this board's USB console on every wake -- the port IS
# the chip -- so bring-up against a deep-sleep image means chasing a COM port
# that disappears a second after boot. This keeps it up, without editing a
# committed header that someone then forgets to put back.
#
#   $env:TWABB_BENCH = '1'; idf.py build flash    # bench
#   Remove-Item Env:\TWABB_BENCH;  idf.py build   # shipping image
if(DEFINED ENV{TWABB_BENCH} AND NOT "$ENV{TWABB_BENCH}" STREQUAL "0")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE TWABB_DEEP_SLEEP=0)
    message(STATUS "twab_button: BENCH BUILD -- deep sleep disabled")
endif()
