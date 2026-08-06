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

## Sources and confidentiality

**No vendor internals — from any vendor, not just the ones currently in play.**
Neither in files nor in commit messages.

The standard is academic: a statement counts as reliable only where it can be
evidenced from public information, with a link. Everything else was obtained either
by our own testing or through insider knowledge, and the three are not
interchangeable:

* **Documented** — public source, linked. May be written as fact.
* **Verified, not citable** — obtained by a person from an access-protected area and
  checked there; the reference is recorded internally but must not be published; and
  the statement has been reduced to what is not confidential. May be written as fact,
  carrying this label. It is the rule journalism uses for source protection: the claim
  stands, we know where it comes from, the reader does not get the source.

  The four conditions hold together. A statement for which nobody can name the
  internal reference does not fall here — that is insider knowledge.
* **Measured** — established by our own tests. May be written down, but always marked
  as such, because it describes what a platform did on the day we looked, not what it
  guarantees. It can change with the next release, without notice and without an
  entry in any changelog.
* **Insider knowledge** — is not written down at all.

What a platform's behaviour *means for us* stays documentable even where the
mechanism does not: "the platform enforces a deadline, it is self-healing, it is
outside our control" carries the design consequence without disclosing anything.

Contract and regulatory material is wanted and citable: eduPersonAssurance, GÉANT and
eduGAIN terms, published wallet programme obligations.

## Working practice

Branch first, never commit on `main`. Push only when asked. Lint and tests green
before opening a pull request.

Design records under `docs/superpowers/` record a decision at a point in time — do
not rewrite them to match a later state; write a new one.
