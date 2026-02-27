.PHONY: test lint lint-fix format format-check audit clean install dev demo-prepare demo-screens

# Development
install:
	cd services/alarm_broker && pip install -e ".[dev]"

dev: install
	cd services/alarm_broker && python -m pytest tests/ -v

# Testing
test:
	cd services/alarm_broker && python -m pytest -q

test-verbose:
	cd services/alarm_broker && python -m pytest -v

# Linting & Formatting
lint:
	cd services/alarm_broker && ruff format --check services/alarm_broker
	cd services/alarm_broker && ruff check services/alarm_broker

lint-fix:
	cd services/alarm_broker && ruff format services/alarm_broker
	cd services/alarm_broker && ruff check --fix services/alarm_broker

format:
	cd services/alarm_broker && ruff format services/alarm_broker

format-check:
	cd services/alarm_broker && ruff format --check services/alarm_broker

# Security & Dependency Audit
audit:
	cd services/alarm_broker && ruff check services/alarm_broker
	cd services/alarm_broker && bandit -q -r services/alarm_broker/alarm_broker
	cd services/alarm_broker && pip-audit services/alarm_broker

# Pre-commit hooks
pre-commit:
	pre-commit install

# Cleanup
clean:
	rm -rf .pytest_cache .ruff_cache services/alarm_broker/.pytest_cache services/alarm_broker/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

# Docker
docker-build:
	docker compose -f deploy/docker-compose.yml build

docker-up:
	docker compose -f deploy/docker-compose.yml up -d

docker-down:
	docker compose -f deploy/docker-compose.yml down

docker-logs:
	docker compose -f deploy/docker-compose.yml logs -f

# Local demo workflow
demo-prepare:
	python scripts/demo_prepare.py

demo-screens:
	python scripts/demo_capture.py
