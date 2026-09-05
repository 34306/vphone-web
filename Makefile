# vphone-web — web platform only. The firmware/VM build lives in vendor/vphone-cli.
.PHONY: help web_setup web_run web_seed_admin

VENV ?= .venv
PY   := $(VENV)/bin/python

# Load .env if present (export all vars to child processes).
ifneq (,$(wildcard .env))
include .env
export
endif

help:
	@echo "vphone-web targets:"
	@echo "  make web_setup                 Create .venv and install web deps"
	@echo "  make web_seed_admin USER=.. PASS=..   Create the first admin account"
	@echo "  make web_run                   Run the web server (reads .env)"
	@echo ""
	@echo "VM images are built in the vphone-cli submodule — see README.md."

web_setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt
	@echo "Done. Copy .env.example to .env and edit it, then: make web_run"

web_seed_admin:
	@if [ -z "$(USER)" ] || [ -z "$(PASS)" ]; then \
		echo "Usage: make web_seed_admin USER=admin PASS=yourpassword"; exit 1; fi
	$(PY) -m web.app.seed --user "$(USER)" --pass "$(PASS)" --role admin

web_run:
	$(PY) -m uvicorn web.app.main:app \
		--host $(or $(VPHONE_HOST),127.0.0.1) \
		--port $(or $(VPHONE_PORT),8080)
