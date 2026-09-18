// twab_master — pins and tuning. No secrets here (those live in
// firmware/common/twab_secrets.h); this file is committed on purpose.
#pragma once

// ---- UART link to the bridge ----------------------------------------------
// UART0 is the console. Use UART1 or UART2 and cross the wires:
//   master TX -> bridge RX, master RX -> bridge TX, GND -> GND.
// The two boards MUST share a ground; without it the link works on the bench
// (shared USB ground through the laptop) and fails the moment they're on
// separate supplies.
#define TWABM_UART_PORT       UART_NUM_2
#define TWABM_UART_TX_GPIO    17
#define TWABM_UART_RX_GPIO    16
#define TWABM_UART_BAUD       115200

// ---- Status LED (-1 to disable) --------------------------------------------
// Blinks once per accepted frame; a slow double-blink means the bridge has
// gone quiet.
#define TWABM_LED_GPIO        2

// ---- Fleet ------------------------------------------------------------------
// ESP-NOW itself allows 20 peers on ESP32; this is the table the master keeps
// for routing acks and commands back to nodes.
#define TWABM_MAX_PEERS       16

// How long without a line from the bridge before the link counts as down.
#define TWABM_BRIDGE_TIMEOUT_MS   30000
// Master heartbeat out to the bridge.
#define TWABM_HEARTBEAT_MS        10000

// Depth of the RX hand-off queue. The ESP-NOW receive callback runs in the
// WiFi task and must not block or do work, so it only enqueues; everything
// else happens in the forward task.
#define TWABM_RX_QUEUE_DEPTH  12

// Longest NDJSON line accepted from the bridge.
#define TWABM_LINE_MAX        512
