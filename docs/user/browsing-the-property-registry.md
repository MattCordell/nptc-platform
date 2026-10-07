# Browsing the property registry

The property registry lists every **property** a catalogue entry can hold, such as
discipline, subgroup and specimen. The registry screens show how each property is
defined. To add, change or retire a property, see
[Managing registry properties](managing-registry-properties.md).

Any signed-in contributor role can read the registry: Provisional, Member, Reviewer or
Administrator. Visitors who are not signed in, and Observers, cannot. To record values for
a property on an entry, see [Editing registry properties](editing-registry-properties.md).

Open the registry from **Administration → Property registry**, or go straight to
`/admin/properties`.

## The property list

The list shows one row for each property, in the registry's own order.

| Column | What it shows |
|---|---|
| **Key** | The property's fixed identifier. Choose it to open the property's page. |
| **Label** | The name people see on screens. A label can change, but the key never does. |
| **Datatype** | The kind of value the property holds, such as `code` or `string`. |
| **Scope** | Where the property applies: **Submission**, **Maintenance** or **Both**. |
| **Status** | **Active**, or **Deprecated** when the property is no longer offered for new values. |

Above the table, a summary gives the number of properties, and how many are active and
deprecated.

### Showing deprecated properties

The list hides deprecated properties at first. Tick **Show deprecated properties** to
include them. Each deprecated row says **Deprecated** in its Status column. Untick the box
to hide them again.

Your choice is in the page's link. Copy the link from your browser's address bar to send
the same view to someone else.

## A property's page

Choose a key to see the whole definition. The page has up to three sections.

**Definition** always appears. It shows:

- the key, label and datatype
- the cardinality: **Zero or one**, **Exactly one**, **Zero or more** or **One or more**
  values for an entry
- the scope and status
- the origin: **System** for properties the platform ships with, **Administrator** for
  properties an administrator added
- whether a value is required for submission and for publication
- whether the property is used as a catalogue filter
- the display order, and the kind of input the edit screens use

**Terminology binding** appears when the property is tied to a code system or value set. It
shows only the details that exist for that property: what it is bound to, the value set
address, the local code system, the binding strength and the SNOMED CT edition. Addresses
and codes appear exactly as stored.

**Constraints** appears when the property has extra limits. It lists each limit by its
stored name, such as `maxLength` or `forbidden_codes`, followed by its value. The page
shows these names as they are, because each datatype defines its own.

Choose **Back to the property registry** to return to the list. **Edit property** and
**Deprecate property** open the screens described in
[Managing registry properties](managing-registry-properties.md). The list has a
**New property** button for the same reason. **Deprecate property** is hidden once a
property is deprecated.

## If something goes wrong

- **The registry cannot be loaded.** The page says so. Try again in a moment, or contact an
  administrator if it keeps happening.
- **You are not allowed to read the registry.** The page shows the reason the server gave.
  Ask an administrator to check your role.
- **A property's key is not found.** The page names the key and asks you to check it. The
  link may have a typing mistake, or the property may have been removed.
- **The page has content but cannot refresh it.** It keeps showing what it has and warns you
  that it may be out of date. Reload the page to try again.

Each of these messages is also announced to screen readers.

## Using it with a keyboard or screen reader

Press Tab to move through the page: the **Show deprecated properties** box, then each key
in the table, then the links on a property's page. A visible ring shows where you are.
Press Space to tick or untick the box, and Enter to open a key.

Each page has one main heading. The table has a caption, **Registry properties**. Status
always appears as words, never as colour alone. When you tick or untick the box, a screen
reader announces how many properties the list now shows.
