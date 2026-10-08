import { Fragment } from "react";

import type { components } from "../api/schema.ts";
import { CodeChip } from "../components/code-chip.tsx";
import { StatusBadge } from "../components/status-badge.tsx";
import { describePropertyValue } from "./format-property-value.ts";
import { statusLabelFor, statusToneFor } from "./status-options.ts";
import { trimSpecimenSuffix } from "./trim-specimen-suffix.ts";

type PropertyValue = components["schemas"]["PropertyValue"];

const DISCIPLINE_KEY = "discipline";
const SPECIMEN_KEY = "specimen";

interface ValueView {
  ordinal: number;
  text: string;
  snomedCode: string | null;
  justification: string | null;
}

interface PropertyGroup {
  key: string;
  label: string;
  status: string;
  values: ValueView[];
}

/** A specimen reads as its trimmed term alone. A value with no recorded term
 * falls back to its code as plain text, as the catalogue list does. */
function specimenView(property: PropertyValue): ValueView {
  const { text, snomedCode } = describePropertyValue(property.value);
  return {
    ordinal: property.ordinal,
    text: trimSpecimenSuffix(text !== "" ? text : (snomedCode ?? "")),
    snomedCode: null,
    justification: property.justification,
  };
}

function plainView(property: PropertyValue): ValueView {
  const { text, snomedCode } = describePropertyValue(property.value);
  return {
    ordinal: property.ordinal,
    text,
    snomedCode,
    justification: property.justification,
  };
}

/** The first of any values that read the same once trimmed. */
function withoutRepeats(values: ValueView[]): ValueView[] {
  const seen = new Set<string>();
  return values.filter(({ text }) => {
    if (seen.has(text)) {
      return false;
    }
    seen.add(text);
    return true;
  });
}

/** One group per property key, in the order the API served them, with each
 * group's values in their recorded order. The discipline group is left out:
 * the Disciplines row already lists it. */
function groupByKey(properties: PropertyValue[]): PropertyGroup[] {
  const groups = new Map<string, PropertyValue[]>();
  for (const property of properties) {
    if (property.key === DISCIPLINE_KEY) {
      continue;
    }
    groups.set(property.key, [...(groups.get(property.key) ?? []), property]);
  }
  return [...groups.values()].map((members) => {
    const ordered = members.slice().sort((a, b) => a.ordinal - b.ordinal);
    const isSpecimen = members[0].key === SPECIMEN_KEY;
    const views = ordered.map(isSpecimen ? specimenView : plainView);
    return {
      key: members[0].key,
      label: members[0].label,
      status: members[0].status,
      values: isSpecimen ? withoutRepeats(views) : views,
    };
  });
}

function Value({ view }: { view: ValueView }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-2">
        {view.text !== "" ? <span className="break-words">{view.text}</span> : null}
        {view.snomedCode !== null ? <CodeChip code={view.snomedCode} /> : null}
      </span>
      {view.justification !== null ? (
        <span className="break-words text-[var(--color-text-muted)]">
          Justification: {view.justification}
        </span>
      ) : null}
    </div>
  );
}

/**
 * The entry's properties as term/description pairs for a `dl`. Each value is
 * read by its shape (`describePropertyValue`), so nothing here branches on
 * `datatype`, which ADR-0013 forbids; the specimen branch is on the property
 * key. A property whose definition is deprecated keeps its recorded values
 * (FR-11), marked as such.
 */
export function PropertyRows({ properties }: { properties: PropertyValue[] }) {
  return (
    <>
      {groupByKey(properties).map((group) => (
        <Fragment key={group.key}>
          <dt className="flex flex-wrap items-center gap-2 font-medium">
            {group.label}
            {group.status !== "active" ? (
              <StatusBadge
                tone={statusToneFor(group.status)}
                label={statusLabelFor(group.status)}
              />
            ) : null}
          </dt>
          <dd className="m-0 min-w-0">
            {group.values.length === 1 ? (
              <Value view={group.values[0]} />
            ) : (
              <ul className="m-0 flex list-none flex-col gap-2 p-0">
                {group.values.map((view) => (
                  <li key={view.ordinal}>
                    <Value view={view} />
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </Fragment>
      ))}
    </>
  );
}
