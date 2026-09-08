# Bulk reclassify

**Administration → Catalogue** lets you set one registry property to one value across
every entry you have selected, in a single step — the tool this platform exists to
replace hand-editing an Excel workbook for, most often to reclassify discipline across a
group of entries at once. You need the Administrator role.

## Starting a bulk reclassify

Select the entries you want on the [catalogue list](finding-entries.md) — tick each
row's checkbox, or the header checkbox to select every row currently on screen. Once at
least one row is selected, a **Reclassify selected** button appears above the list,
showing how many entries are currently selected.

Choose **Reclassify selected** to open the dialog. Choose the property you want to set,
then enter the value or values you want every selected entry to hold. Give a changelog
note describing the change, and choose **Reclassify**.

## What "reclassify" means

Every value you enter here **replaces** whatever the property currently holds on each
selected entry — it does not add to what is already there. If an entry currently holds
more than one value for the property you are changing, all of it is replaced by what you
enter in this dialog. Check what you are about to set before saving; there is no way to
recover a replaced value except by reclassifying again with the earlier value.

A batch is limited to 100 entries at a time. If you have selected more than that, the
dialog tells you so and does not let you save until you reduce your selection.

## What happens after you save

Once the batch completes, the dialog closes and a results panel appears on the list
screen showing how many entries were applied, left unchanged, conflicted, or were not
found. If every selected entry was updated cleanly, that is all you see.

If any entries were skipped, the panel lists each one and why:

- **Someone else changed it first.** Another editor changed that entry after you
  selected it but before your batch reached it. That one entry keeps their change; every
  other entry in your batch is still updated. The panel shows what your batch would have
  set and what the entry holds now, so you can see whether reclassifying it again is
  still the right thing to do — reopen that entry's own [editing screen](editing-an-entry.md)
  to make the change there, where you can see the full picture before saving.
- **It was replaced while your batch was running.** Rare: the entry was deleted and a new
  one created under the same code while your batch was in progress. It was skipped, and
  there is nothing further to reconcile against — check whether the new entry under that
  code still needs the same change.
- **It no longer exists.** The entry was deleted before your batch reached it.

Your selection is cleared once the batch completes, whether or not everything succeeded
— the results panel is what tells you what to revisit, not the list's checkboxes.

## If something goes wrong

**A message naming what stopped the whole batch, with the dialog still open.** One kind
of check depends on an individual entry's own setting (whether it accepts any specimen)
and cannot be resolved per entry — if it fails, nothing in the batch is saved, including
entries that would otherwise have succeeded. Your chosen property, values and changelog
note are kept exactly as you left them; adjust what you are setting and try again.

**Save stays unavailable, with a message under the changelog note.** Same as
[editing a property directly](editing-registry-properties.md#if-something-goes-wrong) —
write a real sentence describing the change.

**"You cannot reclassify entries with your current sign-in."** See the note on
multi-factor authentication in [Editing an entry](editing-an-entry.md) — the same
Administrator-only MFA requirement applies here.
