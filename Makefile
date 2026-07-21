.PHONY: install up down logs test test-unit test-integration lint format typecheck check migrate migration

install:
	pip install -e ".[dev]"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api

test:
	pytest

test-unit:
	pytest -m unit

test-integration:
	pytest -m integration

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app/

check: lint typecheck test-unit

migrate:
	alembic upgrade head

migration:
	alembic revision --autogenerate -m "$(MSG)"
