
MODULE_NAME=wfd

ifeq ($(WFD_BUILD_FROM_SOURCE),1)
$(MODULE_NAME)-objs += wfd_dev.o
ifneq ($(strip $(CONFIG_BCM_WFD_RATE_LIMITER)),)
$(MODULE_NAME)-objs += rate_limiter.o
endif

EXTRA_CFLAGS += -I$(obj)
# Only this source-built WFD module uses the verified BCM4908 GPL headers.
EXTRA_CFLAGS += -I$(src)/bdmf-compat/framework
EXTRA_CFLAGS += -I$(src)/bdmf-compat/system
EXTRA_CFLAGS += -I$(src)/bdmf-compat/system/linux
EXTRA_CFLAGS += -I$(src)/bdmf-compat/system/linux/ce
EXTRA_CFLAGS += -I$(src)/runner-compat
EXTRA_CFLAGS += -DWL4908
EXTRA_CFLAGS += -I$(HND_SRC)/rdp/drivers/rdp_subsystem
EXTRA_CFLAGS += -I$(HND_SRC)/rdp/drivers/rdp_subsystem/rdp
EXTRA_CFLAGS += -I$(INC_BRCMDRIVER_PRIV_PATH)/$(BRCM_BOARD)
EXTRA_CFLAGS += -I$(BRCMDRIVERS_DIR)/opensource/net/enet/shared
EXTRA_CFLAGS += -I$(INC_RDPA_MW_PATH)
EXTRA_CFLAGS += -I$(BRCMDRIVERS_DIR)/broadcom/net/wl/bcm9$(BRCM_CHIP)/include
EXTRA_CFLAGS += -I$(BRCMDRIVERS_DIR)/broadcom/net/wl/bcm9$(BRCM_CHIP)/main/src/include
EXTRA_CFLAGS += -I$(BRCMDRIVERS_DIR)/broadcom/net/wl/shared/impl1
EXTRA_CFLAGS += -DDSLCPE -DBCMDRIVER -DPKTC
ifneq ($(strip $(CONFIG_BCM_PKTFWD)),)
EXTRA_CFLAGS += -DBCM_PKTFWD
ifneq ($(strip $(CONFIG_BCM_PKTFWD_FLCTL)),)
EXTRA_CFLAGS += -DBCM_PKTFWD_FLCTL
endif
endif
EXTRA_CFLAGS += -I$(INC_BRCMSHARED_PUB_PATH)/bcm963xx
EXTRA_CFLAGS += -I$(INC_BRCMDRIVER_PUB_PATH)/bcm963xx
EXTRA_CFLAGS += $(INC_RDP_FLAGS) -Wall -g
else
$(MODULE_NAME)-objs += $(MODULE_NAME)$(PRBM_EXT).o
endif

obj-$(CONFIG_BCM_WIFI_FORWARDING_DRV) += $(MODULE_NAME).o

clean:
	rm -f core *.o *.a *.s .*.cmd *.ko

