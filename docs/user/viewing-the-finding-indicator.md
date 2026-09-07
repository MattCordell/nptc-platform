# What the finding indicator means

When you look up a test in the catalogue, you may see a flag next to it saying there is an
**open finding**. This page explains what that means and what it does not mean.

> **This page describes API behaviour, not a screen yet.** The public catalogue pages
> (`/catalogue`, `/catalogue/{businessKey}`) are still placeholders — this indicator is
> visible today to a developer reading the API directly, and lands on the public pages
> themselves with the public search and entry UI. See issue #141.

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
