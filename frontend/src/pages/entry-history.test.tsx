import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { formatEventTime } from "../audit/audit-filters.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";
import type { Route } from "../test/stub-api.ts";
import { stubApi } from "../test/stub-api.ts";

/**
 * The entry's full change history (FR-19, NFR-31), driven through the real
 * route and signed out, as an anonymous visitor uses it.
 */

const KEY = "NPTC-000247";
const HISTORY_URL = `/catalogue/${KEY}/history`;
const HISTORY_PATH = `/catalogue/entries/${KEY}/history`;
const NOT_FOUND_HEADING = { level: 1, name: /couldn't find that page/i } as const;
const LOAD_FAILURE = "The change history could not be loaded. Try again in a moment.";

function historyEvent(overrides: Record<string, unknown> = {}) {
  return {
    occurred_at: "2026-09-01T12:00:00Z",
    action: "catalogue_entry.updated",
    changed_by: null,
    changed_fields: ["preferred_term", "status"],
    note: "Corrected the spelling.",
    release: null,
    ...overrides,
  };
}

function historyRoute(
  items: Record<string, unknown>[],
  nextCursor: string | null = null,
  status = 200,
): Route {
  return {
    method: "GET",
    path: HISTORY_PATH,
    status,
    body: status === 200 ? { items, next_cursor: nextCursor } : { detail: "boom" },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function historyCalls(calls: ReturnType<typeof stubApi>) {
  return calls.filter((call) => call.path.endsWith("/history"));
}

/** Two pages: the first names a cursor, the second is the last. */
function pagedStub() {
  return stubApi([], {
    vary: (call) => {
      if (!call.path.endsWith("/history")) {
        return null;
      }
      const before = call.searchParams.get("before");
      return before === null
        ? historyRoute([historyEvent({ note: "Page one" })], "cursor-7=")
        : historyRoute([historyEvent({ note: "Page two" })]);
    },
  });
}

/** Holds back every history request until `release()`. Install before rendering. */
function holdHistory() {
  const inner = globalThis.fetch;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.stubGlobal("fetch", async (request: Request) => {
    if (new URL(request.url).pathname.endsWith("/history")) {
      await gate;
    }
    return inner(request);
  });
  return { release: () => release() };
}

/**
 * The sighted-reader copy of a message, as opposed to its live-region copy
 * (`LiveRegion` is a `role="status"` element filled after mount).
 */
async function visibleNote(text: string | RegExp) {
  const matches = await screen.findAllByText(text);
  const visible = matches.find((node) => node.closest("[role=status]") === null);
  expect(visible).toBeDefined();
  return visible as HTMLElement;
}

async function expectAnnounced(text: string | RegExp) {
  await waitFor(() => {
    const spoken = screen
      .getAllByRole("status")
      .some((region) =>
        typeof text === "string"
          ? region.textContent === text
          : text.test(region.textContent ?? ""),
      );
    expect(spoken).toBe(true);
  });
}

async function renderHistory(routes: Route[] = [historyRoute([historyEvent()])]) {
  const calls = stubApi(routes);
  const view = await renderRoute(HISTORY_URL);
  return { calls, ...view };
}

describe("the history page (FR-19)", () => {
  it("shows one h1 naming the entry, and sets the document title", async () => {
    await renderHistory();

    expect(
      await screen.findByRole("heading", {
        level: 1,
        name: `Change history for ${KEY}`,
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await waitFor(() =>
      expect(document.title).toBe(`Change history for ${KEY} — NPTC Catalogue`),
    );
  });

  it("links the breadcrumb back to the home page, the catalogue and the entry", async () => {
    await renderHistory();
    await screen.findByText("Corrected the spelling.", { exact: false });

    const trail = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(trail).getByRole("link", { name: "Home" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(within(trail).getByRole("link", { name: "Catalogue" })).toHaveAttribute(
      "href",
      "/catalogue",
    );
    expect(within(trail).getByRole("link", { name: KEY })).toHaveAttribute(
      "href",
      `/catalogue/${KEY}`,
    );
    expect(within(trail).getByText("Change history")).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("asks for fifty changes, from the newest", async () => {
    const { calls } = await renderHistory();

    await screen.findByText(/Corrected the spelling/);
    const call = historyCalls(calls)[0];
    expect(call.searchParams.get("limit")).toBe("50");
    expect(call.searchParams.has("before")).toBe(false);
  });

  it("shows when, what, which fields and why, as a list in the order sent", async () => {
    await renderHistory([
      historyRoute([
        historyEvent({ occurred_at: "2026-09-02T01:30:00Z", note: "Newer" }),
        historyEvent({
          occurred_at: "2026-09-01T12:00:00Z",
          action: "code_binding.replacement_linked",
          changed_fields: ["replaced_by_binding_id", "status"],
          note: null,
        }),
      ]),
    ]);

    const list = await screen.findByRole("list", {
      name: "Changes to this entry, newest first",
    });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Catalogue entry updated")).toBeInTheDocument();
    expect(
      within(rows[0]).getByText("Fields: Requesting term, Status"),
    ).toBeInTheDocument();
    expect(within(rows[0]).getByText("Reason: Newer")).toBeInTheDocument();
    expect(
      within(rows[1]).getByText("Code binding replacement linked"),
    ).toBeInTheDocument();
    expect(within(rows[1]).getByText("Fields: Status")).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/Reason/)).toBeNull();
    expect(list.textContent).not.toMatch(/binding.?id|catalogue_entry|row.?version/i);
  });

  it("shows each time in UTC+10, in mono with tabular figures", async () => {
    await renderHistory();

    const time = await screen.findByText(formatEventTime("2026-09-01T12:00:00Z"));
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-09-01T12:00:00Z");
    expect(time.textContent).toContain("22:00:00");
    expect(time.className).toContain("font-mono");
    expect(time.className).toContain("tabular-nums");
  });

  it("names no author to an anonymous reader", async () => {
    await renderHistory();
    await screen.findByText(/Corrected the spelling/);

    expect(screen.queryByText(/^By /)).toBeNull();
  });

  it("names the author when the API sends one", async () => {
    await renderHistory([
      historyRoute([historyEvent({ changed_by: "Dr Jane Citizen" })]),
    ]);

    expect(await screen.findByText("By Dr Jane Citizen")).toBeInTheDocument();
  });

  it("announces how many changes a page holds", async () => {
    await renderHistory([
      historyRoute([historyEvent(), historyEvent({ note: "Again" })], "next"),
    ]);

    await expectAnnounced("2 changes on this page. More changes are on the next page.");
  });

  it("says so when no change is recorded, and offers no paging", async () => {
    await renderHistory([historyRoute([])]);

    expect(
      await screen.findByText("No changes are recorded for this entry."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Pagination" })).toBeNull();
    expect(
      screen.queryByRole("list", { name: "Changes to this entry, newest first" }),
    ).toBeNull();
  });

  it("shows a loading note, under the heading, while the history loads", async () => {
    stubApi([historyRoute([historyEvent()])]);
    const hold = holdHistory();
    await renderRoute(HISTORY_URL);

    expect(
      screen.getByRole("heading", { level: 1, name: `Change history for ${KEY}` }),
    ).toBeInTheDocument();
    expect(screen.getByText("Loading the change history…")).toBeInTheDocument();

    await act(async () => hold.release());
    expect(await screen.findByText(/Corrected the spelling/)).toBeInTheDocument();
    expect(screen.queryByText("Loading the change history…")).toBeNull();
  });
});

describe("paging", () => {
  it("passes the next cursor back as before, unchanged, and keeps it in the URL", async () => {
    const calls = pagedStub();
    const user = userEvent.setup();
    const { router } = await renderRoute(HISTORY_URL);
    await screen.findByText(/Page one/);

    await user.click(screen.getByRole("button", { name: "Next page" }));

    expect(await screen.findByText(/Page two/)).toBeInTheDocument();
    expect(historyCalls(calls).at(-1)?.searchParams.get("before")).toBe("cursor-7=");
    expect(decodeURIComponent(router.state.location.href)).toContain("before=cursor-7=");
  });

  it("returns to the earlier page with Previous, and offers none on the first", async () => {
    pagedStub();
    const user = userEvent.setup();
    const { router } = await renderRoute(HISTORY_URL);
    await screen.findByText(/Page one/);
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText(/Page two/);
    await user.click(screen.getByRole("button", { name: "Previous page" }));

    expect(await screen.findByText(/Page one/)).toBeInTheDocument();
    expect(router.state.location.href).not.toContain("before");
  });

  it("says there are no more changes on the last page", async () => {
    pagedStub();
    const user = userEvent.setup();
    await renderRoute(HISTORY_URL);
    await screen.findByText(/Page one/);

    await user.click(screen.getByRole("button", { name: "Next page" }));

    await screen.findByText(/Page two/);
    expect(await screen.findByText("No more results")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("opens the page a pasted link names, and can only go back from it to the first", async () => {
    const calls = pagedStub();
    await renderRoute(`${HISTORY_URL}?before=cursor-7%3D`);

    expect(await screen.findByText(/Page two/)).toBeInTheDocument();
    expect(historyCalls(calls)[0].searchParams.get("before")).toBe("cursor-7=");
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("keeps the Next button, with its focus, while the next page loads", async () => {
    pagedStub();
    const user = userEvent.setup();
    await renderRoute(HISTORY_URL);
    await screen.findByText(/Page one/);
    const next = screen.getByRole("button", { name: "Next page" });

    next.focus();
    await user.keyboard("{Enter}");

    expect(await screen.findByText(/Page two/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next page" })).toBe(next);
    expect(next).toHaveFocus();
  });

  it("shows no earlier page's changes under another entry", async () => {
    const other = "NPTC-000999";
    stubApi([], {
      vary: ({ path }) =>
        path.endsWith("/history")
          ? path.includes(other)
            ? historyRoute([historyEvent({ note: "Other entry" })])
            : historyRoute([historyEvent({ note: "This entry" })])
          : null,
    });
    const { router } = await renderRoute(HISTORY_URL);
    await screen.findByText(/This entry/);

    await act(async () => {
      await router.navigate({
        to: "/catalogue/$businessKey/history",
        params: { businessKey: other },
      });
    });

    expect(await screen.findByText(/Other entry/)).toBeInTheDocument();
    expect(screen.queryByText(/This entry/)).toBeNull();
  });
});

describe("a history that cannot be shown", () => {
  it("offers a retry when the load fails, announces it, and recovers", async () => {
    let failing = true;
    stubApi([historyRoute([historyEvent()])], {
      vary: ({ path }) =>
        failing && path.endsWith("/history") ? historyRoute([], null, 500) : null,
    });
    await renderRoute(HISTORY_URL);

    expect(await visibleNote(LOAD_FAILURE)).toBeInTheDocument();
    await expectAnnounced(LOAD_FAILURE);
    expect(screen.queryByRole("heading", NOT_FOUND_HEADING)).toBeNull();
    expect(screen.queryByText(/No changes are recorded/)).toBeNull();

    failing = false;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Corrected the spelling/)).toBeInTheDocument();
    expect(screen.queryByText(LOAD_FAILURE)).toBeNull();
  });

  it("keeps the changes on screen, with a warning, when a refresh fails", async () => {
    let failing = false;
    stubApi([historyRoute([historyEvent()])], {
      vary: ({ path }) =>
        failing && path.endsWith("/history") ? historyRoute([], null, 500) : null,
    });
    const { queryClient } = await renderRoute(HISTORY_URL);
    await screen.findByText(/Corrected the spelling/);

    failing = true;
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: ["api"] });
    });

    expect(await visibleNote(/could not be refreshed just now/)).toBeInTheDocument();
    await expectAnnounced(/could not be refreshed just now/);
    expect(screen.getByText(/Corrected the spelling/)).toBeInTheDocument();
  });

  it("shows the not-found page for a 404", async () => {
    await renderHistory([{ ...historyRoute([]), status: 404, body: { detail: "No" } }]);

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
  });

  // The 422 body is the framework's own, not a declared shape, so the page
  // must not read it: an array here would break any code that did.
  it("shows the not-found page for a 422, without reading its body", async () => {
    await renderHistory([
      {
        ...historyRoute([]),
        status: 422,
        body: [{ loc: ["query", "before"], msg: "bad", type: "x" }],
      },
    ]);

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
    expect(screen.queryByText(/bad/)).toBeNull();
  });

  it("shows the same page for a cursor the server refuses", async () => {
    stubApi([{ ...historyRoute([]), status: 422, body: {} }]);
    await renderRoute(`${HISTORY_URL}?before=not-a-cursor`);

    expect(await screen.findByRole("heading", NOT_FOUND_HEADING)).toBeInTheDocument();
  });
});

describe("accessibility (NFR-31)", () => {
  it("has no automated violations with changes listed and paging offered", async () => {
    pagedStub();
    const { container } = await renderRoute(HISTORY_URL);
    await screen.findByText(/Page one/);

    await expectNoA11yViolations(container);
  });

  it("has no violations when no change is recorded", async () => {
    const { container } = await renderHistory([historyRoute([])]);
    await screen.findByText("No changes are recorded for this entry.");

    await expectNoA11yViolations(container);
  });

  it("has no violations while the history loads", async () => {
    stubApi([historyRoute([historyEvent()])]);
    holdHistory();
    const { container } = await renderRoute(HISTORY_URL);
    await screen.findByText("Loading the change history…");

    await expectNoA11yViolations(container);
  });

  it("has no violations when the load fails", async () => {
    const { container } = await renderHistory([historyRoute([], null, 500)]);
    await visibleNote(LOAD_FAILURE);

    await expectNoA11yViolations(container);
  });

  it("keeps the shell's landmarks and adds none of its own", async () => {
    await renderHistory();
    await screen.findByText(/Corrected the spelling/);

    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByRole("banner")).toHaveLength(1);
    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
    expect(screen.queryByRole("complementary")).toBeNull();
  });

  it("reaches every control by keyboard, in reading order", async () => {
    pagedStub();
    const user = userEvent.setup();
    await renderRoute(HISTORY_URL);
    await screen.findByText(/Page one/);
    const trail = screen.getByRole("navigation", { name: "Breadcrumb" });
    const entryLink = within(trail).getByRole("link", { name: KEY });

    entryLink.focus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Next page" })).toHaveFocus();
  });
});
