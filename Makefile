.PHONY: help setup onboard update version config preflight prep build install status uninstall test clean

help:
	@echo "MMDVM hotspot add-on"
	@echo "  ./setup.sh      guided first installation (recommended)"
	@echo "  ./mmdvm onboard quick configuration wizard (current values as defaults)"
	@echo "  ./mmdvm update  lossless update: pull, snapshot, build, rollback on failure"
	@echo "  ./mmdvm status  same as make status"
	@echo "  ./mmdvm version repository + installed version"
	@echo "  make setup      same as ./setup.sh"
	@echo "  make config     validate config.env and render build/etc"
	@echo "  make preflight  read-only environment/conflict checks"
	@echo "  make prep       enable UART/dependencies (sudo)"
	@echo "  make build      fetch pinned upstream, apply patches, build"
	@echo "  make install    install and start the add-on services (sudo)"
	@echo "  make status     show services, logs and hardware state"
	@echo "  make uninstall  stop and remove the add-on (sudo)"
	@echo "  make test       syntax, unit tests and hardware-free dry-runs"
	@echo "  make clean      remove build artifacts"

setup:
	./setup.sh

onboard:
	./mmdvm onboard

update:
	./mmdvm update

version:
	./mmdvm version

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
	for s in mmdvm scripts/*.sh setup.sh; do bash -n "$$s"; done
	./setup.sh --dry-run
	python3 -m py_compile scripts/*.py oled/oled_display.py
	python3 -m unittest discover -s tests -v
	./mmdvm --help >/dev/null
	./mmdvm onboard --help >/dev/null
	./mmdvm update --help >/dev/null
	python3 scripts/render_configs.py --config config.example.env --check --quiet
	MMDVM_PLATFORM=x86 ./scripts/prepare-system.sh --dry-run --skip-preflight

clean:
	rm -rf build
