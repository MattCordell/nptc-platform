# Managing registry properties

An administrator can add a **property** to the registry, change some of its details, and
retire it when it is no longer needed. This guide covers those three tasks. To read how a
property is defined, see [Browsing the property registry](browsing-the-property-registry.md).
To record a property's values on an entry, see
[Editing registry properties](editing-registry-properties.md).

You need the Administrator role. Every button on these screens is visible to anyone who can
open the registry. If you do not have the role, the server refuses your change and the
screen shows its reason. If your sign-in did not include a second step, the site asks you
to complete it first.

A property is never deleted. Retiring it keeps every value already recorded.

## Add a property

1. Open **Administration → Property registry**, then choose **New property**. You can also
   go straight to `/admin/properties/new`.
2. Fill in the form. Choose a value for **Datatype**, **Cardinality** and **Scope**; they
   start empty so that you decide each one.
3. Give a **Reason**, then choose **Create property**.

When it succeeds, the screen opens the new property's page.

| Field | What to enter |
|---|---|
| **Key** | Lowercase letters, digits and underscores, starting with a letter, up to 63 characters. Exports use the key, and it cannot change later. |
| **Label** | The name people see. You can change it later. |
| **Datatype** | The kind of value the property holds. The list comes from the server. You cannot change it later. |
| **Cardinality** | How many values an entry can hold. You cannot change it later. |
| **Scope** | Whether the property appears when proposing a test, when maintaining one, or both. You cannot change it later. |
| **Display order** | A whole number from -2,147,483,648 to 2,147,483,647. Lower numbers come first. Leave it empty for 0. |
| **Required for submission**, **Required for publication**, **Used as a catalogue filter** | Tick the ones that apply. |
| **Constraints** | Optional. A JSON object. The hint under the box lists the names the chosen datatype accepts. |
| **Reason** | Why you are making the change. It goes to the audit log and is not published. |

### Terminology binding

Some datatypes tie a property to a code system. For those, a **Terminology binding** group
appears. Choose what the property is **Bound to**:

- **Value set:** enter the **Value set URI**, the **Binding strength** and the SNOMED CT
  **Edition**, for example `au`.
- **Local code system:** enter the key of the local code system.

## Change a property

1. Open the property's page and choose **Edit property**.
2. The key, datatype, cardinality and scope appear as text under **Fixed once created**.
   You cannot change them.
3. Change the label, display order, flags or constraints. Saving replaces the whole
   constraints object, so keep every name you still need.
4. Give a **Reason**, then choose **Save changes**.

The screen sends only what you changed. If you changed nothing, it asks you to change at
least one field.

### If someone else changed the property first

The screen says who changed it and when, and that nothing was saved. It then reloads the
property. Fields you have not edited show the new values, and the fields you edited keep your
text. Check that your change is still needed, then choose **Save changes** again.

The screen sends only the fields you edited. If you type an old value back to undo the other
person's change, that counts as an edit and is sent.

## Retire a property

1. Open the property's page and choose **Deprecate property**.
2. Read the warning. Deprecation cannot be undone. Entries keep the values already recorded,
   but the property no longer appears on data-entry forms. To use it again, create a new
   property.
3. Give a **Reason**, then choose **Deprecate property** in the dialog.

The dialog closes, the status changes to **Deprecated**, and a screen reader announces it.
The built-in system properties cannot be deprecated. The dialog shows the reason if you try.
If someone else has already deprecated the property, the dialog says so and the page reloads
to show the new status. Choose **Cancel**, or press Escape, to close the dialog without
changing anything.

## If something goes wrong

- **The server refuses the change.** The form shows the server's reason at the top, and
  keyboard focus moves to it. What you typed stays in the form. If you try again and the
  screen finds a problem before sending, the server's earlier reason disappears and the new
  problem shows instead.
- **The key already exists.** Choose a different key.
- **The constraints are not valid for the datatype.** The server says so but not which name
  is wrong. Check the names against the hint under the Constraints box.
- **The datatypes cannot be loaded.** The create screen says so. Reload the page to try
  again.
- **There is no way to delete a property.** The server always refuses. Deprecate it instead.

The screens check some things before sending: the key's format, the binding fields, and that
Constraints is one JSON object. These show as a list at the top of the form, and each item
links to its field.

## Using it with a keyboard or screen reader

Press Tab to move through each form in order. A visible ring shows where you are. Select
lists and checkboxes work as they do elsewhere. In the Deprecate dialog, Tab stays inside the
dialog, and Escape closes it and returns you to the button that opened it.

Each page has one main heading. After a successful save, focus moves to the main region of
the page you land on. After a deprecation, focus moves to the property's heading. Status
always appears as words, never as colour alone.
