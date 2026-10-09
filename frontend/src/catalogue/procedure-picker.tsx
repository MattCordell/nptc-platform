import { Combobox } from "@base-ui/react/combobox";
import { useState } from "react";

import { refusalDetail } from "../api/conflicts.ts";
import { useProcedureSearch } from "../api/queries.ts";
import { Field } from "../components/field.tsx";
import { INPUT_CLASSES } from "../components/input-classes.ts";
import { useDebouncedValue } from "./use-debounced-value.ts";

/**
 * Searching for a SNOMED CT procedure to bind (FR-26).
 *
 * The terminology server filters, not the browser: `filter={null}` hands the
 * list to Base UI as given. The server fixes the scope at `<71388002
 * |Procedure|`, and a typed code outside it comes back empty, so the picker
 * is the editor's only guard against binding a concept outside Procedure. The
 * write path does not check it. The picker holds no selection: a pick is
 * handed to `onPick`, and the caller resolves the code's names.
 */

const SEARCH_DEBOUNCE_MS = 400;

const ITEM_CLASSES =
  "flex min-h-8 cursor-pointer select-none items-center gap-2 px-2 py-1 text-sm text-[var(--color-text)] data-[highlighted]:bg-[var(--color-accent)] data-[highlighted]:text-[var(--color-accent-contrast)]";

interface Option {
  value: string;
  label: string;
}

const itemLabel = (option: Option): string => option.label;

export function ProcedurePicker({
  id,
  label,
  onPick,
}: {
  id: string;
  label: string;
  onPick: (code: string) => void;
}) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const query = useDebouncedValue(text.trim(), SEARCH_DEBOUNCE_MS);
  const search = useProcedureSearch(query);

  const options: Option[] =
    query === text.trim() && search.data
      ? search.data.items.map((item) => ({
          value: item.code,
          label:
            item.au_preferred_term === null
              ? item.code
              : `${item.code} — ${item.au_preferred_term}`,
        }))
      : [];

  const typed = text.trim().length > 0;
  const settled = typed && query === text.trim();
  const outage = settled && search.isError;
  const noMatch = settled && search.isSuccess && options.length === 0;
  const total = search.data?.total ?? null;
  const truncated = settled && total !== null && total > options.length;

  const statusText = !typed
    ? null
    : !settled || search.isFetching
      ? "Searching…"
      : outage
        ? `${
            refusalDetail(search.error) ?? "The terminology server could not be reached."
          } You can still change the rest of this entry.`
        : noMatch
          ? "No procedure matches. Only concepts under Procedure can be bound."
          : truncated
            ? `Showing ${options.length} of ${total}. Type more to narrow the list.`
            : null;

  return (
    <Field
      id={id}
      label={label}
      hint="Type a term, or a SNOMED CT code. Only procedures are offered."
      error={outage ? statusText : undefined}
    >
      {(controlProps) => (
        <Combobox.Root<Option, false>
          items={options}
          filter={null}
          value={null}
          inputValue={text}
          open={open && typed}
          onOpenChange={setOpen}
          itemToStringLabel={itemLabel}
          onInputValueChange={(next, details) => {
            if (details.reason !== "item-press") {
              setText(next);
              setOpen(true);
            }
          }}
          onValueChange={(item) => {
            if (item !== null) {
              onPick(item.value);
              setText("");
              setOpen(false);
            }
          }}
        >
          <Combobox.Input
            {...controlProps}
            // Base UI hides the visible label from assistive technology while
            // the popup is open, which would leave the input unnamed.
            aria-label={label}
            className={INPUT_CLASSES}
            inputMode="search"
            autoComplete="off"
          />
          <Combobox.Portal>
            <Combobox.Positioner sideOffset={4} className="z-50 outline-none">
              <Combobox.Popup className="max-h-72 w-[var(--anchor-width)] max-w-[var(--available-width)] overflow-y-auto rounded-[var(--radius-control)] border border-[var(--color-border)] bg-[var(--color-surface)] py-1">
                {/* Inside the popup, because Base UI marks everything outside it
                    inert while it is open, and an inert live region is not read. */}
                <Combobox.Status className="text-sm text-[var(--color-text-muted)]">
                  {statusText === null || outage ? null : (
                    <p className="m-0 px-2 py-1">{statusText}</p>
                  )}
                </Combobox.Status>
                <Combobox.List>
                  {(option: Option) => (
                    <Combobox.Item
                      key={option.value}
                      value={option}
                      className={ITEM_CLASSES}
                    >
                      {option.label}
                    </Combobox.Item>
                  )}
                </Combobox.List>
              </Combobox.Popup>
            </Combobox.Positioner>
          </Combobox.Portal>
        </Combobox.Root>
      )}
    </Field>
  );
}
