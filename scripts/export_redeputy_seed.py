"""Export a small Re-Deputy roster sample into On Track's neutral JSON contract.

This developer tool opens the supplied SQLite database in read-only mode. It has
no Re-Deputy imports and never exports authentication or integration data.
Names are sanitized unless --keep-names is explicitly supplied.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

POSITION_ALIASES = {
    "gimble": "Gimbal",
    "steadi": "Steady",
    "steadicam": "Steady",
}


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _position(value: object) -> str:
    clean = " ".join(str(value or "").split())
    return POSITION_ALIASES.get(clean.casefold(), clean)


def _tables(db: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def export_bundle(
    database: Path,
    *,
    region: str = "Northern",
    limit: int = 20,
    keep_names: bool = False,
) -> dict[str, object]:
    uri = database.resolve().as_uri().replace("file:///", "file:/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = _tables(db)
        required = {"crew_people", "roster_day_assignments"}
        missing = required - tables
        if missing:
            raise ValueError(f"Re-Deputy database is missing required tables: {', '.join(sorted(missing))}")

        assignments = db.execute(
            """
            SELECT assignee_label, position_label, COUNT(*) AS uses
            FROM roster_day_assignments
            WHERE TRIM(COALESCE(assignee_label, '')) <> ''
              AND TRIM(COALESCE(position_label, '')) <> ''
            GROUP BY assignee_label, position_label
            ORDER BY uses DESC, assignee_label, position_label
            """
        ).fetchall()
        people_rows = db.execute(
            """
            SELECT id, canonical_display_name
            FROM crew_people
            WHERE is_active = 1 AND TRIM(canonical_display_name) <> ''
            ORDER BY canonical_display_name
            """
        ).fetchall()

        assignment_counts = Counter(_key(row["assignee_label"]) for row in assignments)
        ranked = sorted(
            people_rows,
            key=lambda row: (-assignment_counts[_key(row["canonical_display_name"])], str(row["canonical_display_name"])),
        )[:limit]
        selected = {_key(row["canonical_display_name"]): row for row in ranked}
        worked: dict[str, set[str]] = defaultdict(set)
        for row in assignments:
            person_key = _key(row["assignee_label"])
            position = _position(row["position_label"])
            if person_key in selected and position:
                worked[person_key].add(position)

        catalogue: set[str] = set()
        if "workday_role_catalogue" in tables:
            catalogue.update(
                _position(row[0])
                for row in db.execute(
                    "SELECT display_label FROM workday_role_catalogue WHERE is_active = 1"
                ).fetchall()
            )
        catalogue.update(position for values in worked.values() for position in values)
        catalogue.discard("")

        group_name = "Operational Crew"
        people: list[dict[str, object]] = []
        for index, row in enumerate(ranked, start=1):
            source_name = str(row["canonical_display_name"])
            display_name = source_name if keep_names else f"Staging Crew {index:02d}"
            email = None if keep_names else f"staging.crew{index:02d}@example.invalid"
            person_key = _key(source_name)
            people.append(
                {
                    "display_name": display_name,
                    "email": email,
                    "home_region": region,
                    "crew_groups": [group_name],
                    "capabilities": [
                        {"crew_group": group_name, "position": name, "signal": "WORKED"}
                        for name in sorted(worked[person_key])
                    ],
                }
            )

    return {
        "version": "1",
        "regions": [{"name": region}],
        "tracks": [],
        "crew_groups": [{"name": group_name}],
        "positions": [
            {"name": name, "crew_group": group_name} for name in sorted(catalogue)
        ],
        "people": people,
        "external_track_mappings": [],
        "external_events": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--region", default="Northern")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--keep-names",
        action="store_true",
        help="Include source display names; never use this for a committed fixture.",
    )
    args = parser.parse_args()
    if not args.database.is_file() or args.database.stat().st_size == 0:
        parser.error("--database must be a non-empty Re-Deputy SQLite database")
    if args.database.resolve() == args.output.resolve():
        parser.error("--output must not overwrite the source database")
    if not 1 <= args.limit <= 25:
        parser.error("--limit must be between 1 and 25")
    bundle = export_bundle(
        args.database, region=args.region, limit=args.limit, keep_names=args.keep_names
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {len(bundle['positions'])} positions and {len(bundle['people'])} people "
        f"to {args.output} ({'source names' if args.keep_names else 'sanitized'})."
    )


if __name__ == "__main__":
    main()
