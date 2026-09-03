import { Field } from "../../components/field.tsx";
import type { ControlProps } from "./types.ts";

/**
 * `ControlKind.NUMBER` - `decimal` (`params.step = "any"`) and `positiveInt`
 * (`params.step = 1`, `params.minimum = 1`) both land here: the two datatype
 * handlers differ only in the params they compute, never in which control
 * they name (ADR-0013 SS3's whole point).
 *
 * Emits a JS `number`, or `null` for an empty input - never the empty
 * string `<input type="number">` reports natively, which `isEmptySlotValue`
 * (`types.ts`) does not recognise as "nothing entered".
 */
export function NumberControl({ id, label, hint, error, value, onChange, params }: ControlProps) {
  const step =
    typeof params.step === "number" || typeof params.step === "string" ? params.step : "any";
  const min = typeof params.minimum === "number" ? params.minimum : undefined;

  return (
    <Field id={id} label={label} hint={hint} error={error}>
      {(controlProps) => (
        <input
          {...controlProps}
          type="number"
          step={step}
          min={min}
          value={typeof value === "number" ? value : ""}
          onChange={(event) => {
            const raw = event.target.value;
            onChange(raw === "" ? null : Number(raw));
          }}
        />
      )}
    </Field>
  );
}
