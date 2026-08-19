import { createRootRoute, createRoute } from "@tanstack/react-router";

import { HomePage } from "../pages/home.tsx";
import { createPlaceholderPage } from "../pages/placeholder.tsx";
import { RequireAuth } from "../shell/require-auth.tsx";
import { RootLayout } from "../shell/root-layout.tsx";
import {
  validateCatalogueSearch,
  validateLookupSearch,
  validateReleaseCompareSearch,
  validateSignInSearch,
  type CatalogueSearch,
  type CatalogueSearchInput,
  type LookupSearch,
  type LookupSearchInput,
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

// FR-14..16, FR-18. Search result state (query, filters, page) lives
// entirely in the URL, so a pasted link restores it (#140).
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
  component: createPlaceholderPage({ title: "Search the catalogue", issue: 138 }),
  head: titled("Search"),
});

// FR-17: /catalogue/lookup?system={uri}&code={code}. Declared as a static
// sibling of $businessKey so it is matched before the dynamic segment - see
// route-tree.test.tsx's precedence assertion.
const catalogueLookupRoute = createRoute({
  getParentRoute: () => catalogueRoute,
  path: "lookup",
  validateSearch: validateLookupSearch as (search: LookupSearchInput) => LookupSearch,
  component: createPlaceholderPage({ title: "Code lookup", issue: 140 }),
  head: titled("Code lookup"),
});

// FR-17: /catalogue/code/{system_token}/{code}. `sct` aliases
// http://snomed.info/sct. Both params stay plain strings - no `params.parse`
// coercion - because a code is a string end to end (FR-06).
const catalogueCodeLookupRoute = createRoute({
  getParentRoute: () => catalogueRoute,
  path: "code/$systemToken/$code",
  component: createPlaceholderPage({ title: "Code lookup", issue: 140 }),
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
  component: createPlaceholderPage({ title: "Catalogue entry", issue: 142 }),
  head: titled("Catalogue entry"),
});

// FR-19, FR-35: full change history, including linked amendment submissions.
const catalogueEntryHistoryRoute = createRoute({
  getParentRoute: () => catalogueEntryRoute,
  path: "history",
  component: createPlaceholderPage({ title: "Entry change history", issue: 141 }),
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
  component: createPlaceholderPage({ title: "About the catalogue" }),
  head: titled("About"),
});

const termsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "terms",
  component: createPlaceholderPage({ title: "Terms of use", issue: 64 }),
  head: titled("Terms of use"),
});

// --- public: auth entry points (#41) ---------------------------------------

const signInRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "sign-in",
  validateSearch: validateSignInSearch as unknown as (
    search: SignInSearchInput,
  ) => SignInSearch,
  component: createPlaceholderPage({ title: "Sign in", issue: 41 }),
  head: titled("Sign in"),
});

const signOutRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "sign-out",
  component: createPlaceholderPage({ title: "Sign out", issue: 41 }),
  head: titled("Sign out"),
});

const registerRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "register",
  component: createPlaceholderPage({ title: "Register", issue: 41 }),
  head: titled("Register"),
});

const authCallbackRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "auth/callback",
  component: createPlaceholderPage({ title: "Signing in…", issue: 41 }),
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
  component: createPlaceholderPage({ title: "New submission" }),
  head: titled("New submission"),
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

const adminRoute = createRoute({
  getParentRoute: () => authenticatedRoute,
  path: "admin",
});

const adminHomeRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "/",
  component: createPlaceholderPage({ title: "Administration" }),
  head: titled("Administration"),
});

// FR-36..39: catalogue entry, designation, code binding, changelog-note edit.
const adminCatalogueRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "catalogue",
});

const adminCatalogueListRoute = createRoute({
  getParentRoute: () => adminCatalogueRoute,
  path: "/",
  component: createPlaceholderPage({ title: "Catalogue administration" }),
  head: titled("Catalogue administration"),
});

const adminCatalogueNewRoute = createRoute({
  getParentRoute: () => adminCatalogueRoute,
  path: "new",
  component: createPlaceholderPage({ title: "New catalogue entry" }),
  head: titled("New catalogue entry"),
});

const adminCatalogueEditRoute = createRoute({
  getParentRoute: () => adminCatalogueRoute,
  path: "$businessKey/edit",
  component: createPlaceholderPage({ title: "Edit catalogue entry", issue: 149 }),
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
  component: createPlaceholderPage({ title: "Property registry" }),
  head: titled("Property registry"),
});

const adminPropertyNewRoute = createRoute({
  getParentRoute: () => adminPropertiesRoute,
  path: "new",
  component: createPlaceholderPage({ title: "New property", issue: 151 }),
  head: titled("New property"),
});

const adminPropertyDetailRoute = createRoute({
  getParentRoute: () => adminPropertiesRoute,
  path: "$propertyKey",
  component: createPlaceholderPage({ title: "Property", issue: 151 }),
  head: titled("Property"),
});

// FR-40..43: user administration.
const adminUsersRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "users",
});

const adminUserListRoute = createRoute({
  getParentRoute: () => adminUsersRoute,
  path: "/",
  component: createPlaceholderPage({ title: "User administration" }),
  head: titled("User administration"),
});

const adminUserDetailRoute = createRoute({
  getParentRoute: () => adminUsersRoute,
  path: "$userId",
  component: createPlaceholderPage({ title: "User" }),
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
  component: createPlaceholderPage({ title: "Validation findings", issue: 141 }),
  head: titled("Validation findings"),
});

const adminFindingDetailRoute = createRoute({
  getParentRoute: () => adminValidationRoute,
  path: "$findingId",
  component: createPlaceholderPage({ title: "Validation finding", issue: 141 }),
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
  component: createPlaceholderPage({ title: "Release administration", issue: 141 }),
  head: titled("Release administration"),
});

const adminReleaseNewRoute = createRoute({
  getParentRoute: () => adminReleasesRoute,
  path: "new",
  component: createPlaceholderPage({ title: "Cut a release", issue: 141 }),
  head: titled("Cut a release"),
});

// FR-62..69, FR-78: export configuration.
const adminExportConfigRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "exports/config",
  component: createPlaceholderPage({ title: "Export configuration" }),
  head: titled("Export configuration"),
});

// NFR-08..13: audit log viewer, Admin only.
const adminAuditRoute = createRoute({
  getParentRoute: () => adminRoute,
  path: "audit",
  component: createPlaceholderPage({ title: "Audit log" }),
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
