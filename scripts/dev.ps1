<#
.SYNOPSIS
    Agent Blue Accounting - Developer Task Runner (PowerShell).

.DESCRIPTION
    Provides a unified command interface for common development tasks.
    Works on Windows with PowerShell 5.1+ and PowerShell Core 7+.

.EXAMPLE
    .\scripts\dev.ps1 help
    .\scripts\dev.ps1 doctor
    .\scripts\dev.ps1 verify
    .\scripts\dev.ps1 test
#>

param(
    [Parameter(Position=0)]
    [string]$Command = "help",

    [Parameter(Position=1, ValueFromRemainingArguments)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSCommandPath)

# Ensure we run from the project root.
Set-Location $ProjectRoot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Invoke-Step {
    param([string]$Name, [scriptblock]$Block)
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

function Get-RestString {
    if ($Rest) { return ($Rest -join " ") }
    return ""
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

switch ($Command) {

    "help" {
        Write-Host "Agent Blue Accounting - Developer Commands (PowerShell)"
        Write-Host ""
        Write-Host "  .\scripts\dev.ps1 help               Show this help"
        Write-Host "  .\scripts\dev.ps1 setup              Install dev deps and hooks"
        Write-Host "  .\scripts\dev.ps1 lint               Run Ruff linter"
        Write-Host "  .\scripts\dev.ps1 lint-fix           Run Ruff safe auto-fixes"
        Write-Host "  .\scripts\dev.ps1 format             Run Ruff formatter"
        Write-Host "  .\scripts\dev.ps1 format-check       Check formatting"
        Write-Host "  .\scripts\dev.ps1 typecheck          Run Mypy"
        Write-Host "  .\scripts\dev.ps1 test               Run full test suite"
        Write-Host "  .\scripts\dev.ps1 test-unit          Run unit tests"
        Write-Host "  .\scripts\dev.ps1 test-integration   Run integration tests"
        Write-Host "  .\scripts\dev.ps1 test-fast          Run unit tests (fast)"
        Write-Host "  .\scripts\dev.ps1 verify             Run full quality gate"
        Write-Host "  .\scripts\dev.ps1 compose-check      Validate Docker Compose"
        Write-Host "  .\scripts\dev.ps1 docker-build       Build Docker services"
        Write-Host "  .\scripts\dev.ps1 docker-up          Start Docker services"
        Write-Host "  .\scripts\dev.ps1 docker-down        Stop Docker services"
        Write-Host "  .\scripts\dev.ps1 docker-restart     Restart Docker services"
        Write-Host "  .\scripts\dev.ps1 docker-ps          Show Docker status"
        Write-Host "  .\scripts\dev.ps1 docker-logs        Follow API logs"
        Write-Host "  .\scripts\dev.ps1 docker-logs-all    Follow all logs"
        Write-Host "  .\scripts\dev.ps1 health-live        Check liveness endpoint"
        Write-Host "  .\scripts\dev.ps1 health-ready       Check readiness endpoint"
        Write-Host "  .\scripts\dev.ps1 health             Check both endpoints"
        Write-Host "  .\scripts\dev.ps1 db-upgrade         Run pending migrations"
        Write-Host "  .\scripts\dev.ps1 db-downgrade       Downgrade one migration"
        Write-Host "  .\scripts\dev.ps1 db-current         Show current revision"
        Write-Host "  .\scripts\dev.ps1 db-history         Show migration history"
        Write-Host "  .\scripts\dev.ps1 db-revision MSG=x  Create migration"
        Write-Host "  .\scripts\dev.ps1 doctor             Environment diagnostic"
        Write-Host "  .\scripts\dev.ps1 status             Project status"
        Write-Host "  .\scripts\dev.ps1 clean              Remove generated artifacts"
    }

    "setup" {
        python scripts/doctor.py --check python
        Invoke-Step "Install dependencies" { pip install -e ".[dev]" }
        Write-Host "Installing pre-commit hooks..." -ForegroundColor Cyan
        & pre-commit install 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "pre-commit not available (optional)" -ForegroundColor Yellow
        }
    }

    "lint" {
        Invoke-Step "Ruff Lint" { ruff check . }
    }

    "lint-fix" {
        Invoke-Step "Ruff Fix" { ruff check --fix . }
    }

    "format" {
        Invoke-Step "Ruff Format" { ruff format . }
    }

    "format-check" {
        Invoke-Step "Ruff Format Check" { ruff format --check . }
    }

    "typecheck" {
        Invoke-Step "Mypy" { mypy app/ }
    }

    "test" {
        Invoke-Step "Pytest (full)" { pytest -vv }
    }

    "test-unit" {
        Invoke-Step "Pytest (unit)" { pytest -m unit -vv }
    }

    "test-integration" {
        Invoke-Step "Pytest (integration)" { pytest -m integration -vv }
    }

    "test-fast" {
        Invoke-Step "Pytest (fast)" { pytest -m unit -q }
    }

    "verify" {
        Invoke-Step "Ruff Lint" { ruff check . }
        Invoke-Step "Ruff Format Check" { ruff format --check . }
        Invoke-Step "Mypy" { mypy app/ }
        Invoke-Step "Pytest" { pytest -vv }
        Invoke-Step "Docker Compose Config" { docker compose config --quiet }
        Write-Host "`n=== All checks passed ===" -ForegroundColor Green
    }

    "compose-check" {
        Invoke-Step "Docker Compose Config" { docker compose config --quiet }
    }

    "docker-build" {
        Invoke-Step "Docker Build" { docker compose build }
    }

    "docker-up" {
        Invoke-Step "Docker Up" { docker compose up -d }
    }

    "docker-down" {
        Invoke-Step "Docker Down" { docker compose down }
    }

    "docker-restart" {
        Invoke-Step "Docker Restart" {
            docker compose down
            docker compose up -d
        }
    }

    "docker-ps" {
        docker compose ps
    }

    "docker-logs" {
        docker compose logs -f api
    }

    "docker-logs-all" {
        docker compose logs -f
    }

    "health-live" {
        Invoke-Step "Liveness" {
            python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health/live'); print(r.read().decode()); sys.exit(0 if r.status==200 else 1)"
        }
    }

    "health-ready" {
        Invoke-Step "Readiness" {
            python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health/ready'); print(r.read().decode()); sys.exit(0 if r.status==200 else 1)"
        }
    }

    "health" {
        Invoke-Step "Liveness" {
            python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health/live'); print(r.read().decode()); sys.exit(0 if r.status==200 else 1)"
        }
        Invoke-Step "Readiness" {
            python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health/ready'); print(r.read().decode()); sys.exit(0 if r.status==200 else 1)"
        }
    }

    "db-upgrade" {
        Invoke-Step "Alembic Upgrade" { alembic upgrade head }
    }

    "db-downgrade" {
        Write-Host "WARNING: This will downgrade the database schema." -ForegroundColor Yellow
        Invoke-Step "Alembic Downgrade" { alembic downgrade -1 }
    }

    "db-current" {
        alembic current
    }

    "db-history" {
        alembic history
    }

    "db-revision" {
        $msg = Get-RestString
        if ([string]::IsNullOrWhiteSpace($msg)) {
            Write-Host "ERROR: Migration message required." -ForegroundColor Red
            Write-Host 'Usage: .\scripts\dev.ps1 db-revision MSG="description"'
            exit 1
        }
        Invoke-Step "Alembic Revision" { alembic revision --autogenerate -m $msg }
    }

    "doctor" {
        python scripts/doctor.py
    }

    "status" {
        Write-Host "=== Git ===" -ForegroundColor Cyan
        git branch --show-current
        git status --short
        Write-Host ""
        Write-Host "=== Docker Compose ===" -ForegroundColor Cyan
        try { docker compose ps } catch { Write-Host "Docker Compose not available" }
        Write-Host ""
        Write-Host "=== Alembic ===" -ForegroundColor Cyan
        try { alembic current } catch { Write-Host "Alembic not available" }
    }

    "clean" {
        Write-Host "Removing generated artifacts..." -ForegroundColor Cyan
        $dirs = @("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")
        foreach ($dir in $dirs) {
            Get-ChildItem -Path $ProjectRoot -Filter $dir -Directory -Recurse -ErrorAction SilentlyContinue |
                Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
        Get-ChildItem -Path $ProjectRoot -Filter "*.pyc" -Recurse -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        Write-Host "Done." -ForegroundColor Green
    }

    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host "Run: .\scripts\dev.ps1 help"
        exit 1
    }
}
