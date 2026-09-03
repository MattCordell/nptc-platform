import type { ComponentType } from "react";

import { ConceptPickerControl } from "./concept-picker.tsx";
import { NumberControl } from "./number.tsx";
import { TextControl } from "./text.tsx";
import { TextareaControl } from "./textarea.tsx";
import { UriControl } from "./uri.tsx";
import type { ControlKind, ControlProps } from "./types.ts";

export type { ControlKind, ControlProps, PropertyValueSlot } from "./types.ts";
export { groupFieldId, isEmptySlotValue, slotFieldId } from "./types.ts";
export { RepeatableValues } from "./repeatable-values.tsx";

/**
 * Every `ControlKind` mapped to the component that renders it (ADR-0013 SS3,
 * FR-77, issue #151). A `Record`, not a `switch`: `ControlKind` is a closed
 * union generated from the API schema, so adding a sixth member without
 * adding a row here is a `tsc -b` error, not a runtime fallthrough - there
 * is deliberately no `default` case anywhere this type is consumed.
 *
 * `frontend/tests/adr-0013-no-datatype-switch.test.ts` is this file's own
 * guard in the other direction: nothing under `frontend/src` may branch on
 * a property's *datatype* instead of going through this table.
 */
export const CONTROLS: Record<ControlKind, ComponentType<ControlProps>> = {
  text: TextControl,
  textarea: TextareaControl,
  number: NumberControl,
  uri: UriControl,
  concept_picker: ConceptPickerControl,
};
