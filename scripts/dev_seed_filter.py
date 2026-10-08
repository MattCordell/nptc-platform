#!/usr/bin/env python3
"""Drops the entries from a transform `import-dataset.json` that the baseline loader would
refuse, so the 50-row sample workbook can seed a development stack (FR-70, FR-76, ADR-0042).

The sample is a real excerpt that nobody edits. Three things stop `seed_baseline.py`:

- an entry with a specimen that has no SNOMED CT code (loader exit 3). The transform blocks that
  now, so the sample has none; the rule stays as a backstop, like the collision rule below,
- an entry whose preferred term equals a designation on an earlier entry (FR-05, loader exit 5),
- an entry whose SNOMED CT code an earlier entry already holds (the database allows one active
  entry per code, loader exit 5).

Each is an editorial matter for RCPA-QAP, so the dev seed leaves those entries out and says which.
`scripts/dev_seed_workbook_filter.py` drops the collisions earlier, from the workbook, because the
transform blocks a dataset that holds one. The collision rule here stays as a backstop. The sample
today has no duplicate code; the rule keeps the filter right if the sample changes.
Entries keep their business keys, so the kept keys have gaps. Everything outside `entries` passes
through unchanged, including `source.sha256`, which names the workbook the transform read.

This is dev tooling. A production baseline is never filtered: the loader's refusal is the point.

Usage:
  uv run python scripts/dev_seed_filter.py --input in/import-dataset.json --output out/import-dataset.json

Run by `scripts/dev-seed.ps1`; see docs/operations/runbooks/seed-baseline.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nptc_shared.similarity import collision_key

#: 0 = filtered dataset written; 2 = usage error; 3 = the input is unreadable or not shaped like
#: an import dataset; 4 = no entry survived the filter, nothing written.
EXIT_OK = 0
EXIT_USAGE_ERROR = 2
EXIT_INPUT_REFUSED = 3
EXIT_NOTHING_LEFT = 4


class DatasetShapeError(ValueError):
    """The input is not the `import-dataset.json` shape the filter reads."""


@dataclass(frozen=True)
class DroppedEntry:
    business_key: str
    reason: str


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, type=Path, help="The transform's dataset.")
    parser.add_argument("--output", required=True, type=Path, help="Where to write the result.")
    return parser.parse_args(argv)


def _synonym_terms(entry: dict[str, Any]) -> list[str]:
    return [d["term"] for d in entry["designations"]]


def _uncoded_specimens(entry: dict[str, Any]) -> list[str]:
    return [v["value"] for v in entry["properties"]["specimen"] if v["code"] is None]


def _collides_with(
    entry: dict[str, Any], kept_preferred: set[str], kept_synonyms: set[str]
) -> str | None:
    """The first term FR-05 would refuse, judged against the entries already kept. The loader
    writes entries in order, so an entry meets only the ones before it."""
    preferred: str = entry["preferred_term"]
    if collision_key(preferred) in kept_preferred | kept_synonyms:
        return preferred
    for synonym in _synonym_terms(entry):
        if collision_key(synonym) in kept_preferred:
            return synonym
    return None


def _bound_codes(entry: dict[str, Any]) -> list[tuple[str, str]]:
    return [(b["system"], b["code"]) for b in entry["code_bindings"] if b["status"] == "active"]


def filter_dataset(document: dict[str, Any]) -> tuple[dict[str, Any], list[DroppedEntry]]:
    """Returns a copy of `document` without the entries the loader would refuse, and what was
    dropped. Raises `DatasetShapeError` for a document that is not an import dataset."""
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise DatasetShapeError("the dataset has no entries list")

    kept: list[dict[str, Any]] = []
    dropped: list[DroppedEntry] = []
    kept_preferred: set[str] = set()
    kept_synonyms: set[str] = set()
    code_holder: dict[tuple[str, str], str] = {}
    try:
        for entry in entries:
            key = entry["business_key"]
            uncoded = _uncoded_specimens(entry)
            if uncoded:
                dropped.append(
                    DroppedEntry(key, f"specimen with no SNOMED CT code: {', '.join(uncoded)}")
                )
                continue
            colliding = _collides_with(entry, kept_preferred, kept_synonyms)
            if colliding is not None:
                dropped.append(
                    DroppedEntry(key, f"{colliding!r} collides with an earlier entry (FR-05)")
                )
                continue
            held = next((c for c in _bound_codes(entry) if c in code_holder), None)
            if held is not None:
                dropped.append(
                    DroppedEntry(key, f"code {held[1]} is already bound to {code_holder[held]}")
                )
                continue
            kept.append(entry)
            code_holder.update(dict.fromkeys(_bound_codes(entry), key))
            kept_preferred.add(collision_key(entry["preferred_term"]))
            kept_synonyms.update(collision_key(t) for t in _synonym_terms(entry))
    except (KeyError, TypeError) as exc:
        raise DatasetShapeError(f"an entry is missing a field ({type(exc).__name__})") from exc

    return {**document, "entries": kept}, dropped


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise DatasetShapeError("the dataset is not a JSON object")
        filtered, dropped = filter_dataset(document)
    except (OSError, json.JSONDecodeError, DatasetShapeError) as exc:
        print(f"error: cannot filter {args.input} ({type(exc).__name__}: {exc})", file=sys.stderr)
        return EXIT_INPUT_REFUSED

    for item in dropped:
        print(f"dropped {item.business_key}: {item.reason}")
    if not filtered["entries"]:
        print("error: no entry survived the filter; nothing written", file=sys.stderr)
        return EXIT_NOTHING_LEFT

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(filtered, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"kept {len(filtered['entries'])} of {len(filtered['entries']) + len(dropped)} entries")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
