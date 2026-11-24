/* SPDX-License-Identifier: GPL-2.0 */
#ifndef WFD_BDMF_ABI_H
#define WFD_BDMF_ABI_H

/* RT-AX86U AArch64 prebuilt WFD DWARF is the layout reference. These
 * assertions are compiled with the actual target kernel/RDPA headers.
 */
#define WFD_BDMF_ASSERT(name, condition) \
    typedef char wfd_bdmf_abi_##name[(condition) ? 1 : -1]
#define WFD_BDMF_OFFSET(type, field, offset) \
    WFD_BDMF_ASSERT(type##_##field, offsetof(type, field) == (offset))

WFD_BDMF_ASSERT(pointer_size, sizeof(void *) == 8);
WFD_BDMF_ASSERT(number_size, sizeof(bdmf_number) == 8);
WFD_BDMF_ASSERT(number_signed, (bdmf_number)-1 < 0);
WFD_BDMF_ASSERT(index_size, sizeof(bdmf_index) == 8);
WFD_BDMF_ASSERT(index_signed, (bdmf_index)-1 < 0);
WFD_BDMF_ASSERT(boolean_size, sizeof(bdmf_boolean) == 1);
WFD_BDMF_ASSERT(physical_address_size, sizeof(bdmf_phys_addr_t) == 4);
WFD_BDMF_ASSERT(attribute_id_size, sizeof(bdmf_attr_id) == 2);
WFD_BDMF_ASSERT(attribute_type_size, sizeof(bdmf_attr_type_t) == 4);
WFD_BDMF_ASSERT(attribute_type_values,
    bdmf_attr_number == 0 && bdmf_attr_string == 1 && bdmf_attr_buffer == 2 &&
    bdmf_attr_pointer == 3 && bdmf_attr_object == 4 && bdmf_attr_ether_addr == 5 &&
    bdmf_attr_ip_addr == 6 && bdmf_attr_ipv4_addr == 7 && bdmf_attr_ipv6_addr == 8 &&
    bdmf_attr_boolean == 9 && bdmf_attr_enum == 10 && bdmf_attr_dyn_enum == 11 &&
    bdmf_attr_enum_mask == 12 && bdmf_attr_aggregate == 13 &&
    bdmf_attr_custom == 14 && bdmf_attr_last_type == 15);
WFD_BDMF_ASSERT(attribute_value_size, sizeof(bdmf_attr_val_t) == 24);
WFD_BDMF_OFFSET(bdmf_attr_val_t, val_type, 0);
WFD_BDMF_OFFSET(bdmf_attr_val_t, x, 8);
WFD_BDMF_ASSERT(mattr_size, sizeof(bdmf_mattr_t) == 32);
WFD_BDMF_OFFSET(bdmf_mattr_t, magic, 0);
WFD_BDMF_OFFSET(bdmf_mattr_t, oper, 4);
WFD_BDMF_OFFSET(bdmf_mattr_t, drv, 8);
WFD_BDMF_OFFSET(bdmf_mattr_t, max_entries, 16);
WFD_BDMF_OFFSET(bdmf_mattr_t, num_entries, 20);
WFD_BDMF_OFFSET(bdmf_mattr_t, dynamic, 24);
WFD_BDMF_OFFSET(bdmf_mattr_t, entries, 32);
WFD_BDMF_ASSERT(mattr_entry_size, sizeof(bdmf_mattr_entry_t) == 40);
WFD_BDMF_OFFSET(bdmf_mattr_entry_t, aid, 0);
WFD_BDMF_OFFSET(bdmf_mattr_entry_t, index, 8);
WFD_BDMF_OFFSET(bdmf_mattr_entry_t, val, 16);

/* The target's existing RDPA aggregate is passed to the BDMF binary. */
WFD_BDMF_ASSERT(rxq_configuration_size, sizeof(rdpa_cpu_rxq_cfg_t) == 80);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, rx_isr, 0);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, ring_head, 8);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, size, 16);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, type, 20);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, dump, 24);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, isr_priv, 32);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, ic_cfg, 40);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, rx_dump_data_cb, 48);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, rxq_stat, 56);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, irq_affinity_mask, 64);
WFD_BDMF_OFFSET(rdpa_cpu_rxq_cfg_t, ring_prio, 72);

WFD_BDMF_ASSERT(buffer_allocator_signature,
    __builtin_types_compatible_p(typeof(&bdmf_sysb_databuf_alloc),
        uint32_t (*)(void **, uint32_t, uint32_t, unsigned long)));
WFD_BDMF_ASSERT(buffer_free_signature,
    __builtin_types_compatible_p(typeof(&bdmf_sysb_databuf_free),
        void (*)(void *, unsigned long)));
WFD_BDMF_ASSERT(skb_allocator_signature,
    __builtin_types_compatible_p(typeof(&bdmf_skb_header_alloc),
        struct sk_buff *(*)(uint32_t)));
WFD_BDMF_ASSERT(skb_initializer_signature,
    __builtin_types_compatible_p(typeof(&bdmf_skb_headerinit),
        void (*)(struct sk_buff *, void *, uint32_t)));
WFD_BDMF_ASSERT(get_buffer_signature,
    __builtin_types_compatible_p(typeof(&bdmf_attrelem_get_as_buf),
        int (*)(bdmf_object_handle, bdmf_attr_id, bdmf_index, void *, uint32_t)));
WFD_BDMF_ASSERT(set_buffer_signature,
    __builtin_types_compatible_p(typeof(&bdmf_attrelem_set_as_buf),
        int (*)(bdmf_object_handle, bdmf_attr_id, bdmf_index, const void *, uint32_t)));
WFD_BDMF_ASSERT(new_object_signature,
    __builtin_types_compatible_p(typeof(&bdmf_new_and_set),
        int (*)(bdmf_type_handle, bdmf_object_handle,
            bdmf_mattr_handle, bdmf_object_handle *)));
WFD_BDMF_ASSERT(recycler_signature,
    __builtin_types_compatible_p(typeof(&bdmf_sysb_recycle),
        void (*)(bdmf_sysb, unsigned long, uint32_t)));
WFD_BDMF_ASSERT(lock_signature,
    __builtin_types_compatible_p(typeof(&bdmf_lock), int (*)(void)));
WFD_BDMF_ASSERT(unlock_signature,
    __builtin_types_compatible_p(typeof(&bdmf_unlock), void (*)(void)));

#undef WFD_BDMF_OFFSET
#undef WFD_BDMF_ASSERT
#endif
