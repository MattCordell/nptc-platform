// Lives outside `src` for the same reason as `fr-83-no-semantic-tag-stripping
// .test.ts`: it reads a file from disk, so it needs Node's types, which the
// browser program in `tsconfig.app.json` deliberately leaves out.
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { COLLECTION_NOTICE } from "../src/shell/collection-notice.ts";

/**
 * NFR-45: the terms acceptance gate repeats the collection notice that
 * Keycloak's registration page shows, so a person who registered reads the
 * same account of their details when they accept the terms. The two copies sit
 * in different programs, so this test is what stops them drifting apart when
 * the retention wording or the privacy policy (#64) changes one of them.
 */

const MESSAGES = resolve(
  dirname(fileURLToPath(import.meta.url)),
  "../../deploy/keycloak/themes/nptc/login/messages/messages_en.properties",
);

function keycloakMessages(): Map<string, string> {
  const messages = new Map<string, string>();
  for (const line of readFileSync(MESSAGES, "utf-8").split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (separator > 0 && !line.startsWith("#")) {
      messages.set(line.slice(0, separator), line.slice(separator + 1));
    }
  }
  return messages;
}

describe("the gate's collection notice matches Keycloak's registration notice", () => {
  const messages = keycloakMessages();

  it.each([
    ["title", "nptcRegisterNoticeTitle"],
    ["visible", "nptcRegisterNoticeVisible"],
    ["retention", "nptcRegisterNoticeRetention"],
    ["access", "nptcRegisterNoticeAccess"],
    ["privacyLink", "nptcRegisterPrivacyLink"],
  ] as const)("says the same as %s", (field, key) => {
    expect(COLLECTION_NOTICE[field]).toBe(messages.get(key));
  });

  // The one deliberate difference: Keycloak says "this form" beside the form
  // it describes, and the gate has no registration form on the page.
  it("says the same as collect, naming the registration form", () => {
    expect(COLLECTION_NOTICE.collect).toBe(
      messages
        .get("nptcRegisterNoticeCollect")
        ?.replace("on this form", "on the registration form"),
    );
  });
});
