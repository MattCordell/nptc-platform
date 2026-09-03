import type { ReactNode } from "react";

import type { components } from "../../api/schema.ts";

export type ControlKind = components["schemas"]["ControlKind"];

/**
 * What every `ControlKind` component receives (ADR-0013 SS3, issue #151).
 *
 * Each control owns its **own** `Field`/`Select` composition rather than
 * being wrapped in one by its caller - `Select` already wraps `Field`
 * internally (`select.tsx`), so a caller-provided `Field` around a control
 * that renders `Select` would nest two, producing two `<label>`s for one
 * `id`. `label`/`hint`/`error` are passed through here precisely so a
 * control can hand them straight to whichever baseline primitive it uses,
 * with exactly one `Field` in the tree.
 *
 * `params` is `FormControlDescriptor.params` verbatim - JSON-serialisable
 * only, per that type's own contract - so a control reads `params.step`,
 * `params.minimum`, `params.schemes`, `params.valueSetUri` etc. rather than
 * being handed a typed struct per kind; the union that would type it
 * precisely is exactly the per-datatype branching FR-77 forbids here.
 *
 * `propertyKey` is unused by every control except `concept_picker` (it is
 * `usePropertyValueOptions`'s own parameter) but is part of the common shape
 * so `CONTROLS` can stay one `Record<ControlKind, ComponentType<ControlProps>>`
 * rather than a per-kind prop type that would need a cast at the call site.
 */
export interface ControlProps {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string;
  propertyKey: string;
  params: Record<string, unknown>;
  value: unknown;
  onChange: (value: unknown) => void;
}

/** One recorded (or being-edited) value in a property's whole value set. */
export interface PropertyValueSlot {
  value: unknown;
  justification: string | null;
}

/**
 * A slot with nothing meaningful in it - null/undefined, or a blank string.
 * `RepeatableValues` uses this to drop untouched slots before a save rather
 * than submitting `{value: ""}` as a real value, and to decide whether a
 * `0..1`/`0..*` property's array should serialise as empty.
 */
export function isEmptySlotValue(value: unknown): boolean {
  return (
    value === null || value === undefined || (typeof value === "string" && value.trim().length === 0)
  );
}

/** The field id a `PropertyIssueItem` with this `ordinal` maps to (`properties-panel.tsx`). */
export function slotFieldId(propertyKey: string, ordinal: number): string {
  return `${propertyKey}-${ordinal}`;
}

/** The field id a cardinality-level issue (`ordinal: null`) maps to. */
export function groupFieldId(propertyKey: string): string {
  return `${propertyKey}-group`;
}
