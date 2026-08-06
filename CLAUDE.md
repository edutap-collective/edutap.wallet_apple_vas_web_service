# CLAUDE.md — edutap.wallet_apple_vas_web_service

Repository-specific rules. They take precedence over the global defaults.

## Language

**English only.** This repository belongs to eduTAP proper, not to any single
institution: README, changelog, documentation, docstrings, code comments, commit
messages, pull request titles and bodies, and replies to review comments.

The language follows the repository, not the conversation. A discussion held in
German still produces English artefacts here.

## What this service is

The PassKit web service: device registration, pass delivery and the device log
endpoint for Apple Wallet passes.

## Guard rails

**The distribution name is not the import path.** The repository and the
distribution follow `edutap.wallet_<vendor>[_<product>][_<service>]`; the import path
under `src/edutap/` still carries the older spelling. Three consequences that have
already caused breakage:

* `importlib.metadata.version("…")` looks up the **distribution** name. It sits at
  module level here, so getting it wrong fails at import, not on the first request.
* `[project.scripts]` and package-data keys name the **import path**. They must not
  follow a distribution rename.
* README examples using `uvicorn …` or `python -m …` name the **import path** too.

Before any rename, list which occurrences carry which of the three roles. A blanket
search-and-replace cannot tell them apart.

**A registration is per device, not per pass.** One pass on two devices is two
registrations, and removing it on one leaves the other. Anything that reduces this to
a single per-pass flag loses information the university needs.

**An APNs 200 is not a delivery.** It means APNs accepted the push — not that a
device received it, and certainly not that it applied the update. Do not record it as
confirmation.

**Tables are created by the service today (`create_all`), which contradicts the
project's separation of duties**: no service should hold DDL rights at runtime.
Treat this as debt to be repaid towards Alembic, not as the pattern to copy.

## Confidentiality

No vendor internals from Apple or NXP — not in files, not in commit messages. What a
platform's behaviour *means for us* is documentable ("the platform enforces a
deadline, it is self-healing, it is outside our control"); the mechanics, concrete
values and rule sets behind it are not.

Contract and regulatory material is fine and wanted: eduPersonAssurance, GÉANT and
eduGAIN terms.

## Working practice

Branch first, never commit on `main`. Push only when asked. Lint and tests green
before opening a pull request.

Design records under `docs/superpowers/` record a decision at a point in time — do
not rewrite them to match a later state; write a new one.
