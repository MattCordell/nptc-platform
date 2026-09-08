/**
 * Builds the `filter.<key>` query parameters the admin catalogue collection
 * routes accept (issue #267, ADR-0032) - the one place in this codebase that
 * constructs that parameter name by hand.
 *
 * **Why by hand (issue #276).** The generated operation type for
 * `GET /catalogue/admin/entries`/`GET /catalogue/admin/search` (see
 * `schema.ts`) types this parameter as a single literal field named
 * `"filter.{property_key}"` - `docs/api/openapi.json`'s own placeholder
 * syntax for "a templated parameter name", which OpenAPI itself has no way
 * to express. Sending that literal string is a 422 (`{property_key}` is not
 * a filter the endpoint offers); a real filter selects an actual facet key,
 * which is not knowable when `schema.ts` is generated - it is whatever an
 * administrator has marked `filterable` today (FR-16). This module is the
 * escape hatch: it returns a plain object typed as `Record<string,
 * string[]>`, and the call site (`api/queries.ts`) merges it into the
 * request's query params with a type-only cast, the same trade
 * `catalogue_shared.filter_parameter`'s own docstring makes on the backend
 * for the identical reason.
 *
 * **Repeated, not comma-joined.** `openapi-fetch@0.17.0`'s default query
 * serializer (`createQuerySerializer`) already explodes an array value with
 * `style: "form", explode: true` - one repeated `key=value` pair per array
 * entry - which is the exact wire shape `nptc.catalogue.facets.parse_filters`
 * parses server-side (`request.query_params.multi_items()`, ADR-0032). No
 * explicit `querySerializer` override is needed in `client.ts`.
 */
import { FILTER_PARAM_PREFIX } from "../router/search-params.ts";

/**
 * `selections` is keyed by facet alone (`filterSelections`'s own output
 * shape, `router/search-params.ts`) - this function's only job is prefixing
 * each key back to the wire parameter name and dropping any facet with no
 * selected values, so an emptied-out filter does not send
 * `?filter.discipline=` and narrow the result to nothing.
 */
export function filterQueryParams(selections: Record<string, string[]>): Record<string, string[]> {
  const params: Record<string, string[]> = {};
  for (const [key, values] of Object.entries(selections)) {
    if (values.length > 0) {
      params[`${FILTER_PARAM_PREFIX}${key}`] = values;
    }
  }
  return params;
}
