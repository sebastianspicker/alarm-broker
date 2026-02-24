.PHONY: test lint audit clean

test:
	./.venv/bin/pytest -q

lint:
	./.venv/bin/ruff format --check services/alarm_broker
	./.venv/bin/ruff check services/alarm_broker

audit:
	./.venv/bin/bandit -q -r services/alarm_broker/alarm_broker
	./.venv/bin/pip-audit services/alarm_broker

clean:
	rm -rf .pytest_cache .ruff_cache services/alarm_broker/.pytest_cache services/alarm_broker/.ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
