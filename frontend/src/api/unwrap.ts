/**
 * A failed API response, carrying the status code, parsed error body, and
 * response headers.
 *
 * `headers` is what issue #184's step-up detection reads
 * `WWW-Authenticate` from (see `nptc/api/step-up.ts`) - without it here,
 * the challenge is discarded at this single throw site and every call site
 * would need its own access to the raw `Response` to react to it.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;
  readonly headers: Headers;

  constructor(status: number, body: unknown, headers: Headers = new Headers()) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.headers = headers;
  }
}

/**
 * Turns an `openapi-fetch` result into data-or-throw, gated on the response
 * status rather than the parsed `error` value (issue #147 review).
 *
 * `openapi-fetch` parses `error` from the response body, so a failed
 * response with an empty body - a 204, a `HEAD`, or a `Content-Length: 0`
 * response, all of which occur on real 401/404/500s - comes back as
 * `error: undefined` (or `error: ""` when `response.text()` is empty).
 * Branching on `if (error)` alone therefore lets an empty-bodied failure
 * fall through as a successful, `data: undefined` query result: `useQuery`
 * resolves `isSuccess` and the UI shows an empty list instead of an error.
 * Gating on `response.ok` instead catches every non-2xx regardless of body.
 */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok) {
    // openapi-fetch never populates `data` for a non-ok response, so
    // `result.error` alone is what ApiError.body can hold here.
    throw new ApiError(result.response.status, result.error, result.response.headers);
  }
  return result.data as T;
}
