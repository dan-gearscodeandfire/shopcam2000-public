# twab_master — ESP-NOW receiver, UART to the bridge (ESP32, ESP-IDF >= 5.0)
cmake_minimum_required(VERSION 3.16)

# Shared protocol component + the gitignored twab_secrets.h next to it.
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../common")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(twab_master)
