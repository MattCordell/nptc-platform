import { Field } from "../../components/field.tsx";
import type { ControlProps } from "./types.ts";

/** `ControlKind.TEXTAREA` - a longer string (`string` datatype, `maxLength > 200`). */
export function TextareaControl({
  id,
  label,
  hint,
  error,
  value,
  onChange,
}: ControlProps) {
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      {(controlProps) => (
        <textarea
          {...controlProps}
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}
