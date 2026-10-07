/**
 * The wording of the About page (/about). Owners edit this file only; the
 * layout lives in `pages/about.tsx`. Each section becomes one card with its
 * own `h2`. Link only to screens that are built, and describe only features
 * that exist.
 */

export type AboutLinkTarget = "/catalogue" | "/exports" | "/terms";

export interface AboutSection {
  /** Used for the heading's `id`, so it must be unique and URL-safe. */
  id: string;
  heading: string;
  paragraphs: readonly string[];
  links?: readonly { label: string; to: AboutLinkTarget }[];
}

export const ABOUT_TITLE = "About the catalogue";

export const ABOUT_INTRO =
  "The National Pathology Test Catalogue lists the pathology tests that a clinician can request in Australia.";

export const ABOUT_SECTIONS: readonly AboutSection[] = [
  {
    id: "what-it-is",
    heading: "What the catalogue is",
    paragraphs: [
      "The catalogue is a shared list of pathology tests. Each entry has a name, a code and the details that describe the test.",
      "It is also known as the SPIA Requesting terminology. Laboratories, requesters and software systems use it to refer to the same test in the same way.",
    ],
    links: [{ label: "Search the catalogue", to: "/catalogue" }],
  },
  {
    id: "who-maintains-it",
    heading: "Who maintains it",
    paragraphs: [
      "RCPA-QAP curates the catalogue. Its curators add new tests, correct entries and retire tests that are no longer used.",
      "NCTS publishes the catalogue as a SNOMED CT reference set and as a FHIR ValueSet. Each release is a fixed version that systems can load.",
    ],
  },
  {
    id: "how-it-is-used",
    heading: "How to use it",
    paragraphs: [
      "Anyone can search the catalogue by test name or code, and open an entry to read its details.",
      "Release schedules and contact details will be added here.",
    ],
  },
];
