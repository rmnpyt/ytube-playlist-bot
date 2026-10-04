# Developer and operator entry points.
#
# Everything here is a thin wrapper around a command you could run by hand; the
# Makefile exists so the same command works in CI, in a container and on the VPS.
#
# On Windows, point PYTHON at the virtual environment:
#   make PYTHON=.venv/Scripts/python.exe check

PYTHON ?= python3
VENV := .venv
COMPOSE := docker compose
IMAGE := ytube-playlist-bot:local

.DEFAULT_GOAL := help
.PHONY: help install format format-check lint typecheck test coverage check \
        pre-commit build up down restart logs ps shell psql clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtual environment and install everything
	python3 -m venv $(VENV)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

format: ## Rewrite files to the canonical style
	$(PYTHON) -m ruff format bot tests
	$(PYTHON) -m ruff check --fix bot tests

format-check: ## Fail if files are not canonically formatted
	$(PYTHON) -m ruff format --check bot tests

lint: ## Run the linter
	$(PYTHON) -m ruff check bot tests

typecheck: ## Run the type checker
	$(PYTHON) -m mypy

test: ## Run the test suite
	$(PYTHON) -m pytest tests

coverage: ## Run the suite and write an HTML coverage report
	$(PYTHON) -m pytest tests --cov-report=html
	@echo "Open htmlcov/index.html"

check: format-check lint typecheck test ## Everything CI checks, in order

pre-commit: ## Run the pre-commit hooks over the whole tree
	$(PYTHON) -m pre_commit run --all-files

build: ## Build the bot image
	docker build -f docker/Dockerfile -t $(IMAGE) .

up: ## Start the stack
	$(COMPOSE) up -d --build

down: ## Stop the stack, keeping data
	$(COMPOSE) down

restart: ## Restart the bot
	$(COMPOSE) restart bot

logs: ## Follow the bot logs
	$(COMPOSE) logs -f bot

ps: ## Show what is running
	$(COMPOSE) ps

shell: ## Open a shell inside the running bot container
	$(COMPOSE) exec bot /bin/bash

clean: ## Remove build, test and coverage artefacts
	rm -rf .mypy_cache .pytest_cache .ruff_cache build dist htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
