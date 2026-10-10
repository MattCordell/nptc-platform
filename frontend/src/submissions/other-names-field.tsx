import { Button } from "../components/button.tsx";
import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { splitSynonyms } from "../catalogue/split-synonyms.ts";
import { FIELD_IDS, blankName } from "./form-state.ts";
import type { NameRow } from "./form-state.ts";

/**
 * The other names a new test is known by (FR-23). A short list to add to and
 * remove from, not the entry form's synonym editor, which exists to retire and
 * reinstate names that are already stored.
 *
 * Pasting a list such as `Zovirax; Cyclir` fills one row per name, split the
 * way the catalogue splits a pasted synonym cell (FR-04).
 */

function atLeastOne(rows: NameRow[]): NameRow[] {
  return rows.length === 0 ? [blankName()] : rows;
}

export function OtherNamesField({
  rows,
  error,
  onChange,
}: {
  rows: NameRow[];
  error?: string;
  onChange: (rows: NameRow[]) => void;
}) {
  const errorId = error ? `${FIELD_IDS.synonyms}-error` : undefined;

  function setTerm(id: string, term: string) {
    onChange(rows.map((row) => (row.id === id ? { ...row, term } : row)));
  }

  function paste(id: string, text: string): boolean {
    const parts = splitSynonyms(text);
    if (parts.length < 2) {
      return false;
    }
    const at = rows.findIndex((row) => row.id === id);
    const added = parts.slice(1).map((term) => ({ ...blankName(), term }));
    const replaced = rows.map((row) =>
      row.id === id ? { ...row, term: parts[0] } : row,
    );
    onChange([...replaced.slice(0, at + 1), ...added, ...replaced.slice(at + 1)]);
    return true;
  }

  return (
    <fieldset
      id={FIELD_IDS.synonyms}
      tabIndex={-1}
      aria-describedby={errorId}
      className="m-0 flex flex-col gap-3 border-0 p-0"
    >
      <legend className="mb-1 text-sm font-medium text-[var(--color-text)]">
        Other names
      </legend>
      <p className="m-0 text-sm text-[var(--color-text-muted)]">
        Other names this test is known by. Paste a list separated by semicolons to add
        several at once.
      </p>
      {rows.map((row, index) => (
        <div key={row.id} className="flex items-end gap-2">
          <div className="flex-1">
            <Field
              id={`${FIELD_IDS.synonyms}-${index + 1}`}
              label={`Other name ${index + 1}`}
            >
              {(controlProps) => (
                <input
                  {...controlProps}
                  className={INPUT_CLASSES}
                  type="text"
                  value={row.term}
                  onChange={(event) => setTerm(row.id, event.target.value)}
                  onPaste={(event) => {
                    if (paste(row.id, event.clipboardData.getData("text"))) {
                      event.preventDefault();
                    }
                  }}
                />
              )}
            </Field>
          </div>
          <Button
            type="button"
            variant="danger"
            aria-label={`Remove other name ${index + 1}`}
            onClick={() =>
              onChange(atLeastOne(rows.filter((other) => other.id !== row.id)))
            }
          >
            Remove
          </Button>
        </div>
      ))}
      <div>
        <Button
          type="button"
          variant="secondary"
          onClick={() => onChange([...rows, blankName()])}
        >
          Add another name
        </Button>
      </div>
      {error ? (
        <p id={errorId} className="m-0 text-sm text-[var(--color-danger)]">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
