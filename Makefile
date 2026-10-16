.PHONY: install test lint ingest transform quality run

install:
	pip install -e ".[dev,analysis]"

test:
	pytest -q

lint:
	ruff check pipeline tests

ingest:
	python -m pipeline.cli ingest

transform:
	python -m pipeline.cli transform

quality:
	python -m pipeline.cli quality

run:
	python -m pipeline.cli run
