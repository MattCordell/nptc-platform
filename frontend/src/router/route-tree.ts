import { createRootRoute, createRoute, stripSearchParams } from "@tanstack/react-router";

import { HomePage } from "../pages/home.tsx";
import { AboutPage } from "../pages/about.tsx";
import { AdminAuditPage } from "../pages/admin-audit.tsx";
import { AdminCatalogueEditPage } from "../pages/admin-catalogue-edit.tsx";
import { AdminCatalogueListPage } from "../pages/admin-catalogue-list.tsx";
import { AdminHomePage } from "../pages/admin-home.tsx";
import { AdminPropertyCreatePage } from "../pages/admin-property-create.tsx";
import { AdminPropertyDetailPage } from "../pages/admin-property-detail.tsx";
import { AdminPropertyEditPage } from "../pages/admin-property-edit.tsx";
import { AdminPropertyListPage } from "../pages/admin-property-list.tsx";
import { AuthCallbackPage } from "../pages/auth-callback.tsx";
import { CatalogueEntryPage } from "../pages/catalogue-entry.tsx";
import { CatalogueSearchPage } from "../pages/catalogue-search.tsx";
import { CodeByTokenPage } from "../pages/code-by-token.tsx";
import { CodeLookupPage } from "../pages/code-lookup.tsx";
import { EntryHistoryPage } from "../pages/entry-history.tsx";
import { createPlaceholderPage } from "../pages/placeholder.tsx";
import { RegisterPage } from "../pages/register.tsx";
import { SignInPage } from "../pages/sign-in.tsx";
import { SignOutPage } from "../pages/sign-out.tsx";
import { SubmissionNewPage } from "../pages/submission-new.tsx";
import { TermsPage } from "../pages/terms.tsx";
import { AdminLayout } from "../shell/admin-layout.tsx";
import { RequireAuth } from "../shell/require-auth.tsx";
import { RootLayout } from "../shell/root-layout.tsx";
import {
  validateAdminCatalogueSearch,
  validateAuditSearch,
  validateCatalogueSearch,
  validateEntryHistorySearch,
  validateLookupSearch,
  validatePropertyListSearch,
  validateReleaseCompareSearch,
  validateSignInSearch,
  type AdminCatalogueSearch,
  type AdminCatalogueSearchInput,
  type AuditSearch,
  type AuditSearchInput,
  type CatalogueSearch,
  type CatalogueSearchInput,
  type EntryHistorySearch,
  type EntryHistorySearchInput,
  type LookupSearch,
  type LookupSearchInput,
  type PropertyListSearch,
  type PropertyListSearchInput,
  type ReleaseCompareSearch,
  type ReleaseCompareSearchInput,
  type SignInSearch,
  type SignInSearchInput,
} from "./search-params.ts";

/**
 * The single source of every URL shape the platform serves. A new screen
 * adds a route here - it never invents its own path elsewhere; see
 * `docs/architecture/frontend-routing.md`.
 *
 * `head`/document-title metadata is declared per public route via
 * `head: () => ({ meta: [...] })`, rendered by `<HeadContent />` in
 * `RootLayout`.
 */

const rootRoute = createRootRoute({
  component: RootLayout,
});

function titled(title: string) {
  return () => ({ meta: [{ title: `${title} — NPTC Catalogue` }] });
}

// --- public: landing ------------------------------------------------------

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: HomePage,
  head: titled("NPTC Catalogue"),
});

// --- public: catalogue (FR-14..19, FR-35; #140) ---------------------------

const catalogueRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "catalogue",
});

// FR-14..16, FR-18. Search, filter and cursor state lives entirely in the
// URL, so a pasted link restores the same results.
const catalogueSearchRoute = createRoute({
  getParentRoute: () => catalogueRoute,
  path: "/",
  // Cast only widens the *declared input* TanStack infers for <Link
  // to="/catalogue" search={...}> (every field becomes optional); the
  // validator itself keeps a plain Record<string, unknown> parameter so it
  // stays trivially unit-testable. See the doc comment on
  // `CatalogueSearchInput` in search-params.ts.
  validateSearch: validateCatalogueSearch as (
    search: CatalogueSearchInput,
  ) => CatalogueSearch,
  // `q` is always returned, so stripping its default keeps a bare link to
  // `/catalogue` from committing as `/catalogue?q=`. A `filter.<key>` has no
  // default to strip: its presence is the selection.
  search: {
    middlewares: [stripSearchParams({ q: "" })],
  },
  component: CatalogueSearchPage,
  head: titled("Search"),
});

// FR-17: /catalogue/lookup?system={uri}&code={code}. Declared as a static
// sibling of $businessKey so it is matched before the dynamic segment - see
// route-tree.test.tsx's precedence assertion.
const catalogueLookupRoute = createRoute({
  getParentRoute: () => catalogueRoute,
  path: "lookup",
  validateSearch: validateLookupSearch as (search: LookupSearchInput) => LookupSearch,
  component: CodeLookupPage,
  head: titled("Code lookup"),
});

// FR-17: /catalogue/code/{system_token}/{code}. `sct` aliases
// http://snomed.info/sct. Both params stay plain strings - no `params.parse`
// coercion - because a code is a string end to end (FR-06).
const catalogueCodeLookupRoute = createRoute({
  getParentRoute: () => catalogueRoute,
  path: "code/$systemToken/$code",
  component: CodeByTokenPage,
  head: titled("Code lookup"),
});

// FR-17: /catalogue/{business_key}, e.g. /catalogue/NPTC-000247.
// `business_key` is the public identifier (FR-03); the internal UUID never
// appears in a route (PRD SS6.2).
const catalogueEntryRoute = createRoute({
  getParentRoute: () => catalogueRoute,
  path: "$businessKey",
});

const catalogueEntryDetailRoute = createRoute({
  getParentRoute: () => catalogueEntryRoute,
  path: "/",
  component: CatalogueEntryPage,
  head: titled("Catalogue entry"),
});

// FR-19, FR-35: full change history, including linked amendment submissions.
const catalogueEntryHistoryRoute = createRoute({
  getParentRoute: () => catalogueEntryRoute,
  path: "history",
  component: EntryHistoryPage,
  validateSearch: validateEntryHistorySearch as (
    search: EntryHistorySearchInput,
  ) => EntryHistorySearch,
  head: titled("Change history"),
});

// --- public: releases (FR-56..61) -----------------------------------------

const releasesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "releases",
});

const releaseListRoute = createRoute({
  getParentRoute: () => releasesRoute,
  path: "/",
  component: createPlaceholderPage({ title: "Releases", issue: 141 }),
  head: titled("Releases"),
});

// FR-60: diff view between two releases.
const releaseCompareRoute = createRoute({
  getParentRoute: () => releasesRoute,
  path: "compare",
  validateSearch: validateReleaseCompareSearch as (
    search: ReleaseCompareSearchInput,
  ) => ReleaseCompareSearch,
  component: createPlaceholderPage({ title: "Compare releases", issue: 141 }),
  head: titled("Compare releases"),
});

const releaseDetailRoute = createRoute({
  getParentRoute: () => releasesRoute,
  path: "$releaseId",
  component: createPlaceholderPage({ title: "Release", issue: 141 }),
  head: titled("Release"),
});

// --- public: other (FR-62..69, FR-78, NFR-45) ------------------------------

const exportsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "exports",
  component: createPlaceholderPage({ title: "Exports" }),
  head: titled("Exports"),
});

const aboutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "about",
  component: AboutPage,
  head: titled("About"),
});

const termsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "terms",
  component: TermsPage,
  head: titled("Terms of use"),
});

// The registration page on Keycloak links here (ADR-0043, NFR-14), so the
// path must resolve before the policy text exists.
const privacyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "privacy",
  component: createPlaceholderPage({ title: "Privacy policy", issue: 64 }),
  head: titled("Privacy policy"),
});

// --- public: auth entry points (#41) ---------------------------------------

const signInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "sign-in",
  validateSearch: validateSignInSearch as (search: SignInSearchInput) => SignInSearch,
  component: SignInPage,
  head: titled("Sign in"),
});

const signOutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "sign-out",
  component: SignOutPage,
  head: titled("Sign out"),
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "register",
  component: RegisterPage,
  head: titled("Register"),
});

const authCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "auth/callback",
  component: AuthCallbackPage,
});

// --- authenticated (structural; RequireAuth; NFR-20) -----------------------
//
// A pathless layout route (no `path`, just an `id`): it contributes no URL
// segment of its own. #41 replaces `RequireAuth`'s body with the real OIDC
// session and a `beforeLoad` redirect; the children below do not move.

const authenticatedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "authenticated",
  component: RequireAuth,
});

// FR-23..31: submission form, list, workflow detail.
const submissionsRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "submissions",
});

const submissionListRoute = createRoute({
  getParentRoute: () => submissionsRoute,
  path: "/",
  component: createPlaceholderPage({ title: "Submissions" }),
  head: titled("Submissions"),
});

const submissionNewRoute = createRoute({
  getParentRoute: () => submissionsRoute,
  path: "new",
  component: SubmissionNewPage,
  head: titled("Submit a new test"),
});

const submissionDetailRoute = createRoute({
  getParentRoute: () => submissionsRoute,
  path: "$submissionId",
  component: createPlaceholderPage({ title: "Submission" }),
  head: titled("Submission"),
});

// FR-32..34: register implementer interest.
const interestRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "interest",
  component: createPlaceholderPage({ title: "My interest" }),
  head: titled("My interest"),
});

const accountRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "account",
  component: createPlaceholderPage({ title: "Account" }),
  head: titled("Account"),
});

// --- authenticated: admin ----------------------------------------------
//
// Deliberately no `/admin/submissions`: the reviewer queue is `/submissions`
// above, and what a given user sees there is decided server-side (NFR-20).

// The built admin screen offered from every admin stub as a way on. The admin
// home links to every admin route, built or planned.
const ADMIN_NEAREST = {
  to: "/admin/catalogue",
  label: "Catalogue administration",
} as const;

const adminRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "admin",
  component: AdminLayout,
});

const adminHomeRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/",
  component: AdminHomePage,
  head: titled("Administration"),
});

// FR-36..39: catalogue entry, designation, code binding, changelog-note edit.
const adminCatalogueRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "catalogue",
});

// FR-14..16, FR-36, NFR-31 (issue #267). Filter and paging state lives
// entirely in the URL, matching `catalogueSearchRoute`'s own reasoning - a
// pasted link restores the same filtered view.
const adminCatalogueListRoute = createRoute({
  getParentRoute: () => adminCatalogueRoute,
  path: "/",
  validateSearch: validateAdminCatalogueSearch as (
    search: AdminCatalogueSearchInput,
  ) => AdminCatalogueSearch,
  // Only `q`'s default is stripped, for `catalogueSearchRoute`'s reason.
  search: {
    middlewares: [stripSearchParams({ q: "" })],
  },
  component: AdminCatalogueListPage,
  head: titled("Catalogue administration"),
});

const adminCatalogueNewRoute = createRoute({
  getParentRoute: () => adminCatalogueRoute,
  path: "new",
  component: createPlaceholderPage({
    title: "New catalogue entry",
    nearest: ADMIN_NEAREST,
  }),
  head: titled("New catalogue entry"),
});

const adminCatalogueEditRoute = createRoute({
  getParentRoute: () => adminCatalogueRoute,
  path: "$businessKey/edit",
  component: AdminCatalogueEditPage,
  head: titled("Edit catalogue entry"),
});

// FR-08..13: property registry administration.
const adminPropertiesRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "properties",
});

const adminPropertyListRoute = createRoute({
  getParentRoute: () => adminPropertiesRoute,
  path: "/",
  validateSearch: validatePropertyListSearch as (
    search: PropertyListSearchInput,
  ) => PropertyListSearch,
  component: AdminPropertyListPage,
  head: titled("Property registry"),
});

const adminPropertyNewRoute = createRoute({
  getParentRoute: () => adminPropertiesRoute,
  path: "new",
  component: AdminPropertyCreatePage,
  head: titled("New property"),
});

const adminPropertyDetailRoute = createRoute({
  getParentRoute: () => adminPropertiesRoute,
  path: "$propertyKey",
  component: AdminPropertyDetailPage,
  head: titled("Property"),
});

const adminPropertyEditRoute = createRoute({
  getParentRoute: () => adminPropertiesRoute,
  path: "$propertyKey/edit",
  component: AdminPropertyEditPage,
  head: titled("Edit property"),
});

// FR-40..43: user administration.
const adminUsersRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "users",
});

const adminUserListRoute = createRoute({
  getParentRoute: () => adminUsersRoute,
  path: "/",
  component: createPlaceholderPage({
    title: "User administration",
    nearest: ADMIN_NEAREST,
  }),
  head: titled("User administration"),
});

const adminUserDetailRoute = createRoute({
  getParentRoute: () => adminUsersRoute,
  path: "$userId",
  component: createPlaceholderPage({
    title: "User",
    nearest: ADMIN_NEAREST,
  }),
  head: titled("User"),
});

// FR-45..55: validation findings.
const adminValidationRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "validation",
});

const adminFindingListRoute = createRoute({
  getParentRoute: () => adminValidationRoute,
  path: "/",
  component: createPlaceholderPage({
    title: "Validation findings",
    issue: 141,
    nearest: ADMIN_NEAREST,
  }),
  head: titled("Validation findings"),
});

const adminFindingDetailRoute = createRoute({
  getParentRoute: () => adminValidationRoute,
  path: "$findingId",
  component: createPlaceholderPage({
    title: "Validation finding",
    issue: 141,
    nearest: ADMIN_NEAREST,
  }),
  head: titled("Validation finding"),
});

// FR-56..61: cut and publish releases (admin side).
const adminReleasesRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "releases",
});

const adminReleaseListRoute = createRoute({
  getParentRoute: () => adminReleasesRoute,
  path: "/",
  component: createPlaceholderPage({
    title: "Release administration",
    issue: 141,
    nearest: ADMIN_NEAREST,
  }),
  head: titled("Release administration"),
});

const adminReleaseNewRoute = createRoute({
  getParentRoute: () => adminReleasesRoute,
  path: "new",
  component: createPlaceholderPage({
    title: "Cut a release",
    issue: 141,
    nearest: ADMIN_NEAREST,
  }),
  head: titled("Cut a release"),
});

// FR-62..69, FR-78: export configuration.
const adminExportConfigRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "exports/config",
  component: createPlaceholderPage({
    title: "Export configuration",
    nearest: ADMIN_NEAREST,
  }),
  head: titled("Export configuration"),
});

// NFR-08..13: audit log viewer, Admin only. Filters and the page cursor live in
// the URL; the server refuses a caller without `audit.read`.
const adminAuditRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "audit",
  validateSearch: validateAuditSearch as (search: AuditSearchInput) => AuditSearch,
  component: AdminAuditPage,
  head: titled("Audit log"),
});

export const routeTree = rootRoute.addChildren([
  homeRoute,
  catalogueRoute.addChildren([
    catalogueSearchRoute,
    catalogueLookupRoute,
    catalogueCodeLookupRoute,
    catalogueEntryRoute.addChildren([
      catalogueEntryDetailRoute,
      catalogueEntryHistoryRoute,
    ]),
  ]),
  releasesRoute.addChildren([releaseListRoute, releaseCompareRoute, releaseDetailRoute]),
  exportsRoute,
  aboutRoute,
  termsRoute,
  privacyRoute,
  signInRoute,
  signOutRoute,
  registerRoute,
  authCallbackRoute,
  authenticatedRoute.addChildren([
    submissionsRoute.addChildren([
      submissionListRoute,
      submissionNewRoute,
      submissionDetailRoute,
    ]),
    interestRoute,
    accountRoute,
    adminRoute.addChildren([
      adminHomeRoute,
      adminCatalogueRoute.addChildren([
        adminCatalogueListRoute,
        adminCatalogueNewRoute,
        adminCatalogueEditRoute,
      ]),
      adminPropertiesRoute.addChildren([
        adminPropertyListRoute,
        adminPropertyNewRoute,
        adminPropertyDetailRoute,
        adminPropertyEditRoute,
      ]),
      adminUsersRoute.addChildren([adminUserListRoute, adminUserDetailRoute]),
      adminValidationRoute.addChildren([adminFindingListRoute, adminFindingDetailRoute]),
      adminReleasesRoute.addChildren([adminReleaseListRoute, adminReleaseNewRoute]),
      adminExportConfigRoute,
      adminAuditRoute,
    ]),
  ]),
]);

export type AppRouteTree = typeof routeTree;
