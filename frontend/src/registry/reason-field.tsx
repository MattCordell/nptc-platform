import { Field } from "../components/field.tsx";

/**
 * The reason every registry write requires. It goes to the audit log only, so
 * unlike an entry's changelog note it is never published as History text and
 * needs no wording rules beyond being present.
 */
export function ReasonField({
  id,
  value,
  onChange,
  error,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  error: string | undefined;
}) {
  return (
    <Field
      id={id}
      label="Reason"
      hint="Recorded in the audit log with this change. It is not published."
      error={error}
    >
      {(controlProps) => (
        <input
          {...controlProps}
          type="text"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </Field>
  );
}
