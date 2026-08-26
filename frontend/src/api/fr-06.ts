/**
 * Compile-time guard for FR-06: every SNOMED CT identifier must be a string
 * end-to-end, never a number. SCTIDs can exceed `Number.MAX_SAFE_INTEGER`
 * and lose leading-zero/precision semantics if coerced - this is the exact
 * defect class the platform exists to eliminate.
 *
 * These are type-only assertions, not runtime code: if a future
 * `pnpm generate:api` regenerates {@link ./schema.ts} from a backend
 * document that typed one of these fields as `number`, `AssertString<T>`
 * resolves to `never` for that field and the assignment below fails
 * `pnpm typecheck` - the field can no longer be assigned `true`.
 *
 * Extend this file whenever a new SCTID-bearing field is added to the
 * OpenAPI document (see docs/architecture/public-api.md).
 */
import type { components } from "./schema.ts";

type AssertString<T> = [T] extends [string] ? true : never;

// `Binding.code` and `Binding.replaced_by_code` (FR-08's SNOMED CT code
// bindings) - `replaced_by_code` is nullable, so `NonNullable` strips the
// `| null` before checking the underlying type is `string`.
const _bindingCodeIsString: AssertString<components["schemas"]["Binding"]["code"]> = true;
const _bindingReplacedByCodeIsString: AssertString<
  NonNullable<components["schemas"]["Binding"]["replaced_by_code"]>
> = true;

// `EntryDetail.business_key` and `EntrySummary.business_key` - the
// catalogue's own identifier, not a SNOMED CT code, but held to the same
// string-only rule since it is derived from one (FR-06).
const _entryDetailBusinessKeyIsString: AssertString<
  components["schemas"]["EntryDetail"]["business_key"]
> = true;
const _entrySummaryBusinessKeyIsString: AssertString<
  components["schemas"]["EntrySummary"]["business_key"]
> = true;

// Referenced only for their type-level effect; keeping a runtime reference
// satisfies `noUnusedLocals` without disabling the rule for this file.
export const fr06Assertions = {
  _bindingCodeIsString,
  _bindingReplacedByCodeIsString,
  _entryDetailBusinessKeyIsString,
  _entrySummaryBusinessKeyIsString,
};
