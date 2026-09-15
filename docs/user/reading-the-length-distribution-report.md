# Reading the preferred-term length distribution report

> **This page describes API behaviour, not a screen yet.** There is no admin screen for
> this report — it is reachable today by a developer or administrator reading the API
> directly. See issue #152.

## What this report is for

RCPA-QAP can set a maximum preferred-term length, above which saving a term produces a
warning rather than being blocked. No maximum is set by default. This report counts, for
each length that actually occurs in the catalogue, how many entries a maximum set to that
length would affect — so RCPA-QAP can choose a sensible value instead of guessing.

Call `GET /catalogue/admin/preferred-term-length-distribution` with an Administrator
credential (the same one used for every other entry-editing action) to fetch it.

**The count includes every entry, whatever its status** — draft and withdrawn entries as
well as active ones. That matches who can actually be warned: an editor can amend a draft's
preferred term before it is ever published, and the warning applies exactly the same way
there as it does on an active entry. A report scoped to active entries alone would
undercount what a chosen maximum affects.

## What the response tells you

The response lists every preferred-term length that occurs anywhere in the catalogue, from
shortest to longest — not every possible length, only the ones at least one entry actually
has. For each length, it gives you:

- **How many entries have a preferred term exactly that long.**
- **How many entries are longer than it.** This is the number that matters when choosing a
  maximum: if you set the maximum to this length, this is how many existing entries would
  start showing a warning.

The response also gives the length of the longest preferred term currently in the
catalogue, so you can see the top of the range without scanning the whole list.

If you are considering a round number that no entry's length happens to match exactly —
50, say, when the closest lengths that actually occur are 48 and 53 — there is no row for
it. Read the count at the nearest length below it instead: the number of entries longer
than 48 is also the number of entries longer than 50, since nothing in the catalogue is
exactly 49 or 50 characters long.

## What it does not tell you

The report does not recommend a maximum — that decision is RCPA-QAP's to make. It also
does not identify *which* entries would be affected by a given maximum; it tells you how
many, not who they are.

## Setting the maximum once you have chosen one

The maximum itself is set by an operator, in the platform's configuration
(`NPTC_MAX_PREFERRED_TERM_LENGTH` — see
[`docs/operations/configuration.md`](../operations/configuration.md)), not through this
report or any other part of the API. Changing it takes effect for every future save; it
never retroactively blocks an entry that already exceeds it.
