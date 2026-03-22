.PHONY: test lint format run clean

test:
	python -m pytest tests/ -v

lint:
	python -m ruff check src/ tests/

format:
	python -m ruff format src/ tests/

run:
	python -m src.cli.main $(ARGS)

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
