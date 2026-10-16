.PHONY: install test lint ingest policies transform quality report export all

install:
	pip install -e ".[dev,analysis]"

test:
	pytest -q

lint:
	ruff check pipeline tests

ingest:
	python -m pipeline.cli ingest

policies:
	python -m pipeline.cli download nfip_policies

transform:
	python -m pipeline.cli transform

quality:
	python -m pipeline.cli quality

report:
	python -m pipeline.cli report

export:
	python -m pipeline.cli export

all: ingest transform quality report export
