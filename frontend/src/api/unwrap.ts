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
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown) {
    super(`API request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (!result.response.ok) {
    throw new ApiError(result.response.status, result.error ?? result.data);
  }
  return result.data as T;
}
