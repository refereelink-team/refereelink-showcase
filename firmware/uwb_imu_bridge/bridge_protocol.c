#include "bridge_protocol.h"
#include <string.h>

uint16_t rl_bridge_crc16(const uint8_t *data, size_t size)
{
    uint16_t crc = 0xffffu;
    size_t i;
    unsigned bit;
    for (i = 0; i < size; ++i) {
        crc ^= (uint16_t)((uint16_t)data[i] << 8);
        for (bit = 0; bit < 8u; ++bit)
            crc = (uint16_t)((crc & 0x8000u) ? ((uint32_t)crc << 1) ^ 0x1021u : (uint32_t)crc << 1);
    }
    return crc;
}

static int raw_valid(const uint8_t *raw, size_t size)
{
    uint8_t sum = 0;
    size_t i;
    if (raw == NULL || size != RL_IMU_RAW_SIZE ||
        raw[0] != 0x7eu || raw[1] != 0x23u || raw[2] != 23u || raw[3] != 4u)
        return 0;
    for (i = 0; i < size - 1u; ++i)
        sum = (uint8_t)(sum + raw[i]);
    return sum == raw[size - 1u];
}

int rl_bridge_valid(const uint8_t *frame, size_t size)
{
    uint16_t stored;
    if (frame == NULL || size != RL_BRIDGE_FRAME_SIZE ||
        frame[0] != 'm' || frame[1] != 'i' || frame[2] != 1u ||
        frame[3] == 0u || frame[10] != RL_IMU_RAW_SIZE)
        return 0;
    stored = (uint16_t)((uint16_t)frame[34] | ((uint16_t)frame[35] << 8));
    return stored == rl_bridge_crc16(frame, 34u) && raw_valid(frame + 11u, RL_IMU_RAW_SIZE);
}

int rl_bridge_encode(uint8_t *out, size_t capacity, uint8_t tag_id,
                     uint16_t sequence, uint32_t sample_ms,
                     const uint8_t *raw, size_t raw_size)
{
    uint16_t crc;
    unsigned i;
    if (out == NULL || capacity < RL_BRIDGE_FRAME_SIZE || tag_id == 0u || !raw_valid(raw, raw_size))
        return 0;
    out[0] = 'm'; out[1] = 'i'; out[2] = 1u; out[3] = tag_id;
    out[4] = (uint8_t)sequence; out[5] = (uint8_t)(sequence >> 8);
    for (i = 0; i < 4u; ++i)
        out[6u + i] = (uint8_t)(sample_ms >> (8u * i));
    out[10] = RL_IMU_RAW_SIZE;
    memcpy(out + 11u, raw, RL_IMU_RAW_SIZE);
    crc = rl_bridge_crc16(out, 34u);
    out[34] = (uint8_t)crc; out[35] = (uint8_t)(crc >> 8);
    return (int)RL_BRIDGE_FRAME_SIZE;
}
