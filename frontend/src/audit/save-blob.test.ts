import { afterEach, describe, expect, it, vi } from "vitest";

import { saveBlob } from "./save-blob.ts";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("saveBlob", () => {
  it("clicks a temporary download link for the blob, then cleans up", () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:audit");
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const clicked: { download: string; href: string; attached: boolean }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push({
        download: this.download,
        href: this.href,
        attached: document.body.contains(this),
      });
    });
    const blob = new Blob(["{}\n"], { type: "application/x-ndjson" });

    saveBlob(blob, "audit-events.ndjson");

    expect(createObjectURL).toHaveBeenCalledWith(blob);
    expect(clicked).toEqual([
      { download: "audit-events.ndjson", href: "blob:audit", attached: true },
    ]);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:audit");
    expect(document.querySelector("a[download]")).toBeNull();
  });
});
