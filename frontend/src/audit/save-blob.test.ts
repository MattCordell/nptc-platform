import { afterEach, describe, expect, it, vi } from "vitest";

import { saveBlob } from "./save-blob.ts";

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("saveBlob", () => {
  it("clicks a temporary download link for the blob, then cleans up", () => {
    vi.useFakeTimers();
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
    expect(document.querySelector("a[download]")).toBeNull();
    // Revoked only after the browser has had time to start the download.
    expect(revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(10_000);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:audit");
  });
});
