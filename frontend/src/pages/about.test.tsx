import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ABOUT_SECTIONS, ABOUT_TITLE } from "../content/about.ts";
import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";

function main() {
  return within(screen.getByRole("main"));
}

describe("/about (NFR-31)", () => {
  it("has no axe violations", async () => {
    const { container } = await renderRoute("/about");
    await screen.findByRole("heading", { level: 1 });

    await expectNoA11yViolations(container);
  });

  it("keeps section ids and paragraphs unique, so owner edits cannot break labelling or keys", () => {
    const ids = ABOUT_SECTIONS.map((section) => section.id);

    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) {
      expect(id).toMatch(/^[a-z0-9-]+$/);
    }
    for (const section of ABOUT_SECTIONS) {
      expect(new Set(section.paragraphs).size).toBe(section.paragraphs.length);
    }
  });

  it("has one h1 and one h2 per content section, in content order", async () => {
    await renderRoute("/about");
    await screen.findByRole("heading", { level: 1 });

    expect(main().getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(main().getByRole("heading", { level: 1 })).toHaveTextContent(ABOUT_TITLE);
    expect(
      main()
        .getAllByRole("heading", { level: 2 })
        .map((heading) => heading.textContent),
    ).toEqual(ABOUT_SECTIONS.map((section) => section.heading));
  });

  it("exposes each section as a region named by its heading", async () => {
    await renderRoute("/about");
    await screen.findByRole("heading", { level: 1 });

    for (const section of ABOUT_SECTIONS) {
      expect(main().getByRole("region", { name: section.heading })).toBeVisible();
    }
  });

  it("renders every paragraph and link from the content file", async () => {
    await renderRoute("/about");
    await screen.findByRole("heading", { level: 1 });

    for (const section of ABOUT_SECTIONS) {
      const region = within(main().getByRole("region", { name: section.heading }));
      for (const text of section.paragraphs) {
        expect(region.getByText(text)).toBeVisible();
      }
      for (const link of section.links ?? []) {
        expect(region.getByRole("link", { name: link.label })).toHaveAttribute(
          "href",
          link.to,
        );
      }
    }
  });

  it("shows the same page to every session status", async () => {
    for (const status of [
      "signed-out",
      "signed-in",
      "restoring",
      "unavailable",
    ] as const) {
      const { unmount } = await renderRoute("/about", { auth: { status } });

      expect(
        await screen.findByRole("heading", { level: 1, name: ABOUT_TITLE }),
      ).toBeVisible();
      unmount();
    }
  });
});
