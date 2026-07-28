## What this changes

<!-- One or two sentences. What is different afterwards? -->

## Which check proves it

<!-- Name the check that would FAIL without this change. If none does, say so -- that is a
     conversation, not a blocker. Adding behaviour without a check that covers it is the
     thing most likely to be sent back. -->

- [ ] An existing check covers it: `validation/checks/...`
- [ ] I added a check, with its expectation hand-authored in `validation/cases/`
- [ ] No check covers it, and here is why:

## Invariants

`CLAUDE.md` lists the invariants — `ingested_at` as the only server-derived field, the collector
never rejecting, raw being append-only and partitioned by ingest date, collection doing no
enrichment, the session rules.

- [ ] This breaks none of them
- [ ] This breaks one deliberately, and I have added a `PROJECT.md` decision-log entry saying
      why, with the alternatives I rejected

## Dependencies

- [ ] No new dependency
- [ ] Adds one. What it buys, and what I rejected:

## Anything you are unsure about

<!-- Genuinely useful. Half-formed is fine. -->
