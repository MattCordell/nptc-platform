/**
 * The collection notice the terms acceptance gate shows (NFR-45). It repeats
 * Keycloak's registration notice
 * (`deploy/keycloak/themes/nptc/login/messages/messages_en.properties`) so a
 * person who registered reads the same account of their details when they
 * accept the terms. `frontend/tests/terms-collection-notice.test.ts` fails if
 * the two differ. The terms text is served by the API; this notice is SPA copy.
 *
 * `collect` is the one sentence that differs on purpose: Keycloak says "this
 * form" beside the form it describes.
 */
export const COLLECTION_NOTICE = {
  title: "How we use your details",
  collect:
    "We collect the details you enter on the registration form to create your account and to attribute your contributions to the catalogue.",
  visible: "Platform administrators can see your identity and your interest records.",
  retention:
    "How long we keep your details is still being settled. The privacy policy will state it.",
  access:
    "To ask for access to your details or to correct them, follow the privacy policy.",
  privacyLink: "Read the privacy policy",
} as const;
