.PHONY: test test-v1 test-v2 cov

test:
	.venv/bin/pytest tests/ -v

test-v1:
	.venv/bin/pytest tests/test_backtest.py tests/test_metrics.py tests/test_batch.py -v

test-v2:
	.venv/bin/pytest tests/engine_v2/ -v --cov=src/engine_v2 --cov-fail-under=85 --cov-report=term-missing

cov:
	.venv/bin/pytest tests/engine_v2/ --cov=src/engine_v2 --cov-fail-under=85 --cov-report=term-missing
