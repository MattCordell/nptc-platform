import type { ComponentType } from "react";
import { useState } from "react";

import type { components } from "../../api/schema.ts";
import { Button } from "../../components/button.tsx";
import { Field } from "../../components/field.tsx";
import type { FormError } from "../../components/error-summary.tsx";
import { groupFieldId, newSlotId, slotFieldId } from "./types.ts";
import type { ControlProps, PropertyValueSlot } from "./types.ts";

type PropertyCardinality = components["schemas"]["PropertyCardinality"];

const MULTI: Record<PropertyCardinality, boolean> = {
  "0..1": false,
  "1..1": false,
  "0..*": true,
  "1..*": true,
};

/**
 * Renders one property's whole value set as a repeatable group of controls -
 * the cardinality wrapper the plan calls for, kept outside every control
 * module so no control needs to know whether it is being asked for one
 * value or several (issue #151).
 *
 * At least one control is always shown, even for a property with no
 * recorded values yet, so there is always something to type into; `onChange`
 * only ever reports the slots an editor actually touched, via
 * `isEmptySlotValue` in the caller's own submit handling - an untouched
 * placeholder slot is not itself "a value".
 *
 * A slot's `ordinal` is its position in `slots` - matching
 * `PropertyValue.ordinal`'s own contract - so `Remove` on slot 2 does not
 * leave a gap; it re-numbers everything after it, same as the server does
 * on a whole-set replace.
 */
export function RepeatableValues({
  propertyKey,
  label,
  cardinality,
  control: Control,
  params,
  slots,
  onChange,
  errors,
}: {
  propertyKey: string;
  label: string;
  cardinality: PropertyCardinality;
  control: ComponentType<ControlProps>;
  params: Record<string, unknown>;
  slots: PropertyValueSlot[];
  onChange: (slots: PropertyValueSlot[]) => void;
  errors: FormError[];
}) {
  const multi = MULTI[cardinality];
  // Stable for the component's lifetime, not regenerated each render - see
  // `PropertyValueSlot.id`'s own doc: this placeholder is only ever shown
  // while `slots` is empty, and the first keystroke into it carries this id
  // into the caller's own state via `onChange` below.
  const [placeholderId] = useState(newSlotId);
  const rendered: PropertyValueSlot[] =
    slots.length === 0
      ? [{ id: placeholderId, value: null, justification: null }]
      : slots;
  const allowJustification = params.allowJustification === true;
  const groupError = errors.find(
    (error) => error.fieldId === groupFieldId(propertyKey),
  )?.message;

  return (
    <div id={groupFieldId(propertyKey)} tabIndex={-1} className="flex flex-col gap-3">
      {rendered.map((slot, index) => {
        const fieldId = slotFieldId(propertyKey, index);
        const slotLabel = multi ? `${label} ${index + 1}` : label;
        return (
          <div key={slot.id} className="flex items-end gap-2">
            <div className="flex-1">
              <Control
                id={fieldId}
                label={slotLabel}
                error={errors.find((error) => error.fieldId === fieldId)?.message}
                propertyKey={propertyKey}
                params={params}
                value={slot.value}
                onChange={(value) => {
                  const next = [...rendered];
                  next[index] = { ...next[index], value };
                  onChange(multi ? next : next.slice(0, 1));
                }}
              />
              {allowJustification && (
                <Field
                  id={`${fieldId}-justification`}
                  label="Justification"
                  hint="Required when this value is not in the bound value set."
                >
                  {(controlProps) => (
                    <input
                      {...controlProps}
                      type="text"
                      value={slot.justification ?? ""}
                      onChange={(event) => {
                        const next = [...rendered];
                        next[index] = {
                          ...next[index],
                          justification: event.target.value,
                        };
                        onChange(multi ? next : next.slice(0, 1));
                      }}
                    />
                  )}
                </Field>
              )}
            </div>
            {multi && (
              <Button
                type="button"
                variant="danger"
                aria-label={`Remove ${slotLabel}`}
                onClick={() => onChange(rendered.filter((_, i) => i !== index))}
              >
                Remove
              </Button>
            )}
          </div>
        );
      })}
      {groupError && <p className="text-sm text-[var(--color-danger)]">{groupError}</p>}
      {multi && (
        <Button
          type="button"
          variant="secondary"
          onClick={() =>
            onChange([...rendered, { id: newSlotId(), value: null, justification: null }])
          }
        >
          Add another value
        </Button>
      )}
    </div>
  );
}
