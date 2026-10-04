"""Concatenate the three migrations into one paste-ready SQL file.

The Supabase dashboard's SQL editor runs a single script, so the three
migrations have to be one file. All three are already pure SQL with no
psql meta-commands, so this is a straight concatenation with nothing
stripped and nothing rewritten: what runs in the dashboard is exactly
what is version-controlled in the repository.

    python scripts/build_single_sql.py
    # then paste supabase/apply_all.sql into Supabase -> SQL Editor -> Run
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "supabase" / "migrations"
TARGET = ROOT / "supabase" / "apply_all.sql"
FILES = [
    "0001_schema.sql",
    "0002_plpgsql.sql",
    "0003_seed.sql",
]

BANNER = """-- ============================================================================
--  apply_all.sql  --  generated file, do not edit
--
--  Single-file version of 0001_schema.sql + 0002_plpgsql.sql + 0003_seed.sql,
--  for pasting into the Supabase dashboard's SQL Editor.
--
--  Source of truth:  supabase/migrations/*.sql
--  Regenerate with:  python scripts/build_single_sql.py
--
--  This file is a concatenation. Nothing is stripped or rewritten, so
--  what runs in the dashboard is exactly what is version controlled.
--
--  Expected on completion: 33 users, 25 students, 6 instructors,
--  18 course sections, 57 enrolments, 15 assignments, 62 submissions,
--  212 attendance records.
-- ============================================================================

"""


def main() -> int:
    parts = [BANNER]

    for name in FILES:
        path = MIGRATIONS / name
        body = path.read_text(encoding="utf-8")
        # Guard against the assumption above silently breaking.
        offenders = [
            (i, line) for i, line in enumerate(body.splitlines(), 1)
            if line.lstrip().startswith("\\")
        ]
        if offenders:
            print(f"ERROR: {name} contains psql meta-commands, "
                  f"which the SQL editor will not accept:")
            for lineno, line in offenders:
                print(f"  {lineno}: {line}")
            return 1

        parts.append(f"\n-- ===== {name} "
                     f"{'=' * (66 - len(name))}\n\n{body.rstrip()}\n")

    TARGET.write_text("".join(parts), encoding="utf-8")

    total = TARGET.read_text(encoding="utf-8").count(";")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    print(f"  {total:,} statements across {len(FILES)} migrations")
    print(f"  {TARGET.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())