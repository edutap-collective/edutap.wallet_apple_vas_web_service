# eduTAP.wallet_apple_vas_web_service - A Web Service for Apple Wallet

This eduTAP Package provides you with a reusable web service that conforms with the API specification from Apple Wallet.

It implements the four endpoints of [Adding a Web Service to Update Passes][apple-overview]:
registering a pass for updates, listing the passes a device is behind on, delivering
an updated pass, and unregistering. It owns **registrations** — which device holds
which pass, and how far behind that device is. It owns no pass content: the pass is
built by a *producer* this service fetches from at delivery time.

One deployment serves one issuer and one producer.

[apple-overview]: https://developer.apple.com/documentation/walletpasses/adding-a-web-service-to-update-passes

## Settings

Every setting is read from the environment with the prefix
`EDUTAP_WALLET_APPLE_VAS_WEB_SERVICE_`. Any of them may instead be supplied as a
Docker secret: if `<VARIABLE>_FILE` names a readable file, the setting takes that
file's stripped contents. A setting whose type is a list or a mapping takes JSON,
whether it comes from the variable or from the file.

### The issuer secret

| Variable | Required | Meaning |
|---|---|---|
| `…_AUTHENTICATION_SECRET` | yes | The issuer secret every pass authentication token is derived from. |
| `…_PREVIOUS_AUTHENTICATION_SECRETS` | no | Secrets rotated away from, newest first, as a JSON array. |

**With no secret configured the service rejects every request.** A deployment that
forgot the value fails closed rather than accepting anything.

`…_PREVIOUS_AUTHENTICATION_SECRETS` exists because rotating the issuer secret
invalidates every token already inside a pass, and a pass only picks up the new
token when it is next rebuilt. Apple:

> Because passes are not guaranteed to be updated, there may still be devices with
> the old pass and the old authentication token. Your server would have to check
> the authentication token against the list of every token that has ever been valid.

Verification tries the current secret first, then each previous one, in constant
time, without exiting early.

### The producer

| Variable | Required | Meaning |
|---|---|---|
| `…_PRODUCER_PASS_URL_TEMPLATE` | yes | Where to fetch a built pass. Must contain `{pass_type_identifier}` and `{serial_number}`. |
| `…_PRODUCER_API_TOKEN` | no | Bearer token this service presents to the producer. |
| `…_PRODUCER_TIMEOUT_SECONDS` | no | Default `10.0`. Must be greater than zero. Apple's device is waiting behind it. |

A template rather than a base URL, because the retrieval contract with the producer
is a deployment's own. Both placeholders are substituted by name; a template naming
anything else is refused with a clear error rather than a 500.

How the producer's answer is translated for the device:

| Producer answers | This service answers | Why |
|---|---|---|
| `200` | `200` with the pass | |
| `410 Gone` | `401` | The pass is permanently withdrawn. Apple documents only `200` and `401` for this endpoint, so `401` is the strongest statement available. |
| `404`, `5xx`, unreachable, timeout | `503` | Recoverable. Telling a device its credential is dead is not something Wallet recovers from — it never re-authenticates. |

### The database

| Variable | Default | Meaning |
|---|---|---|
| `…_DB_HOST` | — | |
| `…_DB_PORT` | `5432` | |
| `…_DB_NAME` | — | |
| `…_DB_USERNAME` | — | |
| `…_DB_PASSWORD` | — | Use `…_DB_PASSWORD_FILE` in a deployment. |
| `…_DB_TYPE` | `postgresql` | |
| `…_DB_DRIVER` | `psycopg2` | |

The service owns the schema `wallet_apple_vas` and touches nothing else. It creates
no tables: see *Migrations* below.

## The authentication token, and what the producer has to do

This is the one contract between this service and whoever builds the passes, and
nothing in the protocol will tell you when it is broken.

Each pass has its own authentication token, and the token is **derived, not
stored**:

```
authenticationToken = HMAC-SHA256(
    key     = issuer_secret,
    message = pass_type_identifier || 0x00 || serial_number,
)
```

rendered as **lowercase hexadecimal** — 64 characters. The separator is a single
zero byte, which occurs in neither identifier, so no two different passes can
produce the same message by concatenation.

**The producer must compute exactly this value and put it in the pass's
`authenticationToken` field, from exactly the same `issuer_secret` this service is
configured with.** There is no write path from the producer into this service and
none is needed: both sides derive the same value from the same secret, which is
also what lets this service authenticate a registration for a pass it has never
heard of — the ordinary case when a freshly issued pass is installed.

If the producer derives it differently, or uses a different secret, nothing looks
broken from the outside: the pass installs, it looks correct in Wallet, and every
registration and every delivery for it is answered `401` for ever. Check this
first when passes will not update.

In Python:

```python
import hmac
from hashlib import sha256

def authentication_token(issuer_secret: str, pass_type_identifier: str, serial_number: str) -> str:
    message = pass_type_identifier.encode("utf-8") + b"\x00" + serial_number.encode("utf-8")
    return hmac.new(issuer_secret.encode("utf-8"), message, sha256).hexdigest()
```

That is `edutap.wallet_apple_vas_web_service.tokens.derive_token`, which a producer
written in Python can import instead of reimplementing.

The device presents it as `Authorization: ApplePass <token>`.

Apple forbids changing a pass's token in an update ("An updated pass is a new pass
with the same pass type identifier and serial number"), so **deactivating a pass is
never done by changing its token** — it is done by delivering an updated, voided
pass.

## Migrations

The service does **not** create its own tables. `wallet_apple_vas` is created and
migrated by running Alembic out of this same image, under a role that has `CREATE`,
before the service starts:

```console
alembic -c /app/alembic.ini upgrade head
```

The migrations read the same `…_DB_*` environment (and the same `_FILE` secrets
convention) the service reads, so they need no configuration of their own.
Locally, `make migrate` runs the same command.

## Running it

### With Docker Compose

`docker-compose.yaml` brings up the service, a PostgreSQL, pgAdmin, Kafka and
Traefik for local work. The values it sets for the issuer secret and the producer
are development placeholders — they make the environment answer rather than 401 and
503 silently, and none of them belongs in a deployment.

```console
docker compose up --build
```

### From a checkout

```console
make venv        # .venv with the package and its dev group
make migrate     # create/upgrade the schema in the configured database
make run         # uvicorn on port 8084
```

### Tests

```console
make lint
make test-local        # no database needed
make test-integration  # needs WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN
```

The integration tests build their tables through the migrations and drop the schema
again, so point the DSN at a throwaway database:

```console
export WALLET_APPLE_VAS_WEB_SERVICE_TEST_DSN=postgresql+psycopg2://user:pass@localhost:5432/throwaway
```
