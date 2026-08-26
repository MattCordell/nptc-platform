import { describe, expect, it } from "vitest";

import { ApiError, unwrap } from "./unwrap.ts";

describe("unwrap", () => {
  it("returns data for a successful response", () => {
    const result = unwrap({
      data: { ok: true },
      error: undefined,
      response: new Response(null, { status: 200 }),
    });

    expect(result).toEqual({ ok: true });
  });

  it("throws ApiError for a failed response, even with a parsed error body", () => {
    const response = new Response(null, { status: 401 });

    expect(() =>
      unwrap({ data: undefined, error: { detail: "not authenticated" }, response }),
    ).toThrow(ApiError);
  });

  // The principal failure mode this guards against (issue #147 review): a
  // failed response with an empty body parses to `error: undefined` in
  // openapi-fetch, so branching on the parsed error alone - rather than the
  // response status - would let this fall through as a successful result.
  it("throws ApiError for a failed response with an empty body", () => {
    const response = new Response(null, { status: 500 });

    expect(() => unwrap({ data: undefined, error: undefined, response })).toThrow(
      ApiError,
    );
  });

  it("carries the status code on the thrown error", () => {
    const response = new Response(null, { status: 404 });

    try {
      unwrap({ data: undefined, error: undefined, response });
      expect.fail("expected unwrap to throw");
    } catch (thrown) {
      expect(thrown).toBeInstanceOf(ApiError);
      expect((thrown as ApiError).status).toBe(404);
    }
  });
});
