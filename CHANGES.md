# Changelog

## 1.1.0.dev0 (unreleased)

### Fixed

- The service starts again. `edutap.wallet_apple` renamed `AppleWalletSettings` to
  `Settings` in December 2024 and turned `models` into an implicit namespace package;
  both imports were repaired. The service had been in a crash loop since 2026-08-08.
- The pass authentication token no longer sits as a constant in `service.py`, where it
  was a working credential in a public repository. It comes from the settings, is
  compared in constant time, and an unconfigured deployment rejects every request
  instead of accepting any. A malformed `Authorization` header returns 401 rather than
  raising `ValueError` into a 500.
- 27 `print()` calls removed, among them the bearer token, the whole request object and
  the settings instance with the database password. `echo=True` on the engine went with
  them.
- The pass endpoints no longer write to `/var/log/…`, a directory the image does not
  contain — the first request would have failed. One of those writes recorded device
  push tokens to disk.

### Changed

- Every dependency is pinned exactly. The Dockerfile installed `edutap.wallet_apple`
  from its default branch, so each build produced a different image; that is what broke
  the service.
- Toolchain aligned with `edutap.data_provider`: hatchling instead of setuptools, ruff
  instead of flake8, black, isort and pyupgrade, `ty` instead of mypy, a `Makefile` with
  the shared targets, and Renovate to watch the pins.
- Python 3.13 is the floor; 3.10 reaches end of life in October 2026.
- CI runs the test suite and the linters. It previously built an image and tested
  nothing.
- The image no longer installs `swig`, `libssl-dev` or `M2Crypto` — the library moved to
  `cryptography`. It builds in seconds instead of minutes and runs as a non-root user.

### Known issues

The service is startable, not yet functional. Pass delivery uses a `Pass` API that no
longer exists (`files_uuencoded`, `Pass.create`); three `where()` clauses join with
Python `and` instead of `&` and therefore filter on one criterion; `SerialNumbers`
misspells `serialNumbers` against Apple's protocol; `get_session` builds an engine and
runs `create_all` on every request; and `kafka_producer` imports a distribution that is
not declared.
