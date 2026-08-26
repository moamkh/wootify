# Wootify Codebase Review — Industry Standards Comparison

**Date:** 2026-05-26  
**Scope:** Full backend (`app/`) — services, connectors, clients, models, controllers  
**Method:** Static analysis + architectural pattern review + async/concurrency audit  

---

## 1. Executive Summary

| Severity | Count | Categories |
|----------|-------|------------|
| 🔴 Critical | 6 | Event-loop blocking, unbounded memory leaks, message loss, dead code |
| 🟠 High | 9 | God classes, massive duplication, silent failures, missing retries, N+1 queries |
| 🟡 Medium | 12 | Caching gaps, schema drift, hardcoded values, fragile state detection |
| 🟢 Low | 8 | Print statements, minor anti-patterns, missing proxies, naming |

**Top 3 risks:**
1. **`requests.post()` in `enterprise_gre_service.py` blocks the asyncio event loop** — every phone validation freezes the entire process.
2. **Unbounded `ChatwootClient` caches** in Bridge + Enterprise services leak memory and file descriptors.
3. **Enterprise vs Bridge opposite failure semantics** — enterprise update failures cause message loss; bridge failures cause infinite replay.

---

## 2. Architecture & Cross-Cutting Issues

### 2.1 Dependency Injection — Manual Singletons (Acceptable but Brittle)
- **No formal DI framework** (no `dependency-injector`, no FastAPI `Depends` for services).
- Services instantiate each other in `__init__` or at module level:
  ```python
  # app/controllers/api_v1_controller.py
  platform_registry = PlatformRegistryService()
  instances = InstanceService()
  bridge = BridgeService()
  ```
- **Risk:** Circular imports and tight coupling. A shared base class or factory would reduce coupling.

### 2.2 No Authentication / Authorization Layer
- API controllers expose instance management with **no auth middleware**.
- Chatwoot webhooks have **no signature validation** (`X-Chatwoot-Signature` header is ignored).
- CORS is `allow_origins=['*']`.

### 2.3 Encryption at Rest is Good, But Key Management is Weak
- `DATA_ENCRYPTION_KEY` falls back to a **deterministic development key** when empty:
  ```
  DATA_ENCRYPTION_KEY is empty; using deterministic development fallback key
  ```
- Production deployments may unknowingly run with the fallback key, making encryption worthless.

### 2.4 No Structured Health Checks / Observability
- No `/health` or `/ready` endpoints.
- No metrics (Prometheus, StatsD) — only structured logs.
- No distributed tracing.

---

## 3. Service-by-Service Analysis

### 3.1 `bale_polling_service.py` — The Event Loop Heart

**Good:**
- Per-instance `asyncio.Task` isolation prevents one instance from crashing others.
- `CancelledError` is correctly re-raised to allow graceful shutdown.
- SQLite lock retry logic (5 attempts) is pragmatic.

**Bad:**

| Issue | Severity | Details |
|-------|----------|---------|
| **Sync DB inside async loop** | 🔴 Critical | `with SessionLocal() as db:` runs synchronous SQLAlchemy inside async tasks. Under load, SQLite disk I/O blocks the event loop for all instances. |
| **SMS sync blocks polling** | 🟠 High | `_maybe_run_enterprise_sms_sync` is `await`-ed **before** `connector.get_updates()`. Slow SMS sync delays real-time message polling. |
| **Opposite failure semantics** | 🔴 Critical | Enterprise update errors are caught, logged, and the update ID is **persisted** → message loss. Bridge errors bubble up, update ID is **not persisted** → infinite replay. |
| **No task limit** | 🟡 Medium | One task per enabled instance with no upper bound. Could exhaust memory with many instances. |
| **In-memory state lost on restart** | 🟡 Medium | `_last_update_ids`, `_share_phone_prompted`, `_enterprise_sms_last_run` are pure RAM. Restarts cause replay or duplicate prompts. |
| **Broad exception swallowing** | 🟡 Medium | Outer `except Exception` in `_run_instance` masks persistent config errors (bad tokens) in an infinite 5s retry loop. |
| **Blocking file I/O** | 🟡 Medium | `_write_temp_sms_result_dump` opens/appends to a JSONL file synchronously inside the async loop. |
| ** connector.connect() every iteration** | 🟡 Medium | Called before every `get_updates` even when already connected. Unnecessary overhead. |

**Event loop under hard load:**
- Each instance task runs a `while` loop that does: DB query → SMS sync (maybe) → `connect()` → `get_updates()` → process updates (DB + HTTP calls) → sleep.
- **All DB operations are sync** → SQLite WAL mode helps but still blocks the event loop thread.
- With 10+ instances, the cumulative blocking time means updates for some instances wait while others do DB I/O.
- **No backpressure:** If `get_updates` returns 100 updates, they are processed sequentially in a `for` loop. A high-volume instance can starve others.

---

### 3.2 `enterprise_bale_service.py` + `enterprise_telegram_service.py` — God Classes

**Good:**
- Comprehensive state machine coverage.
- Configurable message templates via `platform_metadata`.

**Bad:**

| Issue | Severity | Details |
|-------|----------|---------|
| **God Class** | 🟠 High | `EnterpriseBaleService` is 3,224 lines with ~80 methods. It handles SMS, GRE, Chatwoot routing, state machines, keyboard UI, phone normalization, contact mgmt, webhook parsing, and file attachments. Violates SRP severely. |
| **~70% duplication between Bale & Telegram** | 🟠 High | Contact creation, session lifecycle, Chatwoot payload parsing, attachment extraction, keyboard builders, status normalization are nearly identical. A shared base or composition layer would eliminate ~5,000 lines. |
| **Sync DB in async methods** | 🔴 Critical | Every `handle_platform_update`, `receive_chatwoot_webhook`, `_handle_phone_submission` does sync SQLAlchemy ORM queries. Blocks the event loop for every message. |
| **Silent send failures** | 🟠 High | `_send_text` and `_send_media` catch `Exception`, log a warning, and return `{"id": None, "raw": None}`. Callers never know the message failed. |
| **Scattered commit boundaries** | 🟠 High | Some helpers commit internally; others rely on the caller. Mixing patterns makes rollback behavior unpredictable. |
| **Premature SMS last_id commit** | 🔴 Critical | `sync_external_sms_messages` commits the `last_id` **before** delivering messages. If a later `send_text` fails and the process restarts, those SMS records are lost forever. |
| **No row-level locking** | 🟠 High | Concurrent updates for the same user (message + webhook) race on `user.current_state`. No `with_for_update()` or optimistic locking. |
| **Getter with side effects** | 🟡 Medium | `_active_live_session_for_state` sets `session.user_present = True` and calls `save()` + `flush`. A getter should not mutate. |
| **Missing `active_only=True` in `_manual_menu_markup`** | 🟡 Medium | Keyboard shows inactive manuals. |
| **Hardcoded route config** | 🟡 Medium | `ROUTE_CONFIG` dict hardcodes `customer_service` and `sales`. Extending requires code changes. |
| **`_known_menu_button_labels` rebuilds on every live chat msg** | 🟡 Medium | Queries DB and builds markups for every message during live sessions. |
| **Heavy Chatwoot API calls repeated** | 🟡 Medium | `_get_remote_route_session_status` lists **all** contact conversations on every check. |

---

### 3.3 `enterprise_gre_service.py` — Critical Event Loop Blocker

| Issue | Severity | Details |
|-------|----------|---------|
| **`requests.post()` blocks event loop** | 🔴 Critical | `requests` is synchronous. Called from async methods. Freezes the entire process for the duration of the GRE API call. |
| **No timeout** | 🔴 Critical | `requests.post(...)` with no `timeout` can hang **indefinitely**. |
| **Hardcoded internal IP** | 🟠 High | `url="http://172.21.1.59:11211"` is hardcoded. Production URL is commented out. |
| **Hardcoded backdoor phones** | 🟠 High | `09136421196` and `09137307820` always return `eligible`. Test code in production. |
| **`print()` instead of logging** | 🟡 Medium | Lines 51-52 use `print()`. Pollutes stdout and bypasses structured logs. |
| **FastAPI exception in service layer** | 🟡 Medium | Raises `HTTPException(status_code=500)` from a domain service. Layering violation. |
| **Swallows all errors as ineligible** | 🟡 Medium | Network outage silently marks legitimate users as ineligible with no alerting. |

---

### 3.4 `bridge_service.py` — Message Router

| Issue | Severity | Details |
|-------|----------|---------|
| **Unbounded `_clients` dict** | 🔴 Critical | `ChatwootClient` instances are cached by `(base_url, token)` but **never evicted or closed**. Each holds an `httpx.AsyncClient` with its own connection pool. Memory and fd leak. |
| **Unbounded `_status_notify_recent` dict** | 🔴 Critical | Deduplication cache only prunes on access. Unique tuples grow forever. |
| **N+1 queries** | 🟠 High | `ingest_platform_event` may call `_list_contact_conversations`, `_find_existing_contact_conversation`, `_maybe_reopen_contact_conversation`, `_sync_contact_phone_if_needed` sequentially for every message. |
| **`_infer_destination_from_contact_history` loads all conversations** | 🟠 High | `list_for_instance(db, instance_id)` fetches every conversation row, then filters in Python. |
| **Bare `except Exception: pass`** | 🟠 High | `_get_or_create_contact` silently swallows contact search failures. |
| **Sync DB in async** | 🔴 Critical | Same as all other services. `db.commit()` blocks the event loop. |
| **No transaction context managers** | 🟡 Medium | Manual `db.commit()` / `db.rollback()` scattered everywhere. Should use `with db.begin():`. |
| **`_is_probably_platform_message_id` queries DB per candidate** | 🟡 Medium | Called multiple times per webhook. |

---

### 3.5 `instance_service.py` — Runtime Resolver

| Issue | Severity | Details |
|-------|----------|---------|
| **`list_runtime_enabled_instances` decrypts everything every 10s** | 🟠 High | Called by polling manager every 10s. Decrypts `platform_metadata`, `chatwoot_config`, `proxy_config` for every row, then re-computes feature overrides. |
| **`_upsert_feature_overrides` queries `list_all()` every call** | 🟡 Medium | Feature definitions are static seed data. Re-querying them for every instance resolution is wasteful. |
| **`_to_runtime` mutates DB as a side effect of a read** | 🟡 Medium | Calling `get_runtime_instance` can insert new `InstanceFeatureOverride` rows and call `db.flush()`. Surprising for a getter. |
| **Platform normalization is a 450-line if/elif chain** | 🟡 Medium | Should use Pydantic models or schema validators. |
| **CPU-bound crypto in async path** | 🟡 Medium | `encryptor.decrypt_json()` in `list_runtime_enabled_instances` can block the event loop. |

---

### 3.6 `enterprise_document_service.py` — Document Assets

| Issue | Severity | Details |
|-------|----------|---------|
| **No file size limit** | 🟠 High | `upload.read()` loads entire PDF into memory. Malicious upload can OOM the process. |
| **Orphan files on deletion** | 🟡 Medium | `delete_asset` commits DB before `_delete_file_quietly`. If file deletion fails, DB row is gone but file remains. |
| **Blocking file I/O in async** | 🟡 Medium | `target_path.write_bytes(content)`, `file_path.read_bytes()`, `shutil.rmtree` are sync. |
| **Dead code in `replace_catalog`** | 🟢 Low | Unreachable `if not changed: raise ValueError(...)` branch. |

---

### 3.7 `connectors/` — HTTP Clients

| File | Issue | Severity |
|------|-------|----------|
| `bale_connector.py` | Flat `timeout=30` (no granularity); no retries | 🟡 Medium |
| `bale_connector.py` | `download_file_by_id` swallows all exceptions silently | 🟡 Medium |
| `telegram_connector.py` | `HTTPXRequest` default read timeout (5s) < long-poll timeout (30s) → premature `ReadTimeout` | 🔴 Critical |
| `telegram_connector.py` | `Bot` leaks if `get_me()` fails after `initialize()` | 🟠 High |
| `chatwoot_client.py` | Unbounded client cache, no proxy support, no close lifecycle | 🔴 Critical |
| `novin_sms_client.py` | Creates new `AsyncClient` per call (no connection reuse) | 🟠 High |
| `bale_client.py` | **Dead code** — zero imports, duplicates connector with worse practices | 🟡 Medium |

---

## 4. Event Loop Under Hard Load — Deep Analysis

### 4.1 Current Bottlenecks

```
[Instance A Task] ----DB(block)----> SMS(block?) --> connect() --> getUpdates(block 30s)
[Instance B Task] ----DB(block)----> SMS(block?) --> connect() --> getUpdates(block 30s)
[Instance C Task] ----DB(block)----> SMS(block?) --> connect() --> getUpdates(block 30s)
```

1. **Synchronous SQLAlchemy in async tasks**
   - Every `SessionLocal()` creation, query, `flush()`, `commit()` blocks the event loop thread.
   - SQLite WAL mode allows concurrent reads but writes still serialize.
   - PostgreSQL would help but the code still uses sync `psycopg2` → still blocks.

2. **Sequential update processing**
   - `for update in updates:` processes one at a time. No `asyncio.gather` or queue.
   - A burst of 100 messages for one instance blocks that instance's task for the duration.

3. **Synchronous file I/O**
   - `_write_temp_sms_result_dump` does blocking disk writes.
   - `enterprise_document_service.py` writes PDFs synchronously.

4. **Synchronous `requests.post()`**
   - `enterprise_gre_service.py` freezes the entire process (all instances) during phone validation.

5. **No backpressure / rate limiting**
   - If Chatwoot API is slow, `BridgeService.ingest_platform_event` hangs, backing up the instance's task.
   - No circuit breaker — during an outage, every message attempts a doomed API call.

### 4.2 What Happens Under Load?

| Scenario | Current Behavior | Ideal Behavior |
|----------|-----------------|----------------|
| 10 instances, 10 msg/s each | Event loop blocked by sync DB commits; instances starve each other | Async DB + per-instance queues + batching |
| GRE API slow (5s) | Entire process frozen for 5s | Isolated non-blocking HTTP call |
| Chatwoot API down | Every message retries immediately; infinite errors | Circuit breaker opens; queues messages |
| 100 updates in one poll | Processed sequentially; 100× (DB + HTTP) latency | Batched into a queue worker pool |
| Instance token invalid | Infinite 5s retry loop with errors | Exponential backoff + alert + disable instance |

---

## 5. Caching Strategy & Recommendations

### 5.1 Where Caching Helps Most

| Target | Current Cost | Cache Strategy | TTL | Impact |
|--------|-------------|----------------|-----|--------|
| `RuntimeInstance` resolution | Decrypt 3 JSON blobs + query features + build flags per call | In-memory LRU per `instance_key` | 30–60s | Eliminates ~90% of instance service DB queries and crypto ops |
| `FeatureRepository.list_all()` | Queries DB every time `_to_runtime` is called | Permanent cache with write invalidation | ∞ (seed data) | Eliminates ~2 queries per instance resolution |
| Chatwoot contact lookups | `search_contacts` / `get_contact` API calls on every message | In-memory LRU per `(account_id, identifier)` | 5 min | Dramatically reduces Chatwoot API load |
| Chatwoot conversation status | `list_contact_conversations` on every session check | In-memory LRU per `session_id` | 15–30s | Cuts heavy API calls during live chat |
| `_known_menu_button_labels` | Queries manuals + groups from DB on every live-chat msg | In-memory LRU per `instance_key` | 30s | Eliminates repeated DB queries for static structures |
| `_get_chatwoot_client` | Creates new `httpx.AsyncClient` per unique config | Bounded LRU with TTL | 1h + max 50 entries | Prevents connection pool leaks |
| `PlatformConnector.connect()` | Re-validates token every poll iteration | Skip if runtime exists and config hash unchanged | 5m | Reduces token validation overhead |
| `_share_phone_prompted` | In-memory `set()` lost on restart | Persistent cache (Redis / DB flag) | Persistent | Prevents duplicate prompts across restarts |
| `_last_update_ids` | In-memory dict lost on restart | Redis or DB-backed cache | Persistent | Prevents update replay after restart |

### 5.2 Implementation Approaches

**Option A: In-process `functools.lru_cache` + `cachetools.TTLCache`**
- Simplest, no infrastructure changes.
- Good for `FeatureRepository.list_all()`, `RuntimeInstance` resolution, menu labels.
- **Limit:** Process-local only; doesn't help with multi-process deployments.

**Option B: Redis (via `redis-py` or `aioredis`)**
- Shared across processes and restarts.
- Ideal for `_last_update_ids`, `_share_phone_prompted`, contact lookups.
- Requires adding Redis to the stack.

**Option C: Database-backed caching**
- Use existing PostgreSQL/SQLite for `_last_update_ids` and `_share_phone_prompted`.
- Simpler infrastructure but adds DB load.

### 5.3 Recommended Caching Architecture

```python
# instance_service.py
from cachetools import TTLCache

_instance_runtime_cache: TTLCache[str, RuntimeInstance] = TTLCache(maxsize=200, ttl=60)
_feature_cache: list[FeatureDefinition] | None = None

class InstanceService:
    def get_runtime_instance(self, db, instance_key):
        if instance_key in _instance_runtime_cache:
            return _instance_runtime_cache[instance_key]
        runtime = self._to_runtime(db, row)
        _instance_runtime_cache[instance_key] = runtime
        return runtime

    def _invalidate_instance_cache(self, instance_key):
        _instance_runtime_cache.pop(instance_key, None)
```

**For Chatwoot clients (critical leak fix):**
```python
from cachetools import TTLCache

_chatwoot_clients: TTLCache[str, ChatwootClient] = TTLCache(maxsize=50, ttl=3600)

def _get_chatwoot_client(cfg):
    key = f"{base_url}|{token}"
    client = _chatwoot_clients.get(key)
    if client is None:
        client = ChatwootClient(base_url=base_url, token=token)
        _chatwoot_clients[key] = client
    return client
```

---

## 6. Prioritized Action Plan

### Phase 1 — Critical (Do Immediately)

1. **Replace `requests` with `httpx.AsyncClient` in `enterprise_gre_service.py`**
   - Add timeout (`Timeout(connect=5, read=10)`).
   - Remove hardcoded IP; make URL configurable.
   - Remove backdoor phone numbers.
   - Replace `print()` with `logger.info()`.
   - Raise domain exceptions, not `HTTPException`.

2. **Fix unbounded `ChatwootClient` caches**
   - Use `cachetools.TTLCache(maxsize=50, ttl=3600)` in Bridge + Enterprise services.
   - Add `close()` to services that iterates cached clients and calls `aclose()`.
   - Hook into app shutdown (`lifespan` yield cleanup).

3. **Fix Telegram long-polling timeout mismatch**
   - Pass explicit `HTTPXRequest(read_timeout=35)` to `Bot` so it exceeds the API long-poll timeout.

4. **Fix opposite failure semantics in polling service**
   - Enterprise updates: if `handle_platform_update` fails, **do not persist** the update ID.
   - Bridge updates: if `ingest_platform_event` fails, catch and log, then **still persist** the update ID (or use a dead-letter queue).

### Phase 2 — High (Next Sprint)

5. **Add caching to `InstanceService`**
   - `TTLCache` for `RuntimeInstance` (60s).
   - Permanent cache for `FeatureRepository.list_all()`.
   - Invalidate on instance update/delete.

6. **Add circuit breaker for Chatwoot API**
   - Use `pybreaker` or a simple error-count window.
   - Open circuit after N failures; queue messages instead of retrying.

7. **Add retry logic with exponential backoff to connectors**
   - `tenacity` decorator on `ConnectError`, `ReadTimeout`, `HTTPStatusError(502/503/504)`.

8. **Extract shared enterprise logic into a base class**
   - Create `EnterpriseBaseService` with shared methods: contact management, session lifecycle, Chatwoot payload parsing, phone normalization, attachment extraction.
   - Reduce `enterprise_bale_service.py` and `enterprise_telegram_service.py` by ~60%.

9. **Add row-level pessimistic locking**
   - `db.query(EnterpriseBaleUser).filter_by(id=user.id).with_for_update().first()` when handling updates.

### Phase 3 — Medium (Backlog)

10. **Move file I/O to threadpool or `aiofiles`**
    - `enterprise_document_service.py`: `await asyncio.to_thread(path.write_bytes, content)`.
    - `bale_polling_service.py`: `await asyncio.to_thread(self._write_temp_sms_result_dump, ...)`.

11. **Add file size limits to document uploads**
    - Reject uploads > 10MB before `upload.read()`.

12. **Use Pydantic models for platform metadata normalization**
    - Replace 450-line if/elif chains with `BaleMetadata`, `TelegramMetadata`, etc.

13. **Add bounded task pool / queue for update processing**
    - `asyncio.Queue` per instance; worker coroutines process updates concurrently.

14. **Add Redis (or DB) persistence for `_last_update_ids` and `_share_phone_prompted`**
    - Survive restarts without replay or duplicate prompts.

### Phase 4 — Low (Polish)

15. **Remove dead code `bale_client.py`**.
16. **Add `/health` endpoint** that checks DB connectivity and connector health.
17. **Add webhook signature validation** for Chatwoot.
18. **Refactor `_show_root_menu` commit boundaries** — either always commit inside or always at the caller, not both.

---

## 7. Standards Comparison

| Standard / Best Practice | Current State | Gap |
|--------------------------|---------------|-----|
| **SOLID / SRP** | God classes (3,000+ lines) | Severe |
| **DRY** | ~5,000 lines duplicated between Bale/Telegram | Severe |
| **Layered Architecture** | FastAPI exceptions leak into services | Moderate |
| **Async-First I/O** | Sync DB + sync `requests` in async code | Critical |
| **Connection Pooling** | Unbounded client caches; ephemeral SMS client | High |
| **Circuit Breaker / Retry** | None | High |
| **Caching** | Almost none | High |
| **Health Checks** | None | Moderate |
| **Structured Logging** | Good | ✅ Meets standard |
| **Encryption at Rest** | Fernet + JSON encryptor | ✅ Meets standard |
| **Type Hints** | Good coverage | ✅ Meets standard |
| **Repository Pattern** | Thin wrappers exist | ✅ Meets standard (could be thinner) |


---

## 8. Data Layer Deep Dive (Models, Repositories, Alembic)

*Findings from dedicated audit of `app/models.py`, `app/repositories/`, `app/db.py`, and Alembic migrations.*

### 8.1 SQLAlchemy Model Style
- **Monolithic file**: All 15 models live in `app/models.py` (784 lines). Manageable now but will become a bottleneck.
- **Legacy SQLAlchemy 1.x**: Uses `declarative_base()`, `Column(...)`, string-based `relationship()`. Not using SQLAlchemy 2.0's `Mapped`, `mapped_column`.
- **UUID primary keys everywhere**: All tables use `String(36)` synthetic PKs. Consistent but less efficient than `bigint` for high-write tables (`message_mappings`, `enterprise_bale_pending_messages`).

### 8.2 Relationships & Cascade
All cascade behaviors are **correctly configured**:
- `Instance` → children all use `all, delete-orphan`
- `Conversation` → `message_mappings` and `runtime_state` correctly cascaded
- `EnterpriseBaleUser` → `sessions` cascaded
- Foreign key `ondelete` policies well-chosen (`RESTRICT` on platform type, `SET NULL` on group orphaning, `CASCADE` on dependents)

### 8.3 Index Issues

#### Redundant indexes (unique + index)
SQLAlchemy creates a unique constraint **and** a separate index when both `unique=True` and `index=True` are set. The DB already indexes unique constraints.
- `PlatformType.key` — `unique=True, index=True`
- `Instance.instance_key` — `unique=True, index=True`

#### Redundant single-column indexes shadowed by composite indexes
PostgreSQL can use leftmost prefixes of composite indexes. These single-column indexes are wasted:

| Table | Redundant Index | Shadowed By |
|---|---|---|
| `conversations` | `ix_conversations_instance_id` | `uq_instance_chatwoot_conversation` |
| `instance_feature_overrides` | `ix_instance_feature_overrides_instance_id` | `uq_instance_feature_override` |
| `enterprise_bale_users` | `ix_enterprise_bale_users_instance_id` | `uq_enterprise_bale_user_instance_chat` |
| `enterprise_telegram_users` | `ix_enterprise_telegram_users_instance_id` | `uq_enterprise_telegram_user_instance_chat` |
| `enterprise_manual_groups` | `ix_enterprise_manual_groups_instance_id` | `ix_enterprise_manual_groups_sort_order` |
| `enterprise_manual_group_assignments` | `ix_enterprise_manual_group_assignments_group_id` | `ix_enterprise_manual_group_assignments_sort_order` |
| `message_mappings` | `ix_message_mappings_conversation_id` | `ix_message_mappings_conversation_direction_created` |

#### Missing indexes for hot query patterns

| Table / Columns | Why Needed |
|---|---|
| `conversations` `(instance_id, platform_conversation_id, is_active, updated_at DESC)` | `get_by_platform_id()` |
| `conversations` `(instance_id, chatwoot_contact_id, chatwoot_inbox_id, is_active DESC, updated_at DESC)` | `list_by_contact()` |
| `message_mappings` `(conversation_id, created_at DESC)` | `list_by_conversation()` |
| `enterprise_bale_sessions` `(user_id, route_key, status, updated_at DESC, created_at DESC)` | `get_unresolved_for_user_route()` |
| `enterprise_telegram_sessions` `(user_id, route_key, status, updated_at DESC, created_at DESC)` | Same for Telegram |
| `enterprise_bale_users` `(instance_id, phone_number, updated_at DESC)` | `list_by_phone_number()` |
| `enterprise_document_assets` `(instance_id, asset_type, is_active, sort_order, created_at)` | `list_for_instance()`, `get_active_catalog()` |
| `enterprise_manual_groups` `(instance_id, is_active, sort_order)` | `list_by_instance()` |
| `instances` `(is_enabled)` | `_list_enabled_instance_keys()` (called every 10s) |
| `enterprise_bale_sessions` `(status)` | Filters `status != 'resolved'` |

### 8.4 N+1 Query Risks

**Every `relationship()` uses default `lazy="select"`**. No `joinedload`, `selectinload`, or `lazy="raise"` is configured.

High-risk hotspots:
1. **`InstanceRepository.list_all()`** + accessing `.platform_type`, `.runtime_state`, `.conversations` → N queries per instance
2. **`ConversationRepository.list_by_instance()`** + `.message_mappings` or `.runtime_state` → N queries
3. **`EnterpriseBaleSessionRepository.list_by_instance()`** — joins `.user` for filtering but not eager loading. Accessing `session.user` fires N queries.
4. **`EnterpriseDocumentAssetRepository.list_by_group()`** — accessing `asset.group_assignments` lazy-loads per asset.
5. **`EnterpriseManualGroupRepository.list_by_instance()`** — accessing `group.assignments` or `group.instance` fires lazy loads.

### 8.5 Repository Anti-Patterns

#### Inconsistent `flush()` behavior
- `RuntimeStateRepository.get_or_create()` does **not** call `flush()` after adding.
- `ConversationRuntimeStateRepository.get_or_create()` **does** call `flush()`.
- This means code depending on `RuntimeStateRepository` may not see new rows in the same transaction.

#### Inefficient bulk updates (fetch-then-mutate loops)
- `ConversationRepository.deactivate_platform_mappings()` loads all rows, mutates one-by-one → N `UPDATE` statements.
- `EnterpriseDocumentAssetRepository.deactivate_catalogs()` same pattern.
- **Fix:** Use `query.update({Model.field: value}, synchronize_session=False)`.

#### Inefficient in-memory aggregation
- `EnterpriseDocumentAssetRepository.next_sort_order()` loads **all rows** for an instance and computes `max()` in Python.
- **Fix:** Use `func.max(EnterpriseDocumentAsset.sort_order)` in SQL.
- `EnterpriseManualGroupRepository.next_sort_order()` is better (limits to 1) but still could use `func.max()`.

#### Inconsistent `get_by_id`
- Most repositories use `self.db.get(Model, pk)` (identity map, fastest).
- `EnterpriseManualGroupRepository.get_by_id()` uses `self.db.query(Model).filter(Model.id == pk).first()` — slower and inconsistent.

### 8.6 Transaction Boundaries — Most Serious Data Layer Issue

**`get_db()` never commits.** Controllers inject a single `db` session and pass it to multiple services. However, **individual services call `db.commit()` internally**.

Example from `api_v1_controller.py`:
```python
async def create_instance(payload, db: Session = Depends(get_db)):
    response = instances.create_instance(db, payload)          # commits here
    auto_create_result = await _maybe_auto_create_inbox(db=db, ...)  # may raise
```

If `create_instance` succeeds and commits, but `_maybe_auto_create_inbox` later raises, the instance row is **already persisted** and will **not** be rolled back. The endpoint returns 500, yet the DB is in a **partially committed state**.

**Fix:** Commit **once** at the controller boundary (or via a unit-of-work / `@transactional` decorator), not peppered throughout services.

### 8.7 Alembic Migration Concerns
- Migrations `0004` and `0008` wrap `op.create_table()` in `if not _has_table(...)` guards. Defensive but non-standard — hides state drift.
- `0001` uses `op.bulk_insert()` for seeding; `0004`/`0008` use raw `bind.execute(sa.text("INSERT ..."))` — inconsistent.
- `0007` correctly uses `batch_alter_table(recreate='always')` for SQLite — good.
- `0003` has robust downgrade safety check — excellent.

---

## 9. Complete Standards Comparison Matrix

| Standard / Best Practice | Current State | Gap |
|--------------------------|---------------|-----|
| **SOLID / SRP** | God classes (3,000+ lines) | 🔴 Severe |
| **DRY** | ~5,000 lines duplicated Bale/Telegram | 🔴 Severe |
| **Layered Architecture** | FastAPI exceptions in services; `print()` in services | 🟠 High |
| **Async-First I/O** | Sync DB + sync `requests` in async code | 🔴 Critical |
| **Connection Pooling** | Unbounded client caches; ephemeral SMS client | 🔴 Critical |
| **Circuit Breaker / Retry** | None | 🟠 High |
| **Caching** | Almost none | 🟠 High |
| **Health Checks** | None | 🟡 Medium |
| **Structured Logging** | Excellent | ✅ Good |
| **Encryption at Rest** | Fernet + JSON encryptor | ✅ Good |
| **Type Hints** | Good coverage | ✅ Good |
| **Repository Pattern** | Thin wrappers, mostly correct | ✅ Good |
| **Transaction Boundaries** | Scattered commits, partial commit risk | 🔴 Critical |
| **Database Indexes** | Several missing, several redundant | 🟠 High |
| **N+1 Prevention** | All relationships `lazy="select"` | 🟠 High |
| **Pydantic Validation** | Good in schemas | ✅ Good |
| **Webhook Security** | No signature validation | 🟠 High |
| **Authentication** | None | 🟠 High |
| **CORS** | `allow_origins=['*']` | 🟡 Medium |
| **File Upload Limits** | No size limits | 🟠 High |

---

## 10. Summary — What to Fix First

### This Week (Critical)
1. Replace `requests` with `httpx.AsyncClient` in `enterprise_gre_service.py`
2. Fix unbounded `ChatwootClient` caches with `TTLCache` + lifecycle management
3. Fix Telegram long-polling timeout mismatch (`HTTPXRequest` read timeout)
4. Fix opposite failure semantics in `bale_polling_service.py` (enterprise vs bridge)

### Next Sprint (High)
5. Add `TTLCache` for `RuntimeInstance` resolution (60s)
6. Add circuit breaker for Chatwoot API
7. Add retry logic with exponential backoff to connectors
8. Extract shared enterprise base class (eliminate ~5,000 lines duplication)
9. Add missing composite indexes for hot query patterns
10. Replace fetch-then-mutate loops with bulk `query.update()`

### Backlog (Medium)
11. Move file I/O to `asyncio.to_thread()` or `aiofiles`
12. Add file size limits to uploads
13. Add Pydantic models for platform metadata normalization
14. Centralize transaction commits at controller boundary
15. Add eager loading (`selectinload`) to repository list methods
16. Add `lazy="raise"` guardrails to prevent accidental N+1
17. Persist `_last_update_ids` and `_share_phone_prompted` to Redis/DB

### Polish (Low)
18. Remove dead code `bale_client.py`
19. Add `/health` endpoint
20. Add Chatwoot webhook signature validation
21. Migrate to SQLAlchemy 2.0 style (`mapped_column`, `Mapped`)
