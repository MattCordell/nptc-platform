# Running the API and web app on your own machine

Use this when you edit the code and want hot reload. Postgres and Keycloak still run in
Docker, while the API runs under `uv` and the web app under `pnpm`. To run the whole stack in
containers instead, see [`deployment.md`](deployment.md).

You need Docker, `uv`, Node 26 and `pnpm`. Run every command from the repository root unless
a step says otherwise.

Node 25 and later ship no `corepack`, so `corepack enable` does not work. Install pnpm with
npm instead, using the version in the `packageManager` field of the root `package.json`:
`npm install --global pnpm@11.20.0`.

## 1. Create the settings files

```powershell
Copy-Item deploy/.env.example deploy/.env
Copy-Item deploy/.env.example frontend/.env
```

In `deploy/.env`, change `NPTC_FRONTEND_BASE_URL` to `http://localhost:5173`. That is where
`pnpm dev` serves the web app, and the API's cross-origin allow-list and Keycloak's redirect
addresses both read this value.

In `frontend/.env`, add `VITE_API_BASE_URL=http://localhost:8000`. The Vite dev server has no
proxy, so without this the web app sends API calls to itself.

## 2. Start Postgres and Keycloak

```powershell
docker compose -f deploy/compose.yml up -d postgres keycloak
docker compose -f deploy/compose.yml ps
```

Both should read `healthy`. If you started the full stack earlier, recreate Keycloak so it
picks up the new origin: `docker compose -f deploy/compose.yml up -d --force-recreate keycloak`.

## 3. Load the settings into your shell

The API reads its settings from environment variables, not from a `.env` file. This loads
every `NAME=value` line of `deploy/.env` into the current PowerShell session:

```powershell
Get-Content deploy/.env | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}
```

Repeat this in each new terminal that runs an `nptc` command.

## 4. Migrate the database and create the login

```powershell
uv run alembic upgrade head
uv run python -m nptc.db.provision_login
```

The second command creates the `nptc_app_login` role with the password in
`NPTC_APP_DB_PASSWORD`. Make sure the password in `NPTC_DATABASE_URL` matches it. Both
commands are safe to repeat.

## 5. Start the API

```powershell
uv run uvicorn nptc.api.app:create_app --factory --reload --app-dir backend/src
```

- Swagger UI: <http://localhost:8000/api/v1/docs>
- OpenAPI document: <http://localhost:8000/api/v1/openapi.json>
- There is no route at `/`, so a 404 there is expected.

## 6. Start the web app

In a second terminal:

```powershell
cd frontend
pnpm install
pnpm dev
```

Open <http://localhost:5173>. Vite reads `VITE_OIDC_ISSUER`, `VITE_OIDC_CLIENT_ID` and
`VITE_API_BASE_URL` from `frontend/.env`.

## 7. Sign in

Follow [Create the first user and administrator](deployment.md#create-the-first-user-and-administrator)
in the deployment guide. Replace the last command with:

```powershell
uv run python scripts/grant_role.py --username <username> --role administrator
```

## What is built

Many routes are still placeholder pages (see `frontend/src/router/route-tree.ts`). That is
expected at this stage. The admin catalogue list (`/admin/catalogue`) and the catalogue edit
screen are real. Check the route tree for the current state before assuming something is
missing.

## Running the backend tests

`CLAUDE.md` lists the test commands. Most backend tests start a Postgres container, so the full
run is slow. [Which backend tests need a database](backend-test-container-split.md) records
which tests need the container and what the timings are, so you do not repeat that work.

## Known gotchas

- `cp` does not exist in PowerShell. Use `Copy-Item`.
- Recreating Keycloak drops any users you created, because Keycloak has no volume by design.
  The realm re-imports on every start, and only Postgres persists.
- If Postgres restarts in a loop, see
  [Postgres data and encoding](deployment.md#postgres-data-and-encoding).
