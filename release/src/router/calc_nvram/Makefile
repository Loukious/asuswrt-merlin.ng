include ../common.mak

CACL_FLAGS += -Wall -O

ifeq ($(RTCONFIG_JFFS_NVRAM),y)
CACL_FLAGS += -DRTCONFIG_JFFS_NVRAM
endif

all: clean run_python calc_nvram

calc_nvram: calc_nvram.o
	@echo " [calc_nvram] CC $@"
	gcc -o $@ calc_nvram.o $(EXTRALDFLAGS)
	$(TOP)/calc_nvram/calc_nvram

run_python:
	@-type -P python >/dev/null 2>&1 && python $(TOP)/calc_nvram/calc_nvram.py $(TOP)
	@if [ ! -f defaults_nv.h ]; then \
		echo " [calc_nvram] fallback generate defaults_nv.h"; \
		echo '#include "calc_nvram.h"' > defaults_nv.h; \
		awk 'BEGIN{f=0} /struct nvram_tuple router_defaults\[\] = \{/ {f=1} f {print} f && /^\};/ {exit}' $(TOP)/shared/defaults.prep >> defaults_nv.h; \
		if [ -f $(TOP)/shared/nvpriv.prep ]; then \
			awk 'BEGIN{f=0} /static char \*large_nvram_list\[\] = \{/ {f=1} f {sub(/^static /,""); print} f && /^\};/ {exit}' $(TOP)/shared/nvpriv.prep >> defaults_nv.h; \
		else \
			echo 'char *large_nvram_list[] = {NULL};' >> defaults_nv.h; \
		fi; \
	fi

install:

clean:
	rm -f calc_nvram *.o defaults_nv.h

%.o: %.c
	@echo " [calc_nvram] CC $@"
	gcc $(CACL_FLAGS) -o $@ -c $<
