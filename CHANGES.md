# Changelog

## 1.1.0.dev0 (unreleased)

Two pieces of work: getting the service to start again, and giving it a data model
and an update path that actually answer Apple's four endpoints. The `ApplePassData`
table, which held the full pass JSON and its binary parts, is withdrawn — the pass
content belongs to the producer that built it, and this service fetches it at
delivery time.

### Added

- Three tables — `device`, `pass`, `registration` — in a schema of its own,
  `wallet_apple_vas`, declared to `edutap.db_definitions` through an entry point.
- **Alembic migrations**, run out of this image by the deployment
  (`alembic -c /app/alembic.ini upgrade head`). Nothing else in the estate creates
  this schema. Autogenerate is bounded to the package's own schema, or it proposes
  dropping every other service's tables in the shared database.
- **A per-pass authentication token**, derived rather than stored:
  `HMAC-SHA256(issuer_secret, pass_type_identifier || 0x00 || serial_number)`,
  lowercase hex. The producer derives the same value from the same secret; there is
  no write path between the two, and a registration for a pass this service has
  never heard of authenticates. `previous_authentication_secrets` covers a rotation,
  since a pass only picks up a new token when it is next rebuilt. **The derivation
  is the contract with the producer** and is written down in the README.
- **Pass delivery from the configured producer.** One producer per deployment, named
  in configuration by a URL template. A producer `410` is answered `401` (the pass
  is permanently gone, and Apple documents only `200` and `401` here); everything
  else that is not `200` — `404` included — is answered `503`, because it is
  recoverable and a device cannot recover from being told its credential is dead.
- **The update list** answers from both this service's own record (`delivered_tag`)
  and the device's cursor, accepting `passesUpdatedSince` and `previousLastUpdated`
  and ignoring an unparsable value. Neither predicate can shorten the list.
- `delivered_tag` and `last_delivered_at` are written on every successful delivery,
  including a re-fetch that changes nothing: they record contact, not change.
- Every setting is documented in the README, including the token derivation a
  producer has to reproduce, and the development `docker-compose.yaml` now sets an
  issuer secret and a producer — without them the environment came up and answered
  401 to every registration and 503 to every delivery.

### Changed

- Every dependency is pinned exactly. The Dockerfile installed `edutap.wallet_apple`
  from its default branch, so each build produced a different image; that is what
  broke the service.
- Toolchain aligned with `edutap.data_provider`: hatchling instead of setuptools,
  ruff instead of flake8, black, isort and pyupgrade, `ty` instead of mypy, a
  `Makefile` with the shared targets, and Renovate to watch the pins.
- Python 3.13 is the floor; 3.10 reaches end of life in October 2026.
- CI runs the test suite and the linters. It previously built an image and tested
  nothing. It also checks that the migrations reached the image.
- The image no longer installs `swig`, `libssl-dev` or `M2Crypto` — the library
  moved to `cryptography`. It builds in seconds instead of minutes and runs as a
  non-root user.

### Fixed

- The service starts again. `edutap.wallet_apple` renamed `AppleWalletSettings` to
  `Settings` in December 2024 and turned `models` into an implicit namespace
  package; both imports were repaired. The service had been in a crash loop since
  2026-08-08.
- The pass authentication token no longer sits as a constant in `service.py`, where
  it was a working credential in a public repository. It comes from the settings, is
  compared in constant time, and an unconfigured deployment rejects every request
  instead of accepting any. A malformed `Authorization` header returns 401 rather
  than raising `ValueError` into a 500.
- 27 `print()` calls removed, among them the bearer token, the whole request object
  and the settings instance with the database password. `echo=True` on the engine
  went with them.
- The pass endpoints no longer write to `/var/log/…`, a directory the image does not
  contain — the first request would have failed. One of those writes recorded device
  push tokens to disk.
- `get_session` no longer builds an engine and runs `create_all` on every request.
  The engine is process-wide, and the schema comes from the migrations.
- `SerialNumbers.serialNumers` was misspelt, so the response key was wrong on the
  wire.
- Three `where()` clauses joined their conditions with Python `and` instead of `&`,
  which keeps the first condition and silently drops the rest. Unregistering one
  pass deleted every registration of that device.
- Unregistration answers `200`, not `404`: that is not among the codes Apple
  documents for the endpoint.
- `send_updated_pass` read a wrong attribute on a wrong object and cannot ever have
  worked.
- The list endpoint no longer demands an `Authorization` header. Apple authenticates
  it with the device library identifier, and sends no header there.
- The five routes are `def`, not `async def`. They do blocking work, and an
  `async def` body runs on the event loop — one unreachable producer stalled every
  request in the worker.
- `Device.updated_at` is bumped on an upsert. SQLAlchemy's `onupdate` does not fire
  inside an `ON CONFLICT` `set_`, so the column read as "never touched" after every
  push token change.
- The delivery response's `Content-Disposition` no longer interpolates the serial
  number unescaped, and the producer URL template is no longer formatted outside
  every guard — both turned caller-controlled or misconfigured input into a `500` on
  an endpoint whose contract is `200` or `401`.
- The delivery update's `SELECT` is ordered, so two concurrent deliveries of one
  pass cannot deadlock each other into a `500`.
- A complex setting can be supplied as a Docker secret: `FileSecretsSource` reported
  every field as simple and never called its own preparation step, so
  `previous_authentication_secrets` could only come from an environment variable.

### Removed

- `EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_AUTH_REQUIRED`. Set to `false` it disabled
  authentication entirely, handing the full pass to anyone who guessed a serial
  number. It appeared in no test, no README and no compose file, and defaulted to
  `True`, so no deployment that omits it is affected.

### Known issues

- `kafka_producer` still imports `kafka` while the package declares `aiokafka`, and
  reads settings that do not exist. Nothing imports it and no endpoint reaches it;
  it is kept because the events it describes are still wanted, and it needs
  rewriting against `edutap.data_models` before it can run.
- Nothing raises `pass.last_update_tag` after the row is created, so no pass is ever
  announced as changed. That is the notifier's job, and the notifier is the next
  deployable — see the design document, section 6.
- The service is synchronous throughout: SQLModel over psycopg2 and `requests`. The
  routes run in the threadpool, which is where that belongs, but an async rework is
  still owed.
