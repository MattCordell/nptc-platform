# Looking up a code

If you hold a SNOMED CT code, you can find the National Pathology Test Catalogue entry that
uses it. You do not need an account. Open `/catalogue/lookup`, or go straight to
`/catalogue/code/sct/{code}`, for example `/catalogue/code/sct/000123`.

## Using the form

1. Leave **Code system** as **SNOMED CT**. It is the only system the catalogue records codes
   for today.
2. Type the code in **Code**, exactly as written. Keep any leading zeros: `000123` and `123`
   are different codes. Spaces before or after the code are ignored.
3. Choose **Look up**.

If **Code** is empty, the form lists the problem at the top, moves you there, and sends
nothing. Choose **Enter a code.** in that list to go back to the field.

## What you see

The form takes you to the page for that code. It has the heading **Code lookup**, and the
system and the code you asked for sit just below the heading.

### When an entry binds the code

A box called **Matching entry** shows:

| Row | What it shows |
|---|---|
| **Entry** | The test's name, as a link to its page, with its identifier in brackets. |
| **Entry status** | The status of the whole entry, such as **Active**, in words. |
| **Code** | The code, in a grey box, and the status of the code on this entry: **Active** or **Retired**. |
| **Retirement** | Only for a retired code: the reason it was retired and, if there is one, **Replaced by** the code that took its place. |

Choose the entry's name to read the whole entry. See [Reading an entry](reading-an-entry.md).

A retired code still finds its entry. The box then says **This code is retired on this
entry.** If the same code was retired on more than one entry, you see the one retired most
recently.

### When no entry binds the code

A box called **No matching entry** repeats the catalogue's answer: no published entry
matches this system and code, and which systems it knows. The answer is the same whether the
code has no entry or the system is not one the catalogue knows, so the page does not say
which. Check the code, then use **Look up another code**.

### When something goes wrong

- **Code not accepted** means the catalogue refused the system or code as written. Check both
  and look up again.
- **Lookup failed** means the page could not reach the catalogue. Choose **Try again**. The
  button reads **Trying again…** and keeps focus while it works, and the failure box stays
  until the answer arrives.

## Links that carry a code

The form page also accepts a full system address and a code:
`/catalogue/lookup?system=http://snomed.info/sct&code=000123`. It looks the code up at once
and shows the result under the form, with the code filled in.

## Using it with a keyboard or screen reader

Press Tab to move from **Code system** to **Code** to **Look up**. Press Enter in **Code** to
look up. A visible ring shows where you are. Each page has one main heading, and each result
box has its own heading. A screen reader announces the result when it arrives: the entry
found, no match, or a failure. Status is always written in words as well as shown in colour.
