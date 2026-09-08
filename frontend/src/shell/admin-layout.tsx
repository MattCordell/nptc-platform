import { Outlet } from "@tanstack/react-router";

import { StepUpBanner } from "./step-up-banner.tsx";

/**
 * `adminRoute`'s own `component` (`route-tree.ts`) - previously unset, so
 * every admin screen rendered directly under `RequireAuth`'s `<Outlet />`
 * with no admin-specific chrome. This is the first thing that needs one
 * (issue #184's pre-emptive `StepUpBanner`), and a clean insertion point:
 * if a later issue (#267) gives `/admin` a fuller shell - navigation,
 * breadcrumbs - the banner moves into it rather than this file growing
 * unrelated concerns.
 */
export function AdminLayout() {
  return (
    <>
      <StepUpBanner />
      <Outlet />
    </>
  );
}
