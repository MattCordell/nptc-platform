import { useEffect } from "react";

/**
 * Moves focus to the heading with `headingId` once, when the screen mounts.
 * The heading gets `tabindex="-1"` so it can take focus without joining the
 * tab order. Needed by screens that replace the requested page (not-found,
 * route error), where a keyboard or screen-reader user otherwise hears
 * nothing change.
 */
export function useFocusHeadingOnMount(headingId: string, enabled: boolean) {
  useEffect(() => {
    if (!enabled) {
      return;
    }
    const heading = document.getElementById(headingId);
    if (heading === null) {
      return;
    }
    heading.tabIndex = -1;
    heading.focus();
  }, [headingId, enabled]);
}
