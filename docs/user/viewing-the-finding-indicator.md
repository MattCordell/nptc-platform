# What the finding indicator means

When you look up a test in the catalogue, you may see a flag next to it saying there is an
**open finding**. This page explains what that means and what it does not mean.

> **Where you see it.** The catalogue search page shows **Open finding** beside a row's
> **Requesting term**, and nothing when no finding is open (see
> [Searching the catalogue](searching-the-catalogue.md)). An
> entry's own page (`/catalogue/{businessKey}`) shows it next to the status, below the
> heading (see [Reading an entry](reading-an-entry.md)). That page shows nothing when no
> finding is open. The API carries the flag on every list row, search result and entry.

## What it tells you

An open finding means the platform's own automated checks have flagged something about the
SNOMED CT code this test is bound to — for example, that the code may no longer be current,
or that the recorded name has drifted from what SNOMED CT now publishes for it. It is a
signal to look more closely before relying on the entry, not a statement that the entry is
wrong.

## What it does not tell you

The indicator is a plain yes/no flag. It does not say what kind of finding was raised, how
serious it is, or any other detail about it — that detail is reviewed internally by RCPA-QAP
and is not part of the public catalogue. If you need to know more about why an entry is
flagged, contact RCPA-QAP directly rather than looking for more detail in the API response;
there is none to find.

## When it clears

The flag disappears once every finding against the entry has been reviewed and acknowledged,
resolved, or superseded by RCPA-QAP — not automatically, and not on any fixed schedule. An
entry can carry the flag for some time while a review is in progress.
