import { Field } from "../../components/field.tsx";
import type { ControlProps } from "./types.ts";

/** `ControlKind.TEXT` - a short string (`string` datatype, `maxLength <= 200`). */
export function TextControl({ id, label, hint, error, value, onChange }: ControlProps) {
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      {(controlProps) => (
        <input
          {...controlProps}
          type="text"
          value={typeof value === "string" ? value : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}
