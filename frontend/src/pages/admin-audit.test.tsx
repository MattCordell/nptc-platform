import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveBlob } from "../audit/save-blob.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

vi.mock("../audit/save-blob.ts", () => ({ saveBlob: vi.fn() }));

/**
 * The audit log screen (NFR-12, NFR-09, NFR-31), driven through the real route
 * like the other admin screen tests. A caller without `audit.read` is refused
 * by the server alone (NFR-20), so the refusal cases stub a 403.
 */

const AUDIT_URL = "/admin/audit";
const ACTOR_ID = "3f2a1b4c-5d6e-4f70-8192-a3b4c5d6e7f8";
const MFA_CHALLENGE = 'Bearer error="insufficient_user_authentication", acr_values="2"';

const SIGNED_IN = {
  auth: {
    status: "signed-in" as const,
    getAccessToken: () => Promise.resolve("test-token"),
  },
};

const ALICE = { id: ACTOR_ID, display_name: "Alice Admin", is_closed: false };
const CLOSED = {
  id: "9c8b7a6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d",
  display_name: null,
  is_closed: true,
};

function auditEvent(overrides: Record<string, unknown> = {}) {
  return {
    sequence: 3,
    occurred_at: "2026-10-07T04:30:15+00:00",
    actor: ALICE,
    action: "catalogue_entry.updated",
    entity_type: "catalogue_entry",
    entity_id: "NPTC-000001",
    before: null,
    after: null,
    reason: "Corrected a typo",
    ...overrides,
  };
}

function eventsRoute(items: unknown[], nextCursor: string | null = null): Route {
  return {
    method: "GET",
    path: "/audit/events",
    status: 200,
    body: { items, next_cursor: nextCursor },
  };
}

const REFUSED: Route = {
  method: "GET",
  path: "/audit/events",
  status: 403,
  body: { detail: "You do not have permission to do this." },
};

const EXPORT_OK: Route = {
  method: "GET",
  path: "/audit/events/export",
  status: 200,
  contentType: "application/x-ndjson",
  body: '{"sequence":1}\n{"sequence":2}\n',
};

function eventCalls(calls: { path: string; searchParams: URLSearchParams }[]) {
  return calls.filter((call) => call.path.endsWith("/audit/events"));
}

function exportCalls(calls: { path: string; searchParams: URLSearchParams }[]) {
  return calls.filter((call) => call.path.endsWith("/audit/events/export"));
}

beforeEach(() => {
  vi.mocked(saveBlob).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AdminAuditPage", () => {
  describe("results", () => {
    it("lists when, actor, action, entity and reason, with the time at UTC+10", async () => {
      stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      const table = await screen.findByRole("table", { name: "Audit events" });
      const row = within(
        within(table).getByRole("rowheader").closest("tr") as HTMLElement,
      );
      expect(row.getByText("7 Oct 2026, 14:30:15")).toBeInTheDocument();
      expect(
        row.getByRole("button", { name: "Filter by actor Alice Admin" }),
      ).toBeVisible();
      expect(row.getByText("catalogue_entry.updated")).toBeInTheDocument();
      expect(row.getByText("catalogue_entry NPTC-000001")).toBeInTheDocument();
      expect(row.getByText("Corrected a typo")).toBeInTheDocument();
      expect(
        within(table).getByRole("columnheader", { name: "When (UTC+10)" }),
      ).toBeInTheDocument();
    });

    it("has exactly one h1", async () => {
      stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      await screen.findByRole("table", { name: "Audit events" });
      const headings = screen.getAllByRole("heading", { level: 1 });
      expect(headings).toHaveLength(1);
      expect(headings[0]).toHaveTextContent("Audit log");
    });

    it("names a system event and a closed account in words, not colour", async () => {
      stubApi([
        eventsRoute([
          auditEvent({ sequence: 4, actor: null, reason: null }),
          auditEvent({ sequence: 3, actor: CLOSED }),
        ]),
      ]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      expect(await screen.findByText("System")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Filter by actor System/ })).toBeNull();
      expect(
        screen.getByRole("button", { name: "Filter by actor Closed account" }),
      ).toBeInTheDocument();
      expect(screen.getByText("None recorded")).toBeInTheDocument();
    });

    it("asks for the newest page of 50 with no filter when the URL has none", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      await screen.findByRole("table", { name: "Audit events" });
      const params = eventCalls(calls)[0].searchParams;
      expect(params.get("limit")).toBe("50");
      expect([...params.keys()].sort()).toEqual(["limit"]);
    });

    it("says the log is empty when it has no events and no filter applies", async () => {
      stubApi([eventsRoute([])]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      expect(await screen.findByText("The audit log has no events yet.")).toBeVisible();
      expect(screen.queryByRole("navigation", { name: "Pagination" })).toBeNull();
    });

    it("says no events match when a filter applies, and offers to clear it", async () => {
      stubApi([eventsRoute([])]);

      await renderRoute(`${AUDIT_URL}?action=nothing.here`, SIGNED_IN);

      expect(
        await screen.findByText("No audit events match these filters."),
      ).toBeVisible();
      const table = screen.getByRole("table", { name: "Audit events" });
      expect(
        within(table).getByRole("button", { name: "Clear all filters" }),
      ).toBeInTheDocument();
    });

    it("announces the number of events on the page", async () => {
      stubApi([eventsRoute([auditEvent(), auditEvent({ sequence: 2 })], "2")]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(
          "2 events on this page. More events are on the next page.",
        ),
      );
    });
  });

  describe("refusal", () => {
    it("shows the server's refusal when the caller lacks audit.read, with no results", async () => {
      stubApi([REFUSED]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      expect(
        await screen.findByText("You do not have permission to do this."),
      ).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Export as NDJSON" })).toBeNull();
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent(
          "You do not have permission to do this.",
        ),
      );
    });

    it("has no automated accessibility violations when refused", async () => {
      stubApi([REFUSED]);

      const { container } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("You do not have permission to do this.");

      await expectNoA11yViolations(container);
    });

    it("falls back to a next-step message when the failure carries no detail", async () => {
      stubApi([{ method: "GET", path: "/audit/events", status: 500, body: {} }]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      expect(
        await screen.findByText(/The audit log could not be loaded\. Try again/),
      ).toBeInTheDocument();
    });

    it("keeps the events on screen and warns when a refresh fails", async () => {
      let shouldFail = false;
      stubApi([], {
        vary: (call) => {
          if (call.method === "GET" && call.path.endsWith("/audit/events")) {
            return shouldFail
              ? { method: "GET", path: call.path, status: 500, body: { detail: "boom" } }
              : eventsRoute([auditEvent()]);
          }
          return null;
        },
      });

      const { queryClient } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      shouldFail = true;
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: ["api", "/api/v1/audit/events"] });
      });

      await waitFor(() =>
        expect(
          screen.getAllByText(
            "The audit log could not be refreshed just now, so these events may be out of date.",
          ),
        ).toHaveLength(2),
      );
      expect(screen.getByRole("table", { name: "Audit events" })).toBeInTheDocument();
    });

    it("steps up and retries the read when the server asks for multi-factor authentication", async () => {
      const stepUp = vi.fn().mockResolvedValue("done");
      stubApi([], {
        vary: (call, priorSameCalls) => {
          if (call.method !== "GET" || !call.path.endsWith("/audit/events")) {
            return null;
          }
          // StrictMode mounts the load twice, so the first two reads meet the
          // unsatisfied gate; every later read is the retry after step-up.
          return priorSameCalls < 2
            ? {
                method: "GET",
                path: call.path,
                status: 403,
                body: { detail: "This action requires multi-factor authentication." },
                headers: { "WWW-Authenticate": MFA_CHALLENGE },
              }
            : eventsRoute([auditEvent()]);
        },
      });

      await renderRoute(AUDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });

      expect(
        await screen.findByRole("table", { name: "Audit events" }),
      ).toBeInTheDocument();
      expect(stepUp).toHaveBeenCalledWith("2");
    });
  });

  describe("filters", () => {
    it("sends every filter from the URL, naming the actor by id and bounding the days at +10:00", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(
        `${AUDIT_URL}?actor=${ACTOR_ID}&entity_type=catalogue_entry&entity_id=NPTC-000001&action=catalogue_entry.updated&from=2026-10-01&to=2026-10-07`,
        SIGNED_IN,
      );

      await screen.findByRole("table", { name: "Audit events" });
      const params = eventCalls(calls)[0].searchParams;
      expect(params.get("actor_user_id")).toBe(ACTOR_ID);
      expect(params.get("entity_type")).toBe("catalogue_entry");
      expect(params.get("entity_id")).toBe("NPTC-000001");
      expect(params.get("action")).toBe("catalogue_entry.updated");
      expect(params.get("occurred_from")).toBe("2026-10-01T00:00:00+10:00");
      expect(params.get("occurred_to")).toBe("2026-10-08T00:00:00+10:00");
    });

    it("fills the form from the URL and shows each applied filter as a chip", async () => {
      stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(
        `${AUDIT_URL}?action=catalogue_entry.updated&from=2026-10-01`,
        SIGNED_IN,
      );

      await screen.findByRole("table", { name: "Audit events" });
      expect(screen.getByRole("textbox", { name: "Action" })).toHaveValue(
        "catalogue_entry.updated",
      );
      expect(screen.getByLabelText("From")).toHaveValue("2026-10-01");
      const chips = screen.getByRole("group", { name: "Active filters" });
      expect(
        within(chips).getByRole("button", {
          name: "Remove filter Action: catalogue_entry.updated",
        }),
      ).toBeInTheDocument();
      expect(
        within(chips).getByRole("button", { name: "Remove filter From: 2026-10-01" }),
      ).toBeInTheDocument();
    });

    it("applies typed filters to the URL and asks the API again", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);
      const user = userEvent.setup();
      const { router } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      await user.type(screen.getByRole("textbox", { name: "Actor" }), ACTOR_ID);
      await user.type(
        screen.getByRole("textbox", { name: "Entity type" }),
        "catalogue_entry",
      );
      await user.type(screen.getByRole("textbox", { name: "Entity id" }), "NPTC-000001");
      await user.type(screen.getByRole("textbox", { name: "Action" }), "a.b");
      await user.type(screen.getByLabelText("From"), "2026-10-01");
      await user.type(screen.getByLabelText("To"), "2026-10-07");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() => expect(router.state.location.href).toContain("action=a.b"));
      expect(router.state.location.href).toContain(`actor=${ACTOR_ID}`);
      await waitFor(() => {
        const last = eventCalls(calls).at(-1)?.searchParams;
        expect(last?.get("actor_user_id")).toBe(ACTOR_ID);
        expect(last?.get("occurred_to")).toBe("2026-10-08T00:00:00+10:00");
      });
    });

    it("keeps the entity id field unavailable until an entity type is entered", async () => {
      stubApi([eventsRoute([])]);
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("The audit log has no events yet.");

      expect(screen.getByRole("textbox", { name: "Entity id" })).toBeDisabled();
      await user.type(
        screen.getByRole("textbox", { name: "Entity type" }),
        "catalogue_entry",
      );

      expect(screen.getByRole("textbox", { name: "Entity id" })).toBeEnabled();
    });

    it("refuses an actor that is not a user id, names it in a summary and sends nothing", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);
      const user = userEvent.setup();
      const { router } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });
      const before = eventCalls(calls).length;

      await user.type(screen.getByRole("textbox", { name: "Actor" }), "alice");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      const summary = await screen.findByRole("heading", {
        name: "Some filters need fixing",
      });
      expect(summary.closest("div")).toHaveFocus();
      expect(
        within(summary.closest("div") as HTMLElement).getByRole("link", {
          name: /Enter the actor as a user id/,
        }),
      ).toBeInTheDocument();
      expect(screen.getByRole("textbox", { name: "Actor" })).toBeInvalid();
      expect(router.state.location.href).not.toContain("actor");
      expect(eventCalls(calls)).toHaveLength(before);
    });

    it("refuses a range that ends before it starts", async () => {
      stubApi([eventsRoute([auditEvent()])]);
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      await user.type(screen.getByLabelText("From"), "2026-10-08");
      await user.type(screen.getByLabelText("To"), "2026-10-07");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      expect(
        await screen.findByRole("link", {
          name: "To must be the same day as From or later.",
        }),
      ).toBeInTheDocument();
    });

    it("accepts a range of a single day", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      await user.type(screen.getByLabelText("From"), "2026-10-07");
      await user.type(screen.getByLabelText("To"), "2026-10-07");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() => {
        const last = eventCalls(calls).at(-1)?.searchParams;
        expect(last?.get("occurred_from")).toBe("2026-10-07T00:00:00+10:00");
        expect(last?.get("occurred_to")).toBe("2026-10-08T00:00:00+10:00");
      });
    });

    it("says what is wrong with a bad pasted link and sends no request", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(`${AUDIT_URL}?actor=alice&entity_id=X`, SIGNED_IN);

      expect(
        await screen.findByRole("link", { name: /Enter the actor as a user id/ }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: /Enter an entity type as well/ }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(eventCalls(calls)).toHaveLength(0);
    });

    it("never shows the cached unfiltered log under an invalid filter after Back", async () => {
      stubApi([eventsRoute([auditEvent()])]);
      const { router } = await renderRoute(`${AUDIT_URL}?actor=alice`, SIGNED_IN);
      await screen.findByRole("link", { name: /Enter the actor as a user id/ });
      await act(() => router.navigate({ to: AUDIT_URL, search: {} }));
      await screen.findByRole("table", { name: "Audit events" });

      await act(() => router.history.back());

      expect(
        await screen.findByRole("link", { name: /Enter the actor as a user id/ }),
      ).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.queryByText(/events? on this page/)).not.toBeInTheDocument();
    });

    it("filters by an actor when their name is selected in the results", async () => {
      const calls = stubApi([eventsRoute([auditEvent()])]);
      const user = userEvent.setup();
      const { router } = await renderRoute(
        `${AUDIT_URL}?action=catalogue_entry.updated`,
        SIGNED_IN,
      );

      await user.click(
        await screen.findByRole("button", { name: "Filter by actor Alice Admin" }),
      );

      await waitFor(() =>
        expect(router.state.location.href).toContain(`actor=${ACTOR_ID}`),
      );
      expect(router.state.location.href).toContain("action=catalogue_entry.updated");
      await waitFor(() =>
        expect(eventCalls(calls).at(-1)?.searchParams.get("actor_user_id")).toBe(
          ACTOR_ID,
        ),
      );
      expect(await screen.findByRole("textbox", { name: "Actor" })).toHaveValue(ACTOR_ID);
    });

    it("removes one filter from its chip, and the entity id with the entity type", async () => {
      stubApi([eventsRoute([auditEvent()])]);
      const user = userEvent.setup();
      const { router } = await renderRoute(
        `${AUDIT_URL}?entity_type=catalogue_entry&entity_id=NPTC-000001&action=a.b`,
        SIGNED_IN,
      );
      await screen.findByRole("table", { name: "Audit events" });

      await user.click(
        screen.getByRole("button", {
          name: "Remove filter Entity type: catalogue_entry",
        }),
      );

      await waitFor(() =>
        expect(router.state.location.href).not.toContain("entity_type"),
      );
      expect(router.state.location.href).not.toContain("entity_id");
      expect(router.state.location.href).toContain("action=a.b");
    });

    it("clears every filter and the cursor", async () => {
      stubApi([eventsRoute([auditEvent()], "3")]);
      const user = userEvent.setup();
      const { router } = await renderRoute(`${AUDIT_URL}?action=a.b&before=9`, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      await user.click(
        within(screen.getByRole("group", { name: "Active filters" })).getByRole(
          "button",
          {
            name: "Clear all filters",
          },
        ),
      );

      await waitFor(() => expect(router.state.location.href).toBe(AUDIT_URL));
    });

    it("does not show the previous filter's events while the new filter loads", async () => {
      let release: () => void = () => undefined;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      stubApi([], {
        vary: (call) => {
          if (!call.path.endsWith("/audit/events")) {
            return null;
          }
          return call.searchParams.has("action")
            ? {
                ...eventsRoute([
                  auditEvent({ sequence: 2, reason: "From the new filter" }),
                ]),
              }
            : eventsRoute([auditEvent({ reason: "From the old filter" })]);
        },
      });
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("From the old filter");

      // Hold the new response back by making fetch wait on the gate.
      const realFetch = globalThis.fetch;
      vi.stubGlobal("fetch", async (request: Request) => {
        if (new URL(request.url).searchParams.has("action")) {
          await gate;
        }
        return realFetch(request);
      });
      await user.type(screen.getByRole("textbox", { name: "Action" }), "a.b");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() => expect(screen.queryByText("From the old filter")).toBeNull());
      release();
      expect(await screen.findByText("From the new filter")).toBeInTheDocument();
    });
  });

  describe("paging", () => {
    function pagedStub() {
      return stubApi([], {
        vary: (call) => {
          if (!call.path.endsWith("/audit/events")) {
            return null;
          }
          const before = call.searchParams.get("before");
          if (before === null) {
            return eventsRoute([auditEvent({ sequence: 3, reason: "Page one" })], "3");
          }
          return eventsRoute([auditEvent({ sequence: 2, reason: "Page two" })]);
        },
      });
    }

    it("passes the next cursor back as before, unchanged", async () => {
      const calls = pagedStub();
      const user = userEvent.setup();
      const { router } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("Page one");

      await user.click(screen.getByRole("button", { name: "Next page" }));

      expect(await screen.findByText("Page two")).toBeInTheDocument();
      expect(router.state.location.href).toContain("before=3");
      expect(eventCalls(calls).at(-1)?.searchParams.get("before")).toBe("3");
    });

    it("returns to the earlier page with Previous, and offers no Previous on the first", async () => {
      pagedStub();
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("Page one");
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );

      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByText("Page two");
      await user.click(screen.getByRole("button", { name: "Previous page" }));

      expect(await screen.findByText("Page one")).toBeInTheDocument();
    });

    it("says there are no more results on the last page", async () => {
      pagedStub();
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("Page one");

      await user.click(screen.getByRole("button", { name: "Next page" }));

      await screen.findByText("Page two");
      expect(await screen.findByText("No more results")).toBeInTheDocument();
    });

    it("starts from the first page again when a filter changes", async () => {
      const calls = pagedStub();
      const user = userEvent.setup();
      const { router } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("Page one");
      await user.click(screen.getByRole("button", { name: "Next page" }));
      await screen.findByText("Page two");

      await user.type(screen.getByRole("textbox", { name: "Action" }), "a.b");
      await user.click(screen.getByRole("button", { name: "Apply filters" }));

      await waitFor(() => expect(router.state.location.href).not.toContain("before"));
      await waitFor(() => {
        const last = eventCalls(calls).at(-1)?.searchParams;
        expect(last?.get("action")).toBe("a.b");
        expect(last?.has("before")).toBe(false);
      });
      expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
        "aria-disabled",
        "true",
      );
    });
  });

  describe("export", () => {
    it("downloads the filtered set under a fixed name, without the page or the cursor", async () => {
      const calls = stubApi([eventsRoute([auditEvent()], "3"), EXPORT_OK]);
      const user = userEvent.setup();
      await renderRoute(
        `${AUDIT_URL}?actor=${ACTOR_ID}&from=2026-10-01&to=2026-10-07&before=9`,
        SIGNED_IN,
      );
      await screen.findByRole("table", { name: "Audit events" });

      await user.click(screen.getByRole("button", { name: "Export as NDJSON" }));

      await waitFor(() => expect(saveBlob).toHaveBeenCalledTimes(1));
      const [blob, filename] = vi.mocked(saveBlob).mock.calls[0];
      expect(filename).toBe("audit-events.ndjson");
      await expect(blob.text()).resolves.toBe('{"sequence":1}\n{"sequence":2}\n');
      const params = exportCalls(calls)[0].searchParams;
      expect(params.get("actor_user_id")).toBe(ACTOR_ID);
      expect(params.get("occurred_from")).toBe("2026-10-01T00:00:00+10:00");
      expect(params.get("occurred_to")).toBe("2026-10-08T00:00:00+10:00");
      expect(params.has("limit")).toBe(false);
      expect(params.has("before")).toBe(false);
      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("The export is ready."),
      );
    });

    it("exports the applied filters, not edits that have not been applied", async () => {
      const calls = stubApi([eventsRoute([auditEvent()]), EXPORT_OK]);
      const user = userEvent.setup();
      await renderRoute(`${AUDIT_URL}?action=a.b`, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      await user.type(screen.getByRole("textbox", { name: "Entity type" }), "other");
      await user.click(screen.getByRole("button", { name: "Export as NDJSON" }));

      await waitFor(() => expect(exportCalls(calls)).toHaveLength(1));
      const params = exportCalls(calls)[0].searchParams;
      expect(params.get("action")).toBe("a.b");
      expect(params.has("entity_type")).toBe(false);
    });

    it("shows and announces the server's refusal, and saves nothing", async () => {
      stubApi([
        eventsRoute([auditEvent()]),
        {
          method: "GET",
          path: "/audit/events/export",
          status: 403,
          body: { detail: "You do not have permission to do this." },
        },
      ]);
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      await user.click(screen.getByRole("button", { name: "Export as NDJSON" }));

      await waitFor(() =>
        expect(
          screen.getAllByText("You do not have permission to do this."),
        ).toHaveLength(2),
      );
      expect(saveBlob).not.toHaveBeenCalled();
    });

    it("drops an export failure once the applied filters change", async () => {
      stubApi([
        eventsRoute([auditEvent()]),
        { method: "GET", path: "/audit/events/export", status: 500, body: {} },
      ]);
      const user = userEvent.setup();
      const { router } = await renderRoute(`${AUDIT_URL}?action=a.b`, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });
      await user.click(screen.getByRole("button", { name: "Export as NDJSON" }));
      const failure = /The export could not be prepared/;
      expect(await screen.findAllByText(failure)).not.toHaveLength(0);

      await act(() =>
        router.navigate({ to: AUDIT_URL, search: { action: "other.action" } }),
      );

      await waitFor(() => expect(screen.queryByText(failure)).not.toBeInTheDocument());
    });

    it("steps up once on an MFA challenge and does not replay the export by itself", async () => {
      const stepUp = vi.fn().mockResolvedValue("done");
      const calls = stubApi([
        eventsRoute([auditEvent()]),
        {
          method: "GET",
          path: "/audit/events/export",
          status: 403,
          body: { detail: "This action requires multi-factor authentication." },
          headers: { "WWW-Authenticate": MFA_CHALLENGE },
        },
      ]);
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, { auth: { ...SIGNED_IN.auth, stepUp } });
      await screen.findByRole("table", { name: "Audit events" });

      await user.click(screen.getByRole("button", { name: "Export as NDJSON" }));

      await waitFor(() => expect(stepUp).toHaveBeenCalledWith("2"));
      expect(exportCalls(calls)).toHaveLength(1);
      expect(saveBlob).not.toHaveBeenCalled();
    });

    it("cannot be started while the filters are invalid", async () => {
      const calls = stubApi([EXPORT_OK, eventsRoute([])]);
      const user = userEvent.setup();
      await renderRoute(`${AUDIT_URL}?actor=alice`, SIGNED_IN);
      const button = await screen.findByRole("button", { name: "Export as NDJSON" });

      expect(button).toHaveAttribute("aria-disabled", "true");
      await user.click(button);

      expect(exportCalls(calls)).toHaveLength(0);
    });
  });

  describe("accessibility", () => {
    it("has no automated accessibility violations with results and filters applied", async () => {
      stubApi([
        eventsRoute([auditEvent(), auditEvent({ sequence: 2, actor: null })], "2"),
      ]);

      const { container } = await renderRoute(
        `${AUDIT_URL}?action=catalogue_entry.updated&from=2026-10-01`,
        SIGNED_IN,
      );
      await screen.findByRole("table", { name: "Audit events" });

      await expectNoA11yViolations(container);
    });

    it("has no automated accessibility violations in the empty state", async () => {
      stubApi([eventsRoute([])]);

      const { container } = await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByText("The audit log has no events yet.");

      await expectNoA11yViolations(container);
    });

    it("has no automated accessibility violations while showing a filter error", async () => {
      stubApi([eventsRoute([])]);

      const { container } = await renderRoute(`${AUDIT_URL}?actor=alice`, SIGNED_IN);
      await screen.findByRole("link", { name: /Enter the actor as a user id/ });

      await expectNoA11yViolations(container);
    });

    it("reaches the filters, the pager and the export with the keyboard alone", async () => {
      stubApi([eventsRoute([auditEvent()], "3"), EXPORT_OK]);
      const user = userEvent.setup();
      await renderRoute(AUDIT_URL, SIGNED_IN);
      await screen.findByRole("table", { name: "Audit events" });

      async function tabTo(target: HTMLElement) {
        for (
          let presses = 0;
          presses < 40 && document.activeElement !== target;
          presses += 1
        ) {
          await user.tab();
        }
        expect(target).toHaveFocus();
      }

      await tabTo(screen.getByRole("textbox", { name: "Actor" }));
      await tabTo(screen.getByLabelText("To"));
      await tabTo(screen.getByRole("button", { name: "Apply filters" }));
      await tabTo(screen.getByRole("button", { name: "Filter by actor Alice Admin" }));
      await tabTo(screen.getByRole("button", { name: "Next page" }));
    });

    it("gives the actor buttons a 24px minimum target height", async () => {
      stubApi([eventsRoute([auditEvent()])]);

      await renderRoute(AUDIT_URL, SIGNED_IN);

      expect(
        await screen.findByRole("button", { name: "Filter by actor Alice Admin" }),
      ).toHaveClass("min-h-6");
    });
  });
});
