#!/usr/bin/env node
/**
 * Asserts that no client secret reached the built assets (issue #41,
 * NFR-01, NFR-26).
 *
 * Issue #41's first acceptance criterion asks for this "asserted against
 * the built assets, not by inspection" - so this runs over `dist/` after
 * `pnpm build`, not over `src/`. A source-level check would miss a secret
 * that arrived through a `VITE_*` variable at build time, which is exactly
 * the way one would realistically get in: Vite inlines every `VITE_*` value
 * into the bundle as a literal, so a well-meaning `VITE_OIDC_CLIENT_SECRET`
 * would be published to every visitor with no warning from anything else in
 * the toolchain.
 *
 * It is expected to pass trivially today - ADR-0021's `nptc-frontend` is a
 * public client and there is no secret to leak. The point is that it keeps
 * passing.
 *
 * Usage: node scripts/assert-no-secret-in-bundle.mjs [dist-dir]
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import process from "node:process";

const distDir = resolve(process.argv[2] ?? "dist");

/**
 * Each pattern names a way a credential would actually appear, not merely
 * the word "secret" - `Math.random()` and `secretSauce` are not findings,
 * and a check that flagged them would be turned off within a week.
 */
const PATTERNS = [
  { name: "OAuth client_secret parameter", regex: /\bclient_secret\b/i },
  {
    name: "a VITE_ variable named like a secret",
    regex: /VITE_[A-Z0-9_]*(SECRET|PASSWORD|PRIVATE_KEY|TOKEN)/,
  },
  {
    name: "an assignment to a secret-shaped key",
    regex: /["']?(client_?secret|clientSecret)["']?\s*[:=]\s*["'][^"']+["']/i,
  },
  { name: "a PEM private key", regex: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
  {
    name: "a Keycloak client-credentials grant",
    regex: /grant_type=client_credentials/i,
  },
];

async function* walk(dir) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    throw new Error(`cannot read ${dir} - run \`pnpm build\` first (${error.message})`, {
      cause: error,
    });
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(path);
    } else {
      yield path;
    }
  }
}

const findings = [];
let scanned = 0;

for await (const path of walk(distDir)) {
  // Every text-ish asset, not just .js: a secret pasted into a CSS custom
  // property or a source map is just as published.
  if (!/\.(js|mjs|cjs|css|html|json|map|txt|svg)$/i.test(path)) {
    continue;
  }
  scanned += 1;
  const content = await readFile(path, "utf8");
  for (const { name, regex } of PATTERNS) {
    const match = regex.exec(content);
    if (match) {
      findings.push(
        `${relative(distDir, path)}: ${name} (matched ${JSON.stringify(match[0])})`,
      );
    }
  }
}

if (scanned === 0) {
  console.error(`No scannable assets found in ${distDir} - did the build run?`);
  process.exit(1);
}

if (findings.length > 0) {
  console.error("A credential appears to have been published in the built assets:");
  for (const finding of findings) {
    console.error(`  - ${finding}`);
  }
  console.error(
    "\nNFR-01: the frontend is a public OIDC client and must never carry a secret.",
  );
  process.exit(1);
}

console.log(
  `No client secret found in ${scanned} built asset(s) under ${distDir}. (NFR-01)`,
);
