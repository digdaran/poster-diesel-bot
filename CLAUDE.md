# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Source of truth

`docs/ТЗ_Raffle_Platform.md` (v2.4) is the **only** requirements source. Before changing
architecture or business logic, check it. Section 21 of the ТЗ lists features that must **not** be
implemented in this version (ready VK/MAX adapters, VK Mini App, payment refunds, cancellation of
confirmed manual registrations, automatic winner selection, million-scale ticket pools) — do not add
them without explicit sign-off from the project owner.

Decisions and defaults not explicitly covered by the ТЗ are recorded in `DECISIONS.md` — check it before
introducing a new convention, and add an entry there when you make a similar judgment call.
`ARCHITECTURE.md` documents how the ТЗ requirements map onto the actual implementation (process
topology, ticket-pool/payment algorithms, permissions, extensibility points) — read it before making
structural changes.

**No CI.** By product decision there is no GitHub Actions (or other CI) pipeline. Work happens directly
on `main` (no feature branches / PRs by default) but every push must be preceded by a fully green local
run of ruff, black --check, mypy, and pytest — treat that sequence as the CI gate.

## Commands

Backend/Python (run from repo root; requires a venv with `pip install -e ".[backend,telegram,dev]"`):

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
pytest tests/unit/test_ticket_pool.py::test_concurrent_reservation_tail_race -v

# DB migrations
alembic upgrade head
alembic revision --autogenerate -m "description"

# Run backend API locally
uvicorn backend.main:app --reload

# Run Telegram channel locally (separate process)
python -m channels.telegram.main
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

### Process topology (all share the `app/` package as the single source of business logic)

| Process | Entry point | Role |
|---|---|---|
| `backend` | `backend/main.py` (FastAPI/Uvicorn) | Panel REST API, bank webhook routers (`backend/webhooks/`), background jobs (payment status polling, expired-reservation release), `/metrics`. |
| `channel-telegram` | `channels/telegram/main.py` (aiogram 3, long polling) | The only messenger channel active in production. |
| `frontend` | `frontend/` (React+TS SPA) | Static build served behind Caddy; not part of `app/`. |
| `reverse-proxy` | official `caddy` image | HTTPS (ACME prod / self-signed dev), IP-whitelists the panel, exposes only `/webhooks/*` and the panel externally; `/metrics` is never proxied externally. |

### `app/` package layout

- `app/core/` — config (pydantic-settings), `permissions.py` (named `Permission` enum + `PanelRole` ->
  permission-set matrix, mirrors ТЗ §11.3 exactly), `security.py` (JWT + argon2), `db.py` (SQLAlchemy
  engine/session wrapper, WAL + busy_timeout, the `BEGIN IMMEDIATE` mechanism — see below), `phone.py`
  (normalization), `backup.py` (VACUUM INTO + gzip + retention).
- `app/models/` — SQLAlchemy 2.0 declarative models for all entities in ТЗ §6.2 (`Participant`,
  `ChannelBinding`, `Giveaway`, `TicketPool`, `Ticket`, `Payment`, `ManualRegistration`, `PanelUser`,
  `AuditLog`, `Broadcast`, `PlatformSettings`) plus `enums.py`.
- `app/repositories/` — the only place doing raw atomic DB operations (ticket pool reservation, etc.).
- `app/services/` — business scenarios: `ticket_pool_service`, `payment_service`,
  `participant_service`, `manual_registration_service`, `broadcast_service`, `report_service`,
  `audit_service`, `panel_user_service`, `settings_service`.
- `app/payments/` — `BasePaymentProvider` ABC + `MockProvider` / `TBankProvider` / `VTBProvider`
  (stub) + `factory.py` (resolves the active provider: `PlatformSettings.payment_provider_override` in
  DB takes priority over the `.env` default — this is how Super Admin switches banks without a
  redeploy).
- `app/channels/` — `BaseMessengerChannel` ABC + `ChannelCapabilities` + `factory.py`
  (`ACTIVE_CHANNELS = frozenset({ChannelType.TELEGRAM})` — VK/MAX exist only as
  `channels/vk/`, `channels/max/` stub classes that raise `NotImplementedError`; do not add real
  logic there, see ТЗ §21).

### Key invariants — do not violate these when changing code

- **Participant identity is the normalized phone number**, not any messenger ID. `Participant` has no
  messenger-specific fields. Every messenger link lives in `ChannelBinding` (participant can have
  multiple bindings across channels). All identification logic is centralized in
  `app/services/participant_service.py` (find-or-create by phone, verified-vs-unverified binding
  paths, gift-purchase-then-owner-confirms merge behavior).
- **Ticket pool concurrency**: `TicketPool` rows for a giveaway are materialized up front with a
  Python-`random.shuffle`d `shuffle_order` (not `ORDER BY RANDOM()`). Reservation
  (`app/repositories/ticket_pool_repo.py::reserve_tickets`) opens a `BEGIN IMMEDIATE` transaction
  (see `Database.immediate_session()` in `app/core/db.py`), selects free rows ordered by
  `shuffle_order`, and is strictly all-or-nothing: if fewer than `quantity` free rows exist, the whole
  reservation rolls back rather than partially reserving. Concurrent writers across processes serialize
  via SQLite's writer lock + `busy_timeout` (not via any application-level locking) — this is why
  concurrency tests use a temp-file DB, never `sqlite:///:memory:` (in-memory DBs get a separate DB per
  connection under `SingletonThreadPool`, which would hide races).
- **Idempotent state transitions** (payment finalization in `payment_service.finalize_payment`, and
  manual-registration confirm/cancel in `manual_registration_service`) use one atomic conditional
  `UPDATE ... WHERE status = 'PENDING'` inside a `BEGIN IMMEDIATE` transaction — never a
  select-then-update pattern, and never a separate idempotency-key table. Payment finalization is a
  silent no-op on a second call (returns an `applied: bool` flag); manual-registration
  confirm/cancel instead **raises** on a repeat call (`ManualRegistrationStateError`) — this
  asymmetry is intentional per the ТЗ wording, not an inconsistency to "fix".
- **`ignore_phone_verification`** (Super Admin toggle in `PlatformSettings`) must stay reversible:
  `participant_service.can_access_own_account()` evaluates `binding.phone_verified OR
  ignore_phone_verification` live at access time, not a snapshot taken at bind time. Turning the flag
  off must immediately revoke access for bindings that only had access via the flag.
  See ТЗ §7.1/§10.3 before touching this.
- **Permissions are always enforced server-side** via `require_permission(...)` FastAPI dependencies
  reading `app/core/permissions.py`'s matrix — frontend nav filtering is UX only, not a security
  boundary. Unauthorized endpoints return 403, not 404 or a silent empty result.
- **Broadcasts go out only via Telegram** (product decision, not a technical limitation); transactional
  notifications go through whatever channel the participant used to transact.
- Every significant action gets an append-only `AuditLog` row via `app/services/audit_service.py` —
  don't bypass it when adding new mutating endpoints/handlers.
- Exactly one payment provider is active at a time, switchable from the Super Admin panel with no
  redeploy (`app/payments/factory.py` + `PlatformSettings.payment_provider_override`) — don't hardcode
  a provider anywhere outside the factory.

### Testing conventions

- All tests run against an isolated temp-file SQLite DB (see `tests/conftest.py`) — never against
  Docker or the network. Use the `db`/`session`/`settings` fixtures rather than hand-rolling a
  connection.
- Concurrency tests (ticket pool tail-capture race, etc.) use `threading.Barrier`/`threading.Thread`
  against the shared temp-file DB fixture to simulate multiple processes racing on the same reservation.
- `tests/unit/` covers models/services in isolation; `tests/integration/` drives the FastAPI app
  through `httpx`/`TestClient`-style flows (full permission matrix, webhook idempotency, broadcasts +
  reports end-to-end).
- The mandatory test groups from ТЗ §20.1 (payment idempotency, pool/reservation incl. the concurrent
  tail race, full permission matrix, manual registrations, identification/binding,
  `ignore_phone_verification`, multi-channel behavior, notifications/broadcasts) must stay covered —
  when refactoring, check which existing test(s) exercise the invariant you're touching before assuming
  it's untested.
