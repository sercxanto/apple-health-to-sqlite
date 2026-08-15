PYTHON ?= python3
DEMO_DIR ?= demo
PORT ?= 8001

.PHONY: help setup test test-all lint format check demo serve clean

help:  ## Show this help
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

setup:  ## Install the package and dev tools in editable mode
	$(PYTHON) -m pip install -e ".[dev]"

test:  ## Run the fast test suite
	$(PYTHON) -m unittest discover -s tests -t . -v

test-all:  ## Run every test, including the slow large-export test
	APPLEHEALTH_SLOW_TESTS=1 $(PYTHON) -m unittest discover -s tests -t . -v

lint:  ## Check formatting and lint rules
	ruff check .
	ruff format --check .

format:  ## Apply formatting and safe lint fixes
	ruff format .
	ruff check --fix .

check: lint test  ## What CI runs, and what "done" means

demo:  ## Generate a synthetic export, import it, and serve it
	@mkdir -p $(DEMO_DIR)
	$(PYTHON) -m applehealth generate --days 120 $(DEMO_DIR)/export.zip
	$(PYTHON) -m applehealth import $(DEMO_DIR)/export.zip $(DEMO_DIR)/health.db
	$(MAKE) serve

serve:  ## Serve an already-built demo database
	datasette $(DEMO_DIR)/health.db -m datasette/metadata.yml \
		--setting sql_time_limit_ms 10000 -p $(PORT)

clean:  ## Remove build artifacts and the demo directory
	rm -rf $(DEMO_DIR) build dist *.egg-info .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
