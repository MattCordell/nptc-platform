import type { ComponentPropsWithoutRef, FormEvent, ReactNode } from "react";

import { Button } from "./button.tsx";
import { Field } from "./field.tsx";
import { INPUT_CLASSES } from "./input-classes.ts";

type SearchInputProps = {
  /** The visible label. Required: a search box without one is the defect
   * `Field` exists to prevent. */
  label: string;
  /** Put on the input itself, as `Field` does. */
  id?: string;
  hint?: ReactNode;
  value: string;
  onValueChange: (value: string) => void;
  /** Called with the value trimmed, from the button or from Enter. */
  onSubmit: (value: string) => void;
  /** Defaults to "Search". */
  submitLabel?: string;
} & Omit<ComponentPropsWithoutRef<"form">, "onSubmit" | "children" | "id">;

/**
 * A labelled search field with a submit button, in a `role="search"` form so
 * it is a landmark (NFR-31). Controlled, so a screen that mirrors the query
 * into the URL keeps its own draft and resync logic.
 */
export function SearchInput({
  label,
  id,
  hint,
  value,
  onValueChange,
  onSubmit,
  submitLabel = "Search",
  className,
  ...rest
}: SearchInputProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(value.trim());
  }

  return (
    <form
      {...rest}
      role="search"
      onSubmit={handleSubmit}
      className={["flex items-end gap-3", className ?? ""].filter(Boolean).join(" ")}
    >
      <Field id={id} label={label} hint={hint} className="flex-1">
        {(controlProps) => (
          <input
            {...controlProps}
            type="search"
            autoComplete="off"
            value={value}
            onChange={(event) => onValueChange(event.target.value)}
            className={INPUT_CLASSES}
          />
        )}
      </Field>
      <Button type="submit" className="min-h-10">
        {submitLabel}
      </Button>
    </form>
  );
}
