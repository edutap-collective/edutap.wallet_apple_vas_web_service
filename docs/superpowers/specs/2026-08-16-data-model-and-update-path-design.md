# edutap.wallet_apple_vas_web_service — Data model and update path

Date: 2026-08-16
Status: proposed (design phase)

## 1. Purpose and scope

This service implements the pass update web service Apple specifies in
[Adding a Web Service to Update Passes][apple-overview]. It is written to be
reusable: one deployment serves one issuer, and nothing in it is specific to a
single university.

It owns **registrations** — which device holds which pass, and how far behind
that device is. It owns no pass content, no person data and no issuance
decisions.

This document replaces the data model the service has today. The table
`ApplePassData`, which held the full pass JSON and its binary parts, is
withdrawn.

### Out of scope

| Concern | Owner |
|---|---|
| Pass content, templates, rendering | the producer (`edutap.pass_builder` contract instance, HEIDI, …) |
| Person data | `edutap.data_provider`, read by the producer, never by this service |
| Person ↔ pass binding, issuance, deactivation | the issuing contract instance |
| Estate-wide pass state | `public.pass_state` / `public.pass_instance`, written by `lmu_edutap_worker` |

## 2. What Apple requires

Quoted from [Adding a Web Service to Update Passes][apple-overview] and the
four endpoint pages linked from it. These are the constraints the design has to
satisfy; everything else in this document is our choice.

**Two different shared secrets, not one:**

> Authenticate each call to your server using a shared secret before responding
> using one of two shared secrets. Use the value of `authenticationToken` for
> the pass to authenticate the calls that register and unregister a pass, and
> to send an updated pass. The other shared secret is the device library ID, a
> value that's sent by the device when the device registers a pass. Use this
> secret to authenticate the call for the serial numbers of updated passes.

Accordingly, [Get the List of Updatable Passes][apple-list] is the only one of
the four endpoints with no `Header Parameters` section and no `401` response
code.

**The token is immutable:**

> You can update any type of pass and any information in the pass, except for
> the authentication token and the serial number. An updated pass is a new pass
> with the same pass type identifier and serial number.

**Three tables, and what the pass row is for:**

> **Pass table** — Contains the updatable passes. Information for a pass
> includes the pass type identifier, serial number, and a last-update tag. You
> define the contents of this tag and use it to track when you last updated a
> pass.

**Registration creates the pass row if it is absent:**

> Create a new entry for the pass if one doesn't exist.

**Unregistration removes the device once it is unused:**

> Delete the mapping between the pass and the device library identifier from
> the registrations table. Delete the device entry from the device table if the
> registration table has no more entries for that device.

**The push needs the pass signing material:**

> The notification uses the same certificate and private key that the creator
> of the pass used to sign the original, the pass type identifier registered by
> the device, and an empty JSON dictionary for the payload. Delete a device if
> the Apple Push Notification service (APNs) returns an error that the push
> token is invalid.

> A push notification for a pass update works only in the production
> environment.

## 3. The schema

A schema of its own, `wallet_apple_vas` — not `public`. The contract schema is
for tables more than one service touches; these are touched by this service
alone. Declared through an entry point in `edutap.db_definitions`, migrated by
the migration container. No `create_all` at request time, which is what the
current `get_session` does on every call.

### `device`

| Column | Type | Note |
|---|---|---|
| `device_library_identifier` | `varchar(255)`, PK | Apple's identifier for the device |
| `push_token` | `varchar(255)` | APNs token; replaced when the device re-registers |
| `created_at`, `updated_at` | `timestamptz` | |

The push token is a credential. It is never logged and never returned by any
endpoint.

### `pass`

Bookkeeping only — no pass content. The Python class is `PassRecord`; `pass` is
a Python keyword and `Pass` is already the pass model of
`edutap.wallet_apple`.

| Column | Type | Note |
|---|---|---|
| `pass_type_identifier` | `varchar(255)`, PK | |
| `serial_number` | `varchar(255)`, PK | |
| `last_update_tag` | `bigint` | see section 7 |
| `created_at`, `updated_at` | `timestamptz` | |

Together the two key columns are Apple's identity for a pass. A row is created
either by a change event or by a registration for a pass not yet seen.

### `registration`

The many-to-many relation, plus what this device provably holds.

| Column | Type | Note |
|---|---|---|
| `device_library_identifier` | FK → `device`, PK | |
| `pass_type_identifier` | FK → `pass`, PK | |
| `serial_number` | FK → `pass`, PK | |
| `delivered_tag` | `bigint`, nullable | the `last_update_tag` this device provably holds; null until first delivery |
| `last_pushed_at` | `timestamptz`, nullable | |
| `last_delivered_at` | `timestamptz`, nullable | |
| `created_at` | `timestamptz` | |

**Index on `(device_library_identifier, pass_type_identifier)`.** This is the
hot query, and it is not a small result set: a `passTypeIdentifier` may cover
many pass kinds, so one device can have a two-digit number of registrations
under one of them. See section 7.

### Why these tables duplicate `public.pass_instance`

`public.pass_instance` models the same relation — its `instance_ref` is
documented as the `deviceLibraryIdentifier` for Apple VAS. The duplication is
deliberate:

- Apple's endpoints are answered synchronously and must not depend on another
  service having caught up.
- `pass_instance` has no place for a push token, and should not gain one: the
  token is device-scoped, not pass-scoped.
- As of 2026-08-16 nothing writes `pass_instance` at all — `lmu_edutap_worker`
  is a scaffold.

`public.pass_instance` stays the estate-facing record and is fed by the events
this service emits. Nobody should later "consolidate" the two and put a foreign
service in the hot path.

## 4. The authentication token

**One token per pass, derived rather than stored.**

```
token = HMAC-SHA256(issuer_secret, pass_type_identifier || 0x00 || serial_number)
```

Rendered as lowercase hex, which is 64 characters and clears the 16-character
minimum `edutap.wallet_apple` assumes. The `0x00` separator cannot occur in
either identifier, so no pair of inputs can collide by concatenation.

Both sides hold the same `issuer_secret` from the vault: the producer computes
the token when it builds the pass, this service computes it when it verifies.
One configured value, as operationally simple as the single shared token the
service has today, with the blast radius of a per-pass token.

**Why not one token per issuer.** A `.pkpass` is a ZIP, and `pass.json` inside
it carries the token in clear text. With an issuer-wide token, every pass
holder possesses the credential that unlocks every other pass — and
`GET /v1/passes/{passTypeIdentifier}/{serialNumber}` returns the full pass.
What would remain is the unguessability of serial numbers, which appear in
URLs, logs and support tickets. Apple's own statement of purpose rules it out:
the token shows the request comes "from the user who has the pass".

**Why not stored random tokens.** They would need a write path from the
producer into this service at build time — the coupling this design otherwise
avoids — and they cannot authenticate a registration for a pass this service
has not yet heard of. Derivation handles that case, which is exactly the
"registered before the pass is known" gap the old code documented and never
closed.

**Rotation.** Changing `issuer_secret` invalidates every existing token, which
is the situation Apple warns about:

> Don't change the authentication token in an update. Because passes are not
> guaranteed to be updated, there may still be devices with the old pass and
> the old authentication token. Your server would have to check the
> authentication token against the list of every token that has ever been
> valid.

So the settings carry `authentication_secret` plus an ordered
`previous_authentication_secrets` list. Verification tries the current secret
first, then the previous ones; a pass picks up the new token at its next
rebuild. Comparison is constant-time in every case. With no secret configured
the service rejects every request — a deployment that forgot the value fails
closed.

**Revocation is not a token operation.** Deactivating a pass is an issuance
decision, carried out by delivering an updated pass, not by refusing a
credential. Changing a token to revoke a pass violates the rule quoted above.

## 5. The service is person-blind

It stores no `person_uid` and receives none. On
`GET /v1/passes/{passTypeIdentifier}/{serialNumber}` it calls its configured
producer with Apple's key alone and returns what comes back.

The producer resolves the person, fetches current data from
`edutap.data_provider`, builds and signs. It can do that because it — or the
contract instance in front of it — persists the pass identity;
`edutap.pass_builder` itself does not ("The pass ID is a UUID supplied by the
caller and persisted by the caller").

Reasons, in order of weight:

1. **Reuse.** A service that holds no person identifier inherits no identity
   model. The moment it stores one, the next university gets a special case.
2. **Data protection.** Its tables then hold Apple-side identifiers and push
   tokens only — no name, no matriculation number, no person identifier.
3. **No stale copy.** The person ↔ pass binding is issuance knowledge. A copy
   here can drift, and drift means building the wrong person's pass.

**There is exactly one producer per deployment, named in configuration.** The
earlier concern — several producers behind one web service — is answered by
deploying one web service per producer under its own URL, not by resolving the
producer at runtime.

### Consequence for the event contract

**Change events must address the pass, not the person.** An event saying "new
data for person P" would force this service to resolve person → passes, and
everything above collapses. The translation belongs to `lmu_edutap_worker`,
which holds `pass_state` and is the only component that can do it correctly.

## 6. Two deployables

| | Task | Replicas |
|---|---|---|
| **web** | answers Apple's four endpoints; stateless | N |
| **notifier** | consumes change events, writes `last_update_tag`, sends APNs | M, `1 ≤ M ≤ partitions` |

The consumer does not live in the request-serving replicas. A Kafka message
reaches exactly one consumer in a group, so a change learned only in the
memory of one web replica would be invisible to the replica the device
happens to reach. **The message is a trigger; the schema is the truth.**

The notifier scales horizontally. The conditions are:

1. **The event key is the pass identity** — `pass_type_identifier` plus
   `serial_number`. Not the `passTypeIdentifier` alone: there are few distinct
   values, everything would land on few partitions, and replicas beyond that
   would idle.
2. **Replicas ≤ partitions.** The partition count is the real ceiling and
   belongs in the deployment, not in the code.
3. **Writes are idempotent and `last_update_tag` is monotonic** — write only
   when the event is newer than the stored state. The key already orders events
   per pass; the guard also covers the window during a partition-count change,
   when keys are redistributed.

Concurrency that is *not* a hazard: two changes to different passes of the same
device produce two pushes to that device. The payload is empty, Apple coalesces
notifications, and the device answers with one list query. A duplicate push
after a rebalance is the same situation, and happens with a single replica on
restart too.

The notifier holds the Pass Type ID certificate and its private key — the same
material the producer signs with, as Apple requires for the push. That is an
argument for few replicas, not for one.

## 7. Announcement and collection

Two distinct facts, in two places. Kept in one row they would hide the gap
between them, and that gap is the interesting part.

| Fact | Where | Granularity |
|---|---|---|
| there is something new | `pass.last_update_tag` | per pass |
| the device came and fetched | `registration` | per device × pass |

### The tag

Apple leaves its contents to us ("You define the contents of this tag"). It is
a value from a database sequence, not a clock. With several notifier replicas,
wall clocks on different hosts are not comparable, and that is precisely when
it matters.

### Which passes are listed

The device stores the `lastUpdated` value it last received and returns it as
`previousLastUpdated`. We answer from **both** that cursor and our own record:

```
list the registrations of this device under this pass type identifier where
    delivered_tag IS NULL                    (never delivered to this device)
 OR last_update_tag > delivered_tag          (our record of what it holds)
 OR previousLastUpdated IS ABSENT            (no cursor: Apple says list all)
 OR last_update_tag > previousLastUpdated    (its own cursor)
```

The two `IS NULL` / `IS ABSENT` arms are not decoration. `delivered_tag` is
null until the first successful delivery, and in SQL `tag > NULL` is null
rather than true — without the first arm a freshly registered pass would never
be listed, which is the one failure mode this endpoint must not have.

Each predicate closes the other's gap, and neither can shorten the list — the
one error Apple's protocol does not forgive.

- Our record closes the **sequence gap**: if replica A commits tag 100 after
  replica B committed 101, a device that was handed 101 would never see 100
  again under a cursor-only filter. Compared against what *this device* holds,
  100 is still ahead and is listed.
- The device's cursor closes the **lost response**: we set `delivered_tag` when
  we answered successfully, so a reply lost in transit would leave us believing
  the device is current. Its cursor only advances on a response it received.

`lastUpdated` in the response is the highest tag among the passes returned,
rendered as a string, matching Apple's example.

**Returning the whole list instead of filtering was considered and rejected.**
It is protocol-legal, but every returned serial number causes a
`GET /v1/passes/…`, and every one of those causes a build at the producer. A
`passTypeIdentifier` may cover many pass kinds — `pass.de.lmu.events` is the
case at hand — so this would turn each opportunistic poll by a device into
dozens of builds.

### What collection records

Written on a successful answer to `GET /v1/passes/{passTypeIdentifier}/{serialNumber}`,
including when the device asks without a push:

- `delivered_tag` — the tag current at the moment of delivery
- `last_delivered_at` — when
- `last_pushed_at` — set by the notifier when it sends a push

This makes the state readable: `pass.last_update_tag > registration.delivered_tag`
means this device is behind, and `last_delivered_at` says since when. Push
sent, never fetched, is a statement about the device rather than about the
service — the answer support needs and cannot get today.

### Sync lag is ours, "expired" is the issuer's

The service reports a mechanical fact: this device holds tag N, current is M,
last seen at T. It does not report expiry. Whether a lag invalidates a pass
depends on the validity period inside the pass content, which this service
deliberately does not know. That judgement belongs to the issuer, which
combines lag with validity — and it reaches the issuer through the event this
service emits, feeding `public.pass_instance.synced_version`.

Blurring that line would move issuance semantics back into a service that is
meant to be reusable.

### Why this matters beyond diagnostics

eduTAP requires that a person hold exactly one active serial number per PID
pass kind — Student ID, Staff ID. **This is not an Apple constraint**; Apple
permits any number of passes under one `passTypeIdentifier`, and grouping in
Wallet follows from a shared `TeamIdentifier` *and* `passTypeIdentifier`, which
issuers use deliberately for boarding passes and event passes. It is an eduTAP
rule for PID passes, originating in national identity-document law, and it does
not apply to library, catering or event passes.

Two things follow. The rule must never appear as a general uniqueness
constraint in this schema. And where a deactivation discharges a legal
obligation, "did the device actually collect the voided pass?" is evidence
rather than telemetry — which is what `delivered_tag` and `last_delivered_at`
are for.

## 8. Defects in the current implementation this design exposes

Not fixed here; listed so they are not rediscovered.

| Defect | Consequence |
|---|---|
| The token is checked on `GET /devices/{id}/registrations/{passTypeId}` | Apple sends no `Authorization` header there. Harmless with one global token, a hard failure with per-pass tokens. |
| `get_session` calls `create_all` on every request | Schema creation belongs to the migration container. |
| `and` instead of `&` in three `where()` clauses | `A and B and C` returns `A` as soon as `bool(A)` is false, and SQLAlchemy's `__bool__` on an `==` comparison compares object identity, so it is. The query filters on the **first** condition only — the device — and ignores pass type and serial number. Unregistering one pass deletes every registration of that device. |
| Unregistration answers `404` | Apple documents only `200` and `401` for that endpoint. |
| `SerialNumbers.serialNumers` | Misspelt field; the response key is wrong on the wire. |
| `send_updated_pass` reads `db_entries.first().passData` | Wrong attribute on a wrong object; the endpoint cannot ever have worked. |

## 9. Open points

- **The retrieval contract with the producer.** Which endpoint, which
  authentication, and what happens when the producer is unreachable while Apple
  is waiting. `404` and `410` may be used towards the producer; towards Apple
  the documented answers for that endpoint are only `200` and `401`.
- **Parameter name.** Apple's current endpoint page calls it
  `previousLastUpdated` and marks it a required path parameter; this service
  reads `passesUpdatedSince` as a query parameter. What the device actually
  sends decides, and it must be observed before anything is changed.
- **Deactivation mechanics.** Delivering a `voided` pass is the likely
  Apple-native route, since "an updated pass is a new pass with the same pass
  type identifier and serial number". Not verified.
- **`passTypeIdentifier` in the estate.** `public.pass_state` has `pass_id` and
  `wallet_type` but no `passTypeIdentifier`, while Apple always addresses with
  both. Either the event carries it or it is derivable from tenant and
  template.
- **Repeated prompting.** Pushed, not collected for days — push again? Apple
  coalesces, so a storm achieves nothing. An operational decision.
- **Legal reference.** The PID uniqueness rule is stated as originating in
  national identity-document law; the citation is to be supplied.
- **Async.** The service uses synchronous SQLModel and `requests` inside
  `async def`. Out of scope here, but it touches every module this design
  changes.

## 10. Evidence

- **Documented.** Everything quoted in section 2, from [Apple's current
  documentation][apple-overview] and its endpoint pages, retrieved 2026-08-16.
- **Measured, 2026-08-16.** `lmu_edutap_wallet_definitions` gives
  `student_id_v1`, `staff_id_v1` and `library_v1` the same
  `passTypeIdentifier`, `pass.de.lmu.ub`. Reported as an error to be corrected
  in that package; it is not the basis of any decision here. The case that
  carries the argument is `pass.de.lmu.events`, where several pass kinds share
  one identifier deliberately.
- **Measured, 2026-08-16.** `lmu_edutap_worker` writes neither `pass_state` nor
  `pass_instance`; its module docstring describes it as a scaffold.
- **Measured, 2026-08-16.** Against this package's own environment,
  `select(R).where((R.a == 'x') and (R.b == 'y') and (R.c == 'z'))` renders as
  `WHERE r.a = :a_1`. The `and` defect in section 8 keeps the first condition
  and drops the rest; it does not drop the first.
- **Not verified.** The open points in section 9 are marked as such and carry
  no weight in the design.

[apple-overview]: https://developer.apple.com/documentation/walletpasses/adding-a-web-service-to-update-passes
[apple-list]: https://developer.apple.com/documentation/walletpasses/get-the-list-of-updatable-passes
