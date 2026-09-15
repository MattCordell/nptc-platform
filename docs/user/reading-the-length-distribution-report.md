# Reading the preferred-term length distribution report

> **This page describes API behaviour, not a screen yet.** There is no admin screen for
> this report — it is reachable today by a developer or administrator reading the API
> directly. See issue #152.

## What this report is for

RCPA-QAP can set a maximum preferred-term length, above which saving a term produces a
warning rather than being blocked. No maximum is set by default. This report shows how
many entries each candidate maximum would affect, so RCPA-QAP can choose a sensible value
instead of guessing.

Call `GET /catalogue/admin/preferred-term-length-distribution` with an Administrator
credential (the same one used for every other entry-editing action) to fetch it.

## What the response tells you

The response lists every preferred-term length that occurs anywhere in the catalogue, from
shortest to longest. For each length, it gives you:

- **How many entries have a preferred term exactly that long.**
- **How many entries are longer than it.** This is the number that matters when choosing a
  maximum: if you set the maximum to this length, this is how many existing entries would
  start showing a warning.

The response also gives the length of the longest preferred term currently in the
catalogue, so you can see the top of the range without scanning the whole list.

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
