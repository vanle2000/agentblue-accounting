# Agent Blue Accounting

Modular monolith for property management accounting, built with FastAPI, SQLAlchemy 2.x async, and PostgreSQL.

## Prerequisites

- **Python 3.12.10** (the project-local runtime)
- **Docker Desktop** with Docker Compose
- **Git** 2.54+
- **VS Code** (recommended editor)

### Python version isolation

| Environment         | Python version | Purpose                        |
|---------------------|----------------|--------------------------------|
| Hermes              | 3.11.15        | Hermes Agent runtime (isolated)|
| Agent Blue (local)  | 3.12.10        | VS Code, linting, tests        |
| Agent Blue (Docker) | 3.12.x         | Authoritative application runtime |

Hermes and Agent Blue must remain isolated. Hermes can edit files and run
commands but must not install Agent Blue dependencies into the Hermes
environment. Docker is the authoritative runtime because it provides a
consistent Linux environment across development and future deployment.

## Local Python setup

### 1. Verify Python 3.12.10 is installed

```powershell
py -3.12 --version
```

Expected output: `Python 3.12.10`

### 2. Create the project virtual environment

```powershell
py -3.12 -m venv .venv
```

### 3. Activate the virtual environment

**PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
```

**Git Bash:**

```bash
source .venv/Scripts/activate
```

### 4. Select the VS Code interpreter

1. Press `Ctrl+Shift+P`
2. Type `Python: Select Interpreter`
3. Choose `.\.venv\Scripts\python.exe`

### 5. Install dependencies

**PowerShell:**

```powershell
.\.venv\Scripts\pip.exe install -e ".[dev]"
```

**Git Bash:**

```bash
pip install -e ".[dev]"
```

## Environment file setup

```powershell
Copy-Item .env.example .env
```

**Git Bash:**

```bash
cp .env.example .env
```

The `.env` file is git-ignored and contains development-only credentials.

## Docker

### Build and start

**PowerShell:**

```powershell
docker compose up -d --build
```

**Git Bash:**

```bash
docker compose up -d --build
```

### Verify the API

**Liveness:**

```powershell
curl http://localhost:8000/api/v1/health/live
```

Expected:

```json
{"status": "ok", "service": "agentblue-accounting"}
```

**Readiness:**

```powershell
curl http://localhost:8000/api/v1/health/ready
```

Expected:

```json
{"status": "ok", "database": "connected"}
```

### View logs

```powershell
docker compose logs -f api
```

### Stop Docker

```powershell
docker compose down
```

### Stop Docker and remove the database volume

```powershell
docker compose down -v
```

## Alembic migrations

### Create a migration

**PowerShell (inside activated .venv):**

```powershell
alembic revision --autogenerate -m "description of changes"
```

**Git Bash (inside activated .venv):**

```bash
alembic revision --autogenerate -m "description of changes"
```

### Run migrations

```powershell
alembic upgrade head
```

## Testing

### Unit tests

**PowerShell:**

```powershell
.\.venv\Scripts\pytest.exe -m unit
```

**Git Bash:**

```bash
pytest -m unit
```

### Integration tests

Integration tests require PostgreSQL via Docker Compose to be running.

```powershell
.\.venv\Scripts\pytest.exe -m integration
```

### Full test suite

```powershell
.\.venv\Scripts\pytest.exe
```

### Coverage report

```powershell
.\.venv\Scripts\pytest.exe --cov=agentblue --cov-report=term-missing
```

## Code quality

### Ruff linting

```powershell
.\.venv\Scripts\ruff.exe check .
```

### Ruff formatting

```powershell
.\.venv\Scripts\ruff.exe format .
```

### mypy

```powershell
.\.venv\Scripts\mypy.exe app/
```

### pre-commit

**Install:**

```powershell
.\.venv\Scripts\pre-commit.exe install
```

**Run manually:**

```powershell
.\.venv\Scripts\pre-commit.exe run --all-files
```

## Makefile (Git Bash / GNU Make)

The `Makefile` provides convenient shortcuts for Git Bash or any
environment where GNU Make is installed. PowerShell users should use the
direct commands documented above.

```bash
make install          # pip install -e ".[dev]"
make up               # docker compose up -d --build
make down             # docker compose down
make logs             # docker compose logs -f api
make test             # pytest
make test-unit        # pytest -m unit
make test-integration # pytest -m integration
make lint             # ruff check .
make format           # ruff format .
make typecheck        # mypy app/
make check            # lint + typecheck + test-unit
make migrate          # alembic upgrade head
make migration MSG="add users table"  # alembic revision --autogenerate
```

## Common troubleshooting

### Port 5432 already in use

Another PostgreSQL instance may be running. Stop it or change `DB_PORT`
in `.env` and `docker-compose.yml`.

### Port 8000 already in use

Another service is using port 8000. Change the host port mapping in
`docker-compose.yml`:

```yaml
ports:
  - "8001:8000"
```

### Python 3.14 used by accident

The system `python3` command may resolve to Python 3.14 (Hermes). Always
activate the `.venv` or use `.\.venv\Scripts\python.exe` explicitly.

### Packages installed into wrong environment

If you ran `pip install` without activating the `.venv`, packages were
installed into the system Python. Activate the venv first, then reinstall.

### Docker build fails on pip install

Ensure `pyproject.toml` is valid and does not reference packages that
require system-level build tools not present in `python:3.12-slim`.

### mypy reports missing stubs

Run `mypy` only against the `app/` directory. The `alembic.*` module has
`ignore_missing_imports` configured in `pyproject.toml`.

## Project structure

```
agentblue-accounting/
├── app/
│   └── agentblue/
│       ├── __init__.py          # Package version
│       ├── main.py              # FastAPI application factory
│       ├── config.py            # pydantic-settings configuration
│       ├── api/
│       │   ├── __init__.py
│       │   └── health.py        # Liveness and readiness endpoints
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py          # SQLAlchemy declarative base
│       │   ├── session.py       # Async engine and session factory
│       │   └── models/
│       │       └── __init__.py  # Future models package
│       └── logging.py           # structlog configuration
├── migrations/
│   ├── env.py                   # Alembic async environment
│   ├── script.py.mako           # Migration template
│   └── versions/                # Migration scripts
├── tests/
│   ├── conftest.py              # Shared fixtures
│   ├── unit/
│   │   └── test_health_live.py
│   └── integration/
│       ├── test_health_ready.py
│       └── test_health_ready_failure.py
├── docs/
│   └── adr/
│       └── 0001-initial-architecture.md
├── scripts/
├── .env.example
├── .gitignore
├── .gitattributes
├── .pre-commit-config.yaml
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── pyproject.toml
└── README.md
```
