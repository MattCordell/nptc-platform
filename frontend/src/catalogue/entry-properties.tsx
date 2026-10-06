import { Fragment } from "react";

import type { components } from "../api/schema.ts";
import { StatusBadge } from "../components/status-badge.tsx";
import { EntrySection } from "./entry-section.tsx";
import { formatPropertyValue } from "./format-property-value.ts";
import { statusLabelFor, statusToneFor } from "./status-options.ts";

type PropertyValue = components["schemas"]["PropertyValue"];

interface PropertyGroup {
  key: string;
  label: string;
  status: string;
  values: PropertyValue[];
}

/** One group per property key, in the order the API served them, with each
 * group's values in their recorded order. */
function groupByKey(properties: PropertyValue[]): PropertyGroup[] {
  const groups = new Map<string, PropertyGroup>();
  for (const property of properties) {
    const group = groups.get(property.key);
    if (group) {
      group.values.push(property);
    } else {
      groups.set(property.key, {
        key: property.key,
        label: property.label,
        status: property.status,
        values: [property],
      });
    }
  }
  return [...groups.values()].map((group) => ({
    ...group,
    values: group.values.slice().sort((a, b) => a.ordinal - b.ordinal),
  }));
}

function Value({ property }: { property: PropertyValue }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span>{formatPropertyValue(property.value)}</span>
      {property.justification !== null ? (
        <span className="text-sm text-[var(--color-text-muted)]">
          Justification: {property.justification}
        </span>
      ) : null}
    </div>
  );
}

/**
 * Every property the API serves for the entry, rendered the same way whatever
 * its type: `PropertyValue.value` is untyped, and a branch on `datatype` here
 * is what ADR-0013 forbids. A property whose definition is deprecated keeps its
 * recorded values (FR-11), marked as such.
 */
export function EntryProperties({ properties }: { properties: PropertyValue[] }) {
  const groups = groupByKey(properties);
  return (
    <EntrySection title="Properties">
      {groups.length === 0 ? (
        <p className="m-0 text-[var(--color-text-muted)]">
          This entry has no recorded properties.
        </p>
      ) : (
        <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]">
          {groups.map((group) => (
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
              <dd className="m-0">
                {group.values.length === 1 ? (
                  <Value property={group.values[0]} />
                ) : (
                  <ul className="m-0 flex list-none flex-col gap-2 p-0">
                    {group.values.map((property) => (
                      <li key={property.ordinal}>
                        <Value property={property} />
                      </li>
                    ))}
                  </ul>
                )}
              </dd>
            </Fragment>
          ))}
        </dl>
      )}
    </EntrySection>
  );
}
