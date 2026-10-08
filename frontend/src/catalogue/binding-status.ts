import type { StatusTone } from "../components/status-badge.tsx";

/** An unlisted status shows as its raw text on a neutral pill, not a failure. */
const BINDING_STATUSES: Record<string, { label: string; tone: StatusTone }> = {
  active: { label: "Active", tone: "active" },
  retired: { label: "Retired", tone: "deprecated" },
};

export function bindingStatus(status: string): { label: string; tone: StatusTone } {
  return BINDING_STATUSES[status] ?? { label: status, tone: "neutral" };
}
