#!/usr/bin/env node
/**
 * Asserts that no client secret reached the frontend (issue #41, NFR-01,
 * NFR-26).
 *
 * Issue #41's first acceptance criterion asks for this "asserted against
 * the built assets, not by inspection", so the primary check runs over
 * `dist/` after `pnpm build`.
 *
 * **But the built assets cannot carry the whole check.** Vite inlines a
 * `VITE_*` variable's *value* and discards its *name*, so a real
 * `VITE_OIDC_CLIENT_SECRET=hunter2` would appear in `dist/` as the bare
 * string `"hunter2"` — indistinguishable from any other literal. The name,
 * which is the only reliable signal, survives only in the source. So there
 * are two passes:
 *
 *   1. `dist/` for credential-shaped content (an OAuth `client_secret`
 *      parameter, a PEM key, a client-credentials grant) — things that are
 *      recognisable whatever they are called;
 *   2. `src/` for a secret-*named* `import.meta.env` key — the leak the
 *      first pass provably cannot see.
 *
 * Neither pass can detect a secret assigned to an innocuously named
 * variable, and no static check could. That is the residual, and it is
 * covered by review plus the fact that ADR-0021's client is public and has
 * no secret to configure in the first place.
 *
 * Usage: node scripts/assert-no-secret-in-bundle.mjs [dist-dir] [src-dir]
 */

import { readdir, readFile } from "node:fs/promises";
import { join, relative, resolve } from "node:path";
import process from "node:process";

const distDir = resolve(process.argv[2] ?? "dist");
const srcDir = resolve(process.argv[3] ?? "src");

/**
 * Each pattern names a way a credential would actually appear, not merely
 * the word "secret" — `Math.random()` and `secretSauce` are not findings,
 * and a check that flagged them would be turned off within a week.
 */
const BUILT_ASSET_PATTERNS = [
  { name: "OAuth client_secret parameter", regex: /\bclient_secret\b/i },
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

/**
 * A `VITE_*` key whose *name* says it holds a credential. Matched against
 * the source, because the name does not survive the build.
 */
const SOURCE_PATTERNS = [
  {
    name: "a secret-named VITE_ variable",
    regex: /VITE_[A-Z0-9_]*(SECRET|PASSWORD|PASSWD|PRIVATE_KEY|CREDENTIAL|API_KEY|TOKEN)/,
  },
];

const BUILT_ASSET_EXTENSIONS = /\.(js|mjs|cjs|css|html|json|map|txt|svg)$/i;
const SOURCE_EXTENSIONS = /\.(ts|tsx|js|jsx|mjs|cjs|html|env)$/i;

async function* walk(dir, label) {
  let entries;
  try {
    entries = await readdir(dir, { withFileTypes: true });
  } catch (error) {
    throw new Error(
      `cannot read ${label} directory ${dir} - run \`pnpm build\` first (${error.message})`,
      { cause: error },
    );
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(path, label);
    } else {
      yield path;
    }
  }
}

async function scan(dir, { extensions, patterns, label }) {
  const findings = [];
  let scanned = 0;
  for await (const path of walk(dir, label)) {
    if (!extensions.test(path)) {
      continue;
    }
    scanned += 1;
    const content = await readFile(path, "utf8");
    for (const { name, regex } of patterns) {
      const match = regex.exec(content);
      if (match) {
        findings.push(
          `${label}: ${relative(dir, path)}: ${name} (matched ${JSON.stringify(match[0])})`,
        );
      }
    }
  }
  return { findings, scanned };
}

const built = await scan(distDir, {
  extensions: BUILT_ASSET_EXTENSIONS,
  patterns: BUILT_ASSET_PATTERNS,
  label: "built asset",
});

if (built.scanned === 0) {
  console.error(`No scannable assets found in ${distDir} - did the build run?`);
  process.exit(1);
}

// This script is the check, so it must not flag its own pattern list.
const source = await scan(srcDir, {
  extensions: SOURCE_EXTENSIONS,
  patterns: SOURCE_PATTERNS,
  label: "source",
});

const findings = [...built.findings, ...source.findings];

if (findings.length > 0) {
  console.error("A credential appears to have reached the frontend:");
  for (const finding of findings) {
    console.error(`  - ${finding}`);
  }
  console.error(
    "\nNFR-01: the frontend is a public OIDC client and must never carry a secret.",
  );
  process.exit(1);
}

console.log(
  `No client secret found in ${built.scanned} built asset(s) or ${source.scanned} ` +
    `source file(s). (NFR-01)`,
);
