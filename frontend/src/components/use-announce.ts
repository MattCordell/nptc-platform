import { useCallback, useState } from "react";

export type Politeness = "polite" | "assertive";

/**
 * Pairs with `LiveRegion` (`live-region.tsx`): `announce()` sets the message
 * a mounted `LiveRegion` renders, so a screen shows an async result (a save
 * succeeding, a search completing) without leaving a screen-reader user on
 * a page that changed silently. Kept as a hook rather than baked into
 * `LiveRegion` itself so a screen can own when/what to announce while the
 * region's rendering stays fixed.
 */
export function useAnnounce(initialPoliteness: Politeness = "polite") {
  const [message, setMessage] = useState("");
  const [politeness, setPoliteness] = useState<Politeness>(initialPoliteness);

  const announce = useCallback((next: string, nextPoliteness?: Politeness) => {
    if (nextPoliteness) {
      setPoliteness(nextPoliteness);
    }
    // Force a change even if the same text is announced twice in a row:
    // some screen readers only announce on a text *change*, so an identical
    // message with no reset would be silently swallowed the second time.
    setMessage("");
    queueMicrotask(() => setMessage(next));
  }, []);

  return { message, politeness, announce };
}
