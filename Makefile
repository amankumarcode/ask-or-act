.PHONY: help install setup test test-all lint fmt demo eval trace clean

RUN := uv run askoract

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

install:  ## Create the virtualenv and install dependencies
	uv sync

setup:  ## Download Northwind, inject traps, build the test databases
	$(RUN) setup

test:  ## Run the offline test suite (no network, no API keys)
	uv run pytest -m "not network and not llm"

test-all:  ## Run every test, including network and live-LLM tests
	uv run pytest

lint:  ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## Auto-fix lint issues and format
	uv run ruff check --fix .
	uv run ruff format .

demo:  ## Run the showcase questions (baseline vs full agent)
	$(RUN) demo

eval:  ## Run the full test set for every profile and write the results table
	$(RUN) eval --profile baseline --profile full

trace:  ## Show the trace of one case: make trace RUN_ID=latest CASE=A-001
	$(RUN) trace $(or $(RUN_ID),latest) $(CASE)

clean:  ## Remove built databases, runs, and caches (raw downloads are kept)
	rm -rf data/built runs .pytest_cache .ruff_cache
