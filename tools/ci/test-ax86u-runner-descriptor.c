/*
 * Exercise the production Runner descriptor declaration against the original
 * RT-AX86U wfd.o DWARF. No replacement descriptor definition is used here.
 * The access_macros.h dependency contains target ARM/MMIO operations; its
 * include guard suppresses those operations for this userspace layout test.
 */
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

#define __ACCESS_MACROS_H_INCLUDED
#define _BYTE_ORDER_LITTLE_ENDIAN_
#include "rdp_cpu_ring_defs.h"

#define CHECK_BITS(word, field, shift, width) do { \
    const uint32_t field_max = UINT32_MAX >> (32 - (width)); \
    const uint32_t values[] = {0, field_max, field_max >> 1, \
        field_max ^ (field_max >> 1)}; \
    size_t i; \
    for (i = 0; i < sizeof(values) / sizeof(values[0]); ++i) { \
        CPU_RX_DESCRIPTOR descriptor = {0}; \
        descriptor.word = values[i] << (shift); \
        if (descriptor.field != values[i]) { \
            fprintf(stderr, "Runner decode layout mismatch: %s\n", #field); \
            return 1; \
        } \
        descriptor.word = 0; \
        descriptor.field = values[i]; \
        if (descriptor.word != (values[i] << (shift))) { \
            fprintf(stderr, "Runner encode layout mismatch: %s\n", #field); \
            return 1; \
        } \
    } \
} while (0)

int main(void)
{
    CPU_RX_DESCRIPTOR descriptor = {0};

    if (OWNERSHIP_RUNNER != 0 || OWNERSHIP_HOST != 1)
        return 1;

    CHECK_BITS(word0, packet_length, 0, 14);
    CHECK_BITS(word0, source_port, 14, 5);
    CHECK_BITS(word0, is_chksum_verified, 19, 1);
    CHECK_BITS(word0, flow_id, 20, 12);
    CHECK_BITS(word0, pkt_length, 0, 14);
    CHECK_BITS(word0, src_port, 14, 5);
    CHECK_BITS(word0, is_checksum_verified, 19, 1);
    CHECK_BITS(word0, reserved9, 20, 1);
    CHECK_BITS(word0, is_ipsec_upstream, 21, 1);
    CHECK_BITS(word0, cpu_rx_queue, 22, 4);
    CHECK_BITS(word0, reserved8, 26, 2);
    CHECK_BITS(word0, ipsec_error, 28, 3);
    CHECK_BITS(word0, reserved7, 31, 1);

    CHECK_BITS(word1, descriptor_type, 0, 4);
    CHECK_BITS(word1, is_exception, 4, 1);
    CHECK_BITS(word1, reserved1, 5, 4);
    CHECK_BITS(word1, dst_ssid, 9, 16);
    CHECK_BITS(word1, reason, 25, 6);
    CHECK_BITS(word1, payload_offset_flag, 31, 1);

    CHECK_BITS(word2, host_buffer_data_pointer, 0, 31);
    CHECK_BITS(word2, ownership, 31, 1);

    CHECK_BITS(word3, flow_ring_idx, 0, 10);
    CHECK_BITS(word3, ssid, 10, 4);
    CHECK_BITS(word3, reserved6, 14, 2);
    CHECK_BITS(word3, wl_chain_id, 0, 14);
    CHECK_BITS(word3, wl_tx_priority, 14, 2);
    CHECK_BITS(word3, ip_sync_1588_idx, 16, 4);
    CHECK_BITS(word3, reserved4, 20, 6);
    CHECK_BITS(word3, wl_tx_prio, 26, 4);
    CHECK_BITS(word3, is_ucast, 30, 1);
    CHECK_BITS(word3, is_rx_offload, 31, 1);

    descriptor.word2 = UINT32_C(0x12345678);
    if (descriptor.data_ptr != UINT32_C(0x12345678))
        return 1;
    descriptor.word3 = UINT32_C(0x5a5aa55a);
    if (descriptor.wl_metadata != UINT16_C(0xa55a) ||
        descriptor.free_index != UINT16_C(0xa55a) ||
        descriptor.ssid_vector != UINT16_C(0xa55a))
        return 1;

    puts("RT-AX86U Runner descriptor offsets, aliases and bitfields verified.");
    return 0;
}
