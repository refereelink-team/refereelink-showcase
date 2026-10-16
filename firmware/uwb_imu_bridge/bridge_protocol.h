#ifndef RL_BRIDGE_PROTOCOL_H
#define RL_BRIDGE_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define RL_IMU_RAW_SIZE 23u
#define RL_BRIDGE_FRAME_SIZE 36u

/* Portable codec only: no STM32 UART, radio scheduling or flashable firmware. */
uint16_t rl_bridge_crc16(const uint8_t *data, size_t size);
int rl_bridge_valid(const uint8_t *frame, size_t size);
int rl_bridge_encode(uint8_t *out, size_t capacity, uint8_t tag_id,
                     uint16_t sequence, uint32_t sample_ms,
                     const uint8_t *raw, size_t raw_size);

#endif
