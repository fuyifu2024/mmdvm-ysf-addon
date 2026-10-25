.PHONY: help config preflight prep build install status uninstall test clean

help:
	@echo "MMDVM YSF add-on"
	@echo "  make config     validate config.env and render build/etc"
	@echo "  make preflight  read-only environment/conflict checks"
	@echo "  make prep       enable UART/dependencies (sudo)"
	@echo "  make build      fetch pinned upstream, apply patches, build"
	@echo "  make install    install and start the three services (sudo)"
	@echo "  make status     show services, logs and hardware state"
	@echo "  make uninstall  stop and remove the add-on (sudo)"
	@echo "  make test       syntax + hardware-free config/platform dry-runs"
	@echo "  make clean      remove build artifacts"

config:
	python3 scripts/render_configs.py

preflight:
	./scripts/preflight.sh

prep:
	sudo ./scripts/prepare-system.sh

build:
	./scripts/build.sh

install:
	sudo ./scripts/install-services.sh

status:
	./scripts/status.sh

uninstall:
	sudo ./scripts/uninstall.sh

test:
	for s in scripts/*.sh; do bash -n "$$s"; done
	python3 -m py_compile scripts/render_configs.py oled/oled_display.py
	python3 scripts/render_configs.py --config config.example.env --check --quiet
	MMDVM_PLATFORM=x86 ./scripts/prepare-system.sh --dry-run --skip-preflight

clean:
	rm -rf build
