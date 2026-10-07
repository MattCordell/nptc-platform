# Frontend browser smoke tests

The Playwright suite in `frontend/e2e/` opens the running web app in Chromium and runs
axe-core on each page with colour contrast switched on (NFR-31). jsdom cannot compute
layout, so the component tests in `frontend/src/test/a11y.ts` turn that rule off. This
suite closes the gap.

The suite runs on your machine against a running compose stack (NFR-41). It is not part
of CI yet.

## What it checks

| Test | What it checks |
|---|---|
| Landing page (`/`) | The `h1` shows and axe reports no violation |
| Sign-in (`/sign-in`) | The browser lands on the Keycloak authorisation endpoint with `client_id=nptc-frontend`, a `code_challenge` and the right `redirect_uri`, and Keycloak shows its username field. The test does not sign in. |
| Stub page (`/about`) | The `h1` shows and axe reports no violation |
| Not found (`/no-such-page`) | The `h1` shows and axe reports no violation |

Any axe violation fails the test. Axe keeps its default rule set, and the suite disables
no rule.

## Before you run it

You need Node 26.10 or later and pnpm, as in
[`local-development.md`](local-development.md), and a running stack from
[`deployment.md`](deployment.md#start-the-stack). `docker compose -f deploy/compose.yml ps -a`
should show `web` and `keycloak` healthy.

Install the frontend dependencies and the Chromium build once:

```powershell
pnpm install
cd frontend
pnpm exec playwright install chromium
```

## Run it

From `frontend/`:

```powershell
pnpm test:e2e
```

From the repo root, `pnpm --filter nptc-frontend test:e2e` does the same.

The suite targets `http://localhost:8081`. To use another origin, set `NPTC_E2E_BASE_URL`:

```powershell
$env:NPTC_E2E_BASE_URL = "http://localhost:9090"
pnpm test:e2e
```

Keycloak must be reachable from your browser at the address in `NPTC_OIDC_ISSUER`
(`http://localhost:8080` by default). The origin you test must equal the stack's
`NPTC_FRONTEND_BASE_URL`, because Keycloak refuses any other `redirect_uri`. The sign-in
test fails when they differ, because Keycloak then shows an error page instead of its login form.

## Read a failure

A contrast or other axe failure lists each rule as
`- [impact] rule-id: help text (CSS target)`. For a `color-contrast` failure, find the
`--color-*` token behind the named element in `frontend/src/styles/app.css`. A failed run
writes `frontend/playwright-report/` and a trace under `frontend/test-results/`. Open the
report with `pnpm exec playwright show-report`. Both folders are ignored by git.

To prove that the gate catches a real regression, lower a colour token such as
`--color-text-muted` in `app.css`, serve the app, and rerun the suite. The landing, stub
and not-found tests should fail with `color-contrast`. Revert the token afterwards.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `browserType.launch: Executable doesn't exist` | Run `pnpm exec playwright install chromium`. |
| `net::ERR_CONNECTION_REFUSED` | The stack is not running, or `NPTC_E2E_BASE_URL` points at the wrong port. |
| The sign-in test cannot find the username field | Keycloak showed an error page. Usually `NPTC_E2E_BASE_URL` differs from `NPTC_FRONTEND_BASE_URL`, so Keycloak refused the `redirect_uri`. See [`deployment.md`](deployment.md#troubleshooting). |
| The sign-in test times out | An anonymous cold load waits for the session probe before it redirects. Check that `keycloak` is healthy. |

Vitest ignores `frontend/e2e/`, so `pnpm test` never starts a browser.
