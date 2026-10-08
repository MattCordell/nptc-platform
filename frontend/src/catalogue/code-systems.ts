export type CodeSystem = {
  /** The short alias the code route takes in its path. */
  token: string;
  /** The system's full URI, which the lookup route takes in its query. */
  uri: string;
  label: string;
};

/**
 * The code systems the API registers (`nptc.catalogue.code_systems`). The API
 * has no endpoint that lists them, so this is the one place the screens learn
 * them. Add a system here in the same change that registers it server-side.
 */
export const CODE_SYSTEMS: readonly CodeSystem[] = [
  { token: "sct", uri: "http://snomed.info/sct", label: "SNOMED CT" },
];

export const DEFAULT_CODE_SYSTEM: CodeSystem = CODE_SYSTEMS[0] as CodeSystem;

export function codeSystemForToken(token: string): CodeSystem | undefined {
  return CODE_SYSTEMS.find((system) => system.token === token);
}

export function codeSystemForUri(uri: string): CodeSystem | undefined {
  return CODE_SYSTEMS.find((system) => system.uri === uri);
}
