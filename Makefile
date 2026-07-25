# AI Harness Registry — development loop.
#
# The whole pipeline runs offline against the fixture harnesses in
# examples/harnesses/, so you can work on the registry without a GitLab
# instance. `make all` is what CI runs, in the same order.

PY      ?= python3
HUGO    ?= hugo
SITE    ?= site
PUBLIC  ?= $(SITE)/public
BUILD   ?= build
PORT    ?= 1313

.DEFAULT_GOAL := help
.PHONY: help all validate validate-fixtures crawl index emit site search verify verify-snapshot verify-site \
        test fixtures serve dev clean check-offline stats

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2}'

all: validate-fixtures index emit site verify ## Everything CI runs, in CI's order

## --- pipeline -------------------------------------------------------------

validate: ## Validate every fixture harness (what a contributor runs locally)
	$(PY) -m tools.registryctl validate examples/harnesses

# The fixture tree contains one deliberately-broken manifest, so that the error
# path is exercised on every build. This target asserts *exactly* that one fails:
# a new failure and an unexpectedly-fixed fixture both need a human.
EXPECTED_INVALID ?= meeting-notes
validate-fixtures: | $(BUILD) ## Validate fixtures, expecting only the known-bad one to fail
	@$(PY) -m tools.registryctl validate examples/harnesses > $(BUILD)/validate.log 2>&1; \
	 actual=$$(grep '^FAIL' $(BUILD)/validate.log | awk '{print $$2}' | sort | tr '\n' ' ' | sed 's/ $$//'); \
	 cat $(BUILD)/validate.log; \
	 if [ "$$actual" != "$(EXPECTED_INVALID)" ]; then \
	   echo "expected only [$(EXPECTED_INVALID)] to fail validation, got [$$actual]"; exit 1; \
	 fi; \
	 echo "validation as expected: only $(EXPECTED_INVALID) fails"

$(BUILD):
	@mkdir -p $(BUILD)

crawl: | $(BUILD) ## Discover harnesses from the local fixture tree
	$(PY) -m tools.registryctl crawl --harnesses examples/harnesses --out $(BUILD)/crawl

crawl-gitlab: ## Discover harnesses from GitLab (needs REGISTRY_READ_TOKEN)
	$(PY) -m tools.registryctl crawl --groups groups.yaml --out $(BUILD)/crawl

index: crawl ## Build the snapshot: cards, graph, catalogue, index report
	$(PY) -m tools.registryctl index --crawl $(BUILD)/crawl --out $(BUILD)/snapshot

emit: ## Turn the snapshot into Hugo content and data
	$(PY) -m tools.registryctl emit-content --snapshot $(BUILD)/snapshot --site $(SITE)

site: ## Build the static site
	cd $(SITE) && $(HUGO) --gc --minify --destination public

## --- verification ---------------------------------------------------------

verify: verify-snapshot verify-site ## Both gates

verify-snapshot: ## Referential integrity, cycles, mass-change guard
	$(PY) -m tools.registryctl verify-snapshot --snapshot $(BUILD)/snapshot

verify-site: ## Offline safety, links, page budgets, accessibility
	$(PY) -m tools.registryctl verify-site --public $(PUBLIC)

check-offline: ## Fail if anything in the built site reaches outside the network
	@! grep -rlE 'src="https?://(?!gitlab\.acme\.internal)' $(PUBLIC) --include='*.html' \
		|| (echo "external asset reference found" && exit 1)
	@echo "no external assets"

test: ## Unit and pipeline tests
	$(PY) -m pytest tests/ -q

## --- fixtures and tooling -------------------------------------------------

fixtures: ## Regenerate evaluation history and blueprint pages (deterministic)
	$(PY) tools/dev/make_fixture_evals.py
	$(PY) tools/dev/make_blueprints.py

stats: ## Print the index report totals
	$(PY) -m tools.registryctl report --snapshot $(BUILD)/snapshot

## --- local development ----------------------------------------------------

dev: index emit ## Rebuild content, then serve with live reload
	cd $(SITE) && $(HUGO) server --port $(PORT) --bind 0.0.0.0

serve: ## Serve the built site without rebuilding
	cd $(PUBLIC) && $(PY) -m http.server $(PORT)

clean: ## Remove generated output (never touches examples/ or governance/)
	rm -rf $(BUILD) $(PUBLIC) $(SITE)/content/harnesses $(SITE)/data/cards \
	       $(SITE)/data/registry.json $(SITE)/data/index-report.json \
	       $(SITE)/data/facets.json $(SITE)/static/catalog.json $(SITE)/static/graph.json \
	       $(SITE)/resources .pytest_cache
