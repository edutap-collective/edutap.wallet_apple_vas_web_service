# Tools run from .venv by path, not through `uv run`: a bare `uv run` re-locks the
# project on every invocation, which is a network round trip in the middle of a lint.
PYTHON := .venv/bin/python
VENV   := .venv

.DEFAULT_GOAL := help
.PHONY: help venv lint typecheck reformat test-local test-integration docker-build run

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  %-18s %s\n", $$1, $$2}'

venv: ## Create .venv and install the package with its dev group
	test -d $(VENV) || uv venv
	uv pip install -U -e ".[fastapi,sql]" --group dev

lint: venv ## Run ruff checks (blocking)
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

# Separate from `lint`, and not yet blocking. `ty` reports 17 errors, and every one
# of them is a real defect this package already knows about: a Pass API that no
# longer exists (`files_uuencoded`, `pass_json`, `Pass.create`), `settings.apple.*`
# fields that were renamed, `.first()` on an already-materialised sequence, and the
# `kafka_producer` module that imports a distribution nobody declared. Silencing
# them with 17 ignore comments would hide exactly the list that has to be worked
# off; making them block would stop every unrelated change until it is. So it runs,
# it is read, and it turns blocking when the rework lands.
typecheck: venv ## Run the type checker (reports known defects, non-blocking)
	-$(PYTHON) -m ty check src

reformat: venv ## Autoformat and autofix
	$(PYTHON) -m ruff format src tests
	$(PYTHON) -m ruff check --fix src tests

test-local: venv ## Unit tests, no database needed
	$(PYTHON) -m pytest -v

# Needs a reachable PostgreSQL. Point WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN at a
# throwaway database -- these tests create and drop tables.
test-integration: venv ## Tests against a real PostgreSQL
	$(PYTHON) -m pytest -m integration -v

# The image is what reaches the cluster, and it is where this service last broke:
# a build that succeeds proves the pins resolve and the entry point imports. The
# --platform flag matters on an Apple Silicon machine -- every cluster node is
# x86_64, and without it the image fails at start with "exec format error".
docker-build: ## Build the container image for the cluster's architecture
	docker build --platform linux/amd64 -t edutap-wallet-apple-vas-web-service:local .

run: venv ## Start the service locally on port 8084
	$(PYTHON) -m uvicorn edutap.wallet_apple_vas_web_service.standalone:app --reload --port 8084
