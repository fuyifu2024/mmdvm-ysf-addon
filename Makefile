.PHONY: help config preflight prep build install status uninstall clean

help:
	@echo "MMDVM YSF add-on"
	@echo "  make config     validate config.env and render build/etc"
	@echo "  make preflight  read-only environment/conflict checks"
	@echo "  make prep       enable UART, install build dependencies (sudo)"
	@echo "  make build      fetch pinned upstream, apply patches, build"
	@echo "  make install    install and start the three services (sudo)"
	@echo "  make status     show services, logs and hardware state"
	@echo "  make uninstall  stop and remove the add-on (sudo)"
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

clean:
	rm -rf build
