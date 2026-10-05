# Building, running and stopping the stack

This guide takes you from a clean checkout to a working platform in a browser. You need
Docker and nothing else: no local Python or Node install.

**This stack is for development and evaluation, not for production.** It serves plain HTTP,
runs Keycloak in `start-dev` mode without a volume, and allows anyone to register. TLS,
Keycloak persistence and hardened containers arrive with the production overlay (NFR-42,
P5). Do not expose it to the internet.

## What runs

| Service | What it does | Host port |
|---|---|---|
| `postgres` | The database (PostgreSQL 18, pinned to UTF-8) | `POSTGRES_PORT`, default 5432 |
| `keycloak` | Sign-in (OIDC); imports the committed realm on every start and shows it in the `nptc` login theme | `KEYCLOAK_PORT`, default 8080 |
| `migrate` | Runs once: applies every migration, then creates the `nptc_app_login` database login. It exits when done | none |
| `backend` | The API. Starts only after `migrate` succeeds | none |
| `web` | Caddy: serves the web app and forwards `/api/*` to `backend`, so the browser sees one origin | `NPTC_WEB_PORT`, default 8081 |

Only `migrate` holds the database owner's credential. The `backend` service connects as the
least-privilege `nptc_app_login` role and never receives `NPTC_MIGRATION_DATABASE_URL`.

## Start the stack

1. Copy the example settings:

   ```powershell
   Copy-Item deploy/.env.example deploy/.env
   ```

2. Replace each `change-me` in `deploy/.env` with a local-only password. Keep
   `POSTGRES_PASSWORD` and `NPTC_APP_DB_PASSWORD` free of `@ : / ? # %`, because compose
   places them inside database URLs. `migrate` refuses an `NPTC_APP_DB_PASSWORD` with one
   of these characters and names the variable. It cannot check `POSTGRES_PASSWORD`, so a
   bad value there shows up as a `migrate` authentication failure.

3. Build and start everything:

   ```powershell
   docker compose -f deploy/compose.yml up -d --build
   ```

   The first run downloads base images and builds two images, so allow several minutes.

4. Check that it is ready:

   ```powershell
   docker compose -f deploy/compose.yml ps -a
   ```

   `postgres`, `keycloak`, `backend` and `web` should read `healthy`. `migrate` should read
   `Exited (0)`. That exit is correct: it is a one-shot job.

5. Open <http://localhost:8081>.

## Create the first user and administrator

The `nptc` realm has no users. The first person to sign in has no permissions until you
grant a role.

1. Open the Keycloak admin console at <http://localhost:8080/admin> and sign in with
   `KEYCLOAK_ADMIN_USER` and `KEYCLOAK_ADMIN_PASSWORD` (realm `master`).
2. Switch to the `nptc` realm. Open **Users**, choose **Add user**, then set a password on the
   **Credentials** tab with **Temporary** switched off. You can also use **Register** on the
   sign-in page.
3. Sign in to <http://localhost:8081> as that user once. This creates the user's `app_user`
   record with the `provisional` role, which has no administrator access.
4. Grant the administrator role from the command line, using the Keycloak username:

   ```powershell
   docker compose -f deploy/compose.yml exec backend python scripts/grant_role.py --username <username> --role administrator
   ```

   The command records a `user_role.granted` audit event. See
   [`upgrade.md`](upgrade.md#bootstrapping-the-first-administrator) for why this step is deliberately
   outside the API.
5. Open an administrator page, such as <http://localhost:8081/admin/catalogue>. The
   administrator role needs a second factor (NFR-06). The first time, Keycloak asks you to
   set up an authenticator app, then returns you to the page. Until that step succeeds, the
   API hides the role: `GET /api/v1/auth/me` lists only `provisional`, although the database
   already holds the grant. See [`permissions.md`](../architecture/permissions.md).

## Seed the baseline catalogue

A new stack has an empty catalogue. Load the transform's `import-dataset.json` once, before
anyone edits. The seed refuses a catalogue that already holds data, so run `--dry-run` first.
The full procedure, the exit codes and the fix for each refusal are in
[`runbooks/seed-baseline.md`](runbooks/seed-baseline.md).

```powershell
docker compose -f deploy/compose.yml cp transform-report/import-dataset.json backend:/tmp/import-dataset.json
docker compose -f deploy/compose.yml exec backend python scripts/seed_baseline.py --dataset /tmp/import-dataset.json --dry-run
docker compose -f deploy/compose.yml exec backend python scripts/seed_baseline.py --dataset /tmp/import-dataset.json
```

## Stop the stack

Stop the containers and keep the database:

```powershell
docker compose -f deploy/compose.yml down
```

Stop the containers **and delete the database**, which destroys all catalogue data:

```powershell
docker compose -f deploy/compose.yml down -v
```

## Change or update the stack

- **After pulling new code**, run `docker compose -f deploy/compose.yml up -d --build`.
  `migrate` runs again and applies any new migrations before `backend` restarts.
- **After changing `VITE_OIDC_ISSUER` or `VITE_OIDC_CLIENT_ID`**, rebuild the `web` image.
  Vite writes these values into the built files, so a restart is not enough.
- **After changing `NPTC_FRONTEND_BASE_URL`**, recreate `keycloak` as well
  (`up -d --force-recreate keycloak`). The realm import reads it only at start. The privacy
  and terms links on the registration page use the same address.
- **After editing the login theme** in `deploy/keycloak/themes/nptc/`, reload the browser
  page. `start-dev` does not cache themes, so the next page load shows the change and no
  restart is needed. A change to `loginTheme` in the realm file is different: recreate
  `keycloak` as above. See [the login theme](../architecture/authentication.md#the-login-theme).
- **After changing a port**, change the matching address too:
  - `NPTC_WEB_PORT` must match the port in `NPTC_FRONTEND_BASE_URL`.
  - `KEYCLOAK_PORT` must match the port in both `NPTC_OIDC_ISSUER` and `VITE_OIDC_ISSUER`.
    The issuer is the address your browser reaches Keycloak on, and the token's `iss` claim
    repeats it.

## Postgres data and encoding

The stack pins the database to UTF-8 with `POSTGRES_INITDB_ARGS`. Postgres reads that
setting only when it first creates a data directory. A volume from an earlier version keeps
its old encoding, and you must run `down -v` to get the pin.

The Postgres 18 image keeps its data under `/var/lib/postgresql`. If Postgres restarts in a
loop and its log mentions an "unused mount/volume", the volume came from an older layout.
Run `down -v` and start again.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `required variable NPTC_APP_DB_PASSWORD is missing a value` | Your `deploy/.env` predates this variable. Add it from `deploy/.env.example`. |
| `migrate` exits with an error | Run `docker compose -f deploy/compose.yml logs migrate`. The last line names the failure type. A wrong database password is the usual cause after you edit `POSTGRES_PASSWORD` on an existing volume. |
| `backend` never becomes healthy | Run `docker compose -f deploy/compose.yml logs backend`. A settings error names the variable. |
| Keycloak shows "Invalid parameter: redirect_uri" | `NPTC_FRONTEND_BASE_URL` does not match the address in your browser. Fix it, then recreate `keycloak`. |
| Every API call fails with a 401 after sign-in | `NPTC_OIDC_ISSUER` does not match the address Keycloak signed the token with. Use the address your browser uses. |
| Sign-in works, but pages say you lack permission | You have no role yet, or you hold the administrator role but have not completed the second-factor step. See [Create the first user and administrator](#create-the-first-user-and-administrator). |

To run the API or web app on your own machine instead, see
[`local-development.md`](local-development.md). Every setting is listed in
[`configuration.md`](configuration.md).
