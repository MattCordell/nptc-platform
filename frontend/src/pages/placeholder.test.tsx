import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoA11yViolations } from "../test/a11y.ts";
import { renderRoute } from "../test/render-route.tsx";

function main() {
  return within(screen.getByRole("main"));
}

describe("placeholder screens", () => {
  it("shows the title, a text 'Planned' status and the issue where one is known", async () => {
    const { container } = await renderRoute("/releases");

    expect(
      await screen.findByRole("heading", { level: 1, name: "Releases" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    // Status carries a text label, never colour alone (NFR-31).
    expect(main().getByText("Planned")).toBeVisible();
    expect(main().getByText("This screen has not been built yet.")).toBeVisible();
    expect(main().getByText("Planned with issue #141.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("serves the privacy policy path Keycloak's registration page links to, with no axe violations", async () => {
    const { container } = await renderRoute("/privacy");

    expect(
      await screen.findByRole("heading", { level: 1, name: "Privacy policy" }),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(main().getByText("Planned with issue #64.")).toBeVisible();
    await expectNoA11yViolations(container);
  });

  it("omits the issue line when the route names no issue", async () => {
    await renderRoute("/exports");

    await screen.findByRole("heading", { level: 1, name: "Exports" });
    expect(main().getByText("Planned")).toBeVisible();
    expect(main().queryByText(/Planned with issue/)).not.toBeInTheDocument();
  });

  it("links back to the landing page", async () => {
    await renderRoute("/exports");

    await screen.findByRole("heading", { level: 1, name: "Exports" });
    expect(
      main().getByRole("link", { name: "Back to the landing page" }),
    ).toHaveAttribute("href", "/");
  });

  it("offers no nearest-screen link on a public stub", async () => {
    await renderRoute("/exports");

    await screen.findByRole("heading", { level: 1, name: "Exports" });
    expect(main().getAllByRole("link")).toHaveLength(1);
  });

  it("offers the built catalogue administration screen from an admin stub", async () => {
    await renderRoute("/admin/users", { auth: { status: "signed-in" } });

    await screen.findByRole("heading", { level: 1, name: "User administration" });
    expect(
      main().getByRole("link", { name: "Catalogue administration" }),
    ).toHaveAttribute("href", "/admin/catalogue");
  });

  it("reaches the landing link, then the nearest link, in reading order from the keyboard", async () => {
    const user = userEvent.setup();
    await renderRoute("/admin/users", { auth: { status: "signed-in" } });
    await screen.findByRole("heading", { level: 1, name: "User administration" });

    const landing = main().getByRole("link", { name: "Back to the landing page" });
    const nearest = main().getByRole("link", { name: "Catalogue administration" });
    landing.focus();
    await user.tab();

    expect(document.activeElement).toBe(nearest);
    expect(landing.compareDocumentPosition(nearest)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("does not wrap the screen in a second landmark", async () => {
    await renderRoute("/exports");

    await screen.findByRole("heading", { level: 1, name: "Exports" });
    expect(main().queryByRole("region")).not.toBeInTheDocument();
  });
});
