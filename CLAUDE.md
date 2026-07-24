# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source of truth

`docs/ТЗ_Raffle_Platform.md` (v2.4) is the **only** requirements source. Before changing
architecture or business logic, check it. Section 21 of the ТЗ lists features that must **not** be
implemented in this version (ready VK/MAX adapters, VK Mini App, payment refunds, cancellation of
confirmed manual registrations, automatic winner selection, million-scale ticket pools) — do not add
them without explicit sign-off from the project owner. **The VK adapter is the one exception**: the
project owner signed off on it and it has since been built and is active in production alongside
Telegram (see DECISIONS.md #32/#33, ARCHITECTURE.md §7.1) — the ТЗ text itself wasn't updated to
reflect that. MAX is still just a stub (`channels/max/`, raises `NotImplementedError`) — do not
implement it without sign-off.

Decisions and defaults not explicitly covered by the ТЗ are recorded in `DECISIONS.md` — check it before
introducing a new convention, and add an entry there when you make a similar judgment call.
`ARCHITECTURE.md` documents how the ТЗ requirements map onto the actual implementation (process
topology, ticket-pool/payment algorithms, permissions, extensibility points) — read it before making
structural changes.

**No CI.** By product decision there is no GitHub Actions (or other CI) pipeline. Work happens directly
on `main` (no feature branches / PRs by default) but every push must be preceded by a fully green local
run of ruff, black --check, mypy, and pytest — treat that sequence as the CI gate.

## Commands

Backend/Python (run from repo root; requires a venv with `pip install -e ".[backend,telegram,vk,dev]"`):

```bash
# Lint / format / typecheck (all must be clean before pushing)
ruff check app backend channels tests
black --check app backend channels tests
mypy app backend channels

# Auto-fix lint + format
ruff check --fix app backend channels tests
black app backend channels tests

# Full test suite (isolated temp-file SQLite, no Docker/network)
pytest

# Single test file / test
pytest tests/unit/test_ticket_pool.py
pytest tests/unit/test_ticket_pool.py::test_concurrent_capture_on_the_tail_no_duplicates_no_overselling -v

# DB migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Run backend API locally
uvicorn backend.main:app --reload

# Run Telegram channel locally (separate process)
python -m channels.telegram.main

# Run VK channel locally (separate process)
python -m channels.vk.main
```

Note: `pyproject.toml` declares `requires-python = ">=3.11"` (and Docker images use
`python:3.11-slim`), but the code intentionally avoids 3.11-only syntax (e.g. `datetime.UTC`,
hence `ruff` ignoring `UP017`) so it also runs under 3.10 — see DECISIONS.md #15. Don't "fix" this by
introducing 3.11-only constructs.

Frontend (run from `frontend/`):

```bash
npm install
npm run dev        # Vite dev server, proxies /api -> localhost:8000
npm run build       # tsc -b && vite build
npm run lint         # eslint
npm run format       # prettier --check
npm run format:fix
```

Docker / full stack:

```bash
cp .env.example .env
docker compose up --build   # dev: self-signed TLS via docker-compose.override.yml (auto-applied)
docker compose -f docker-compose.yml up -d --build   # prod: real domain + ACME via Caddy
```

## Architecture

`ARCHITECTURE.md` is the detailed reference for process topology, the `app/` package layout, and the
mechanics behind every invariant below (ticket-pool locking, atomic finalization SQL, permissions
matrix, identity model) — read it before structural changes. The rules below are the non-negotiable
summary; don't violate them when changing code.

- **Participant identity is the phone number**, never a messenger ID — all identification logic lives
  in `app/services/participant_service.py`. See ARCHITECTURE.md §6.
- **Ticket pool reservation is atomic and all-or-nothing** (`BEGIN IMMEDIATE`, Python-shuffled
  `shuffle_order`, never `ORDER BY RANDOM()`) — see ARCHITECTURE.md §3. Concurrency tests need a
  temp-file DB, never `sqlite:///:memory:` (in-memory gives each connection its own DB under
  `SingletonThreadPool`, which hides races).
- **Idempotent state transitions** use one atomic conditional `UPDATE ... WHERE status = 'PENDING'`,
  never select-then-update or an idempotency-key table — see ARCHITECTURE.md §4. Payment finalization
  no-ops silently on repeat (`applied: bool`); manual-registration confirm/cancel instead **raises**
  (`ManualRegistrationStateError`) on repeat — that asymmetry is intentional per the ТЗ, not a bug.
- **`ignore_phone_verification`** must stay reversible: `can_access_own_account()` evaluates
  `phone_verified OR ignore_phone_verification` live at access time, never a bind-time snapshot —
  turning the flag off must immediately revoke access for bindings relying only on it. See ТЗ §7.1/§10.3.
- **Permissions are always enforced server-side** (`require_permission(...)`, matrix in
  `app/core/permissions.py`, see ARCHITECTURE.md §5) — frontend nav filtering is UX only.
- **Broadcasts go out only via Telegram**; transactional notifications go through whatever channel the
  participant used to transact.
- **Every mutating action gets an audit-log row** via `app/services/audit_service.py` — don't bypass it.
- **Exactly one payment provider is active at a time**, switchable from Super Admin with no redeploy
  (`PlatformSettings.payment_provider_override`, see ARCHITECTURE.md §2/§7) — never hardcode a provider
  outside the factory.

### Testing conventions

- All tests run against an isolated temp-file SQLite DB (`tests/conftest.py`) — never Docker/network.
  Use the `db`/`session`/`settings` fixtures instead of hand-rolling a connection.
- Concurrency tests use `threading.Barrier`/`threading.Thread` against the shared temp-file DB.
- `tests/unit/` covers models/services in isolation; `tests/integration/` drives the FastAPI app via
  `httpx`/`TestClient` (permission matrix, webhook idempotency, broadcasts + reports end-to-end).
- The mandatory ТЗ §20.1 test groups (payment idempotency, pool/reservation incl. the concurrent tail
  race, full permission matrix, manual registrations, identification/binding,
  `ignore_phone_verification`, multi-channel behavior, notifications/broadcasts) must stay covered —
  check which existing test(s) exercise an invariant before assuming it's untested.
