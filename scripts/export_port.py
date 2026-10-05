"""
Export every SQL statement and every public function to ``port/``.

This exists for the JavaScript rewrite (see HANDOFF.md and the handoff
brief). Nothing here modifies the application: it reads the Python
source with ``ast`` and writes copies of the SQL and a function index
into ``port/``, which is git-ignored. The Python app is unaffected and
``port/`` can be deleted at any time.

Why bother. Every SQL statement in this project lives inside a Python
string literal, spread across five files. The rewrite needs those
statements as files it can copy rather than re-transcribe from Python
source, and it needs an exact inventory of what has to be ported so
nothing is missed.

Run
    python scripts/export_port.py

Writes
    port/sql/NNN_slug.sql      one file per statement, verbatim
    port/sql/manifest.json     where each statement came from
    port/PORT_MAP.md           function-by-function port checklist
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "port"
SQL_DIR = OUT / "sql"

# Module path -> the TypeScript file that will replace it.
TARGETS = {
    "src/db.py": "lib/db.ts",
    "src/auth.py": "lib/auth.ts  (+ NEW lib/session.ts)",
    "src/crud/__init__.py": "lib/queries/*.ts",
    "src/reports.py": "lib/reports.ts",
    "src/screens.py": "app/(app)/*/page.tsx",
    "app.py": "app/layout.tsx, app/(app)/layout.tsx, app/(auth)/login/page.tsx",
}

# Functions that take a SQL string as their first positional argument.
SQL_FUNCS = {"query_df", "query_scalar", "execute", "call_procedure"}

# Screen functions in screens.py -> the page they become.
SCREEN_TARGETS = {
    "dashboard": "app/(app)/dashboard/page.tsx",
    "students_crud": "app/(app)/students/page.tsx",
    "instructors_crud": "app/(app)/instructors/page.tsx",
    "sections_crud": "app/(app)/sections/page.tsx",
    "enrollments_crud": "app/(app)/enrollments/page.tsx",
    "assignments_crud": "app/(app)/assignments/page.tsx",
    "attendance_crud": "app/(app)/attendance/page.tsx",
    "reports_screen": "app/(app)/reports/page.tsx",
    "admin_users": "app/(app)/admin/page.tsx",
}

# Name -> intended TypeScript module, for functions that move somewhere
# other than the mechanical module-for-module mapping.
FUNCTION_TARGETS = {
    # db.py
    "query_df": "lib/db.ts -> query<T>()",
    "query_scalar": "lib/db.ts -> queryOne<T>()",
    "execute": "lib/db.ts -> exec()",
    "call_procedure": "lib/db.ts -> callProcedure()  [KEEP ALLOWLIST]",
    "table_count": "lib/db.ts -> tableCount()  [KEEP ALLOWLIST]",
    "ping": "lib/db.ts -> ping()",
    "get_connection": "lib/db.ts -> withActor()  [see LANDMINE app.user]",
    "setting": "lib/db.ts -> process.env only  [3 sources collapse to 1]",
    "_coerce": "DELETE  [pg returns NUMERIC as string, not Decimal]",
    "dsn_fingerprint": "lib/db.ts -> verbatim",
    "_normalise_dsn": "lib/db.ts -> verbatim",
    "_redact": "lib/db.ts -> verbatim",
    "_friendly_error": "lib/db.ts -> err.code, not err.pgcode",
    "db_target": "lib/db.ts",
    "_connection_string": "lib/db.ts",
    # auth.py
    "hash_password": "lib/auth.ts  [DECIDE D1: bcryptjs vs scrypt]",
    "verify_password": "lib/auth.ts  [DECIDE D1]",
    "password_problems": "lib/auth.ts -> verbatim",
    "authenticate": "lib/auth.ts -> verbatim  [keep decoy hash + identical msg]",
    "register": "lib/auth.ts -> verbatim",
    "require_role": "lib/auth.ts -> verbatim  [admin passes every gate]",
    "visible_role_names": "lib/auth.ts",
    "describe": "lib/auth.ts",
    "lookup_user": "lib/auth.ts",
    # app.py
    "_gate": "lib/nav.ts -> gate()  [server-side, not UI-only]",
    "flash": "components/Flash.tsx  [cookie or ?msg= param]",
    "show_flash": "components/Flash.tsx",
    "render_sidebar": "app/(app)/layout.tsx",
    "render_login": "app/(auth)/login/page.tsx",
    "render_db_status": "app/(app)/layout.tsx",
    "main": "app/layout.tsx",
    "init_state": "DELETE  [no st.session_state; cookies replace it]",
    # screens.py
    "_options": "components/EntityPicker.tsx",
    "_pick": "components/EntityPicker.tsx",
    "_show_table": "components/DataTable.tsx",
    "_flash": "components/Flash.tsx",
    "_departments": "lib/queries/departments.ts",
}

# Tests that must be reproduced, and their JS replacement.
TEST_TARGETS = {
    "scripts/smoke_test.py": ("34 checks", "tests/data.test.ts (Vitest)"),
    "scripts/app_test.py": ("59 checks, streamlit.testing.v1.AppTest",
                            "tests/e2e/*.spec.ts (Playwright)  [NO DROP-IN]"),
    "scripts/verify_config.py": ("18 checks", "tests/config.test.ts (Vitest)"),
    "scripts/verify_hardening.py": ("9 checks", "tests/hardening.test.ts (Vitest)"),
    "scripts/test_neon.py": ("8 checks", "tests/cloud.test.ts (Vitest)"),
    "scripts/check_dsn.py": ("CLI", "scripts/check-dsn.ts (tsx)"),
    "scripts/dsn_fingerprint.py": ("CLI", "scripts/dsn-fingerprint.ts (tsx)"),
    "scripts/apply_to_cloud.py": ("CLI", "scripts/apply-migrations.ts (tsx)"),
    "scripts/capture_screens.py": ("screenshots", "scripts/capture-screens.ts"),
    "scripts/build_single_sql.py": ("concatenates migrations", "DELETE (db/apply_all.sql already exists)"),
    "scripts/isolate_auth.py": ("one-off diagnostic", "DELETE"),
    "scripts/probe_supabase.py": ("Supabase probe, provider abandoned", "DELETE"),
    "scripts/inspect_env.py": ("one-off diagnostic", "DELETE"),
    "scripts/capture_output.sql": ("psql input", "DELETE"),
}


# ---------------------------------------------------------------------------
# SQL extraction
# ---------------------------------------------------------------------------

def call_name(node: ast.Call) -> str:
    """The bare function name of a call, for ``db.query_df(...)`` and ``query_df(...)``."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def render_sql(node: ast.AST) -> tuple[str, bool]:
    """Return the statement text and whether it interpolates Python.

    A plain literal comes back verbatim. An f-string comes back as a
    template with each interpolation written as ``{{ expr }}``, so the
    static skeleton survives while the dynamic parts stay visible --
    the rewrite has to rebuild those as real parameter binding.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False

    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = ast.unparse(value.value)
                parts.append("{{ " + inner + " }}")
        return "".join(parts), True

    return "", False


def statement_kind(sql: str) -> str:
    head = re.sub(r"^\s*(f?\"\"\"|f?'''|f?\")", "", sql).lstrip()
    head = head.upper()
    for kind in ("SELECT", "INSERT", "UPDATE", "DELETE", "CALL", "WITH"):
        if head.startswith(kind):
            return kind
    return "OTHER"


def slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text)
    stop = {"FROM", "WHERE", "SELECT", "AND", "THE", "INTO", "SET",
            "VALUES", "JOIN", "ON", "GROUP", "ORDER", "BY", "AS"}
    kept = [w.lower() for w in words if w.upper() not in stop][:6]
    return "_".join(kept) or "statement"


def enclosing_function(node: ast.AST, parents: dict) -> str:
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
    return "<module>"


def collect_sql_constants(tree: ast.AST) -> list[dict]:
    """Find SQL held in a named constant rather than inline in a call.

    ``src/reports.py`` keeps its eight analytical queries in module
    constants (``Q1_ENROLMENT_PRESSURE`` and friends) which the
    ``REPORTS`` registry then looks up by key, so nothing there appears
    as a call argument and a call-walking pass alone finds none of them.
    Scanning assignments as well catches that shape, and any future
    refactor that hoists a query into a constant.
    """
    found: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else node.targets)
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not names or node.value is None:
            continue
        sql, interpolated = render_sql(node.value)
        if not looks_like_sql(sql):
            continue
        found.append({
            "function": names[0],
            "callee": "constant",
            "line": node.lineno,
            "sql": sql,
            "interpolates_python": interpolated,
            "kind": statement_kind(sql),
            "params": 0,
        })
    return found


def looks_like_sql(text: str) -> bool:
    """True when the text begins like a statement rather than a message."""
    head = re.sub(r"^\s*(f?\"\"\"|f?'''|f?\")", "", text).lstrip().upper()
    return head.startswith(
        ("SELECT", "INSERT", "UPDATE", "DELETE", "CALL", "WITH"))


def collect_statements(tree: ast.AST, parents: dict) -> list[dict]:
    found: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name not in SQL_FUNCS or not node.args:
            continue
        sql, interpolated = render_sql(node.args[0])
        if not sql.strip():
            continue
        found.append({
            "function": enclosing_function(node, parents),
            "callee": name,
            "line": node.lineno,
            "sql": sql,
            "interpolates_python": interpolated,
            "kind": statement_kind(sql),
            "params": len([a for a in node.args[1:]]),
        })
    found.sort(key=lambda r: r["line"])
    return found


# ---------------------------------------------------------------------------
# Function inventory
# ---------------------------------------------------------------------------

def collect_functions(tree: ast.AST) -> list[dict]:
    entries: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(getattr(node, "parent", None), ast.ClassDef):
            continue
        entries.append({
            "name": node.name,
            "line": node.lineno,
            "end_line": node.end_lineno or node.lineno,
            "loc": (node.end_lineno or node.lineno) - node.lineno + 1,
            "docstring": bool(ast.get_docstring(node)),
            "args": len(node.args.args),
        })
    entries.sort(key=lambda r: r["line"])
    return entries


def index_source(path: Path) -> tuple[ast.AST, dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents: dict = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return tree, parents


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_port_map(module_stats: list[dict], test_stats: list[dict],
                   sql_total: int) -> None:
    lines: list[str] = []
    add = lines.append

    add("# Port map: Python -> TypeScript")
    add("")
    add("Generated by `scripts/export_port.py`. Do not hand-edit; re-run it.")
    add("")
    add(f"- SQL statements exported: **{sql_total}**")
    add(f"- Python lines to port: **{sum(m['loc'] for m in module_stats)}**")
    add("")
    add("Read `handoff` section 7 (landmines) before starting. Section 8 is")
    add("the acceptance checklist. Section 4 holds the four blocking decisions.")
    add("")

    add("## Module map")
    add("")
    add("| Python | Lines | TypeScript |")
    add("|---|---:|---|")
    for mod in module_stats:
        add(f"| `{mod['path']}` | {mod['loc']} | `{mod['target']}` |")
    add("")

    add("## Functions")
    add("")
    for mod in module_stats:
        if not mod["functions"]:
            continue
        add(f"### `{mod['path']}` -> `{mod['target']}`")
        add("")
        add("| Function | Line | LOC | Goes to |")
        add("|---|---:|---:|---|")
        for fn in mod["functions"]:
            if fn["name"] in SCREEN_TARGETS:
                dest = SCREEN_TARGETS[fn["name"]]
            else:
                dest = FUNCTION_TARGETS.get(fn["name"], "")
            add(f"| `{fn['name']}` | {fn['line']} | {fn['loc']} | "
                f"{dest if dest else '_mechanical_'} |")
        add("")

    add("## Tests to reproduce")
    add("")
    add("| Script | Covers | JS replacement |")
    add("|---|---|---|")
    for test in test_stats:
        add(f"| `{test['path']}` | {test['covers']} | `{test['target']}` |")
    add("")

    add("## Do not port")
    add("")
    add("| Path | Why |")
    add("|---|---|")
    for path, why in [
        ("`src/screens.py::_options`, `_pick`",
         "folded into `<EntityPicker>`"),
        ("`src/db.py::_coerce`", "pg returns NUMERIC as string, not Decimal"),
        ("`src/db.py::setting` (secrets branch)",
         "Vercel injects everything into process.env"),
        ("`app.py::init_state`", "no st.session_state; cookies replace it"),
        ("`neon.ts`, `hello.ts`, `package.json`",
         "Neon Functions scaffold, git-ignored, wrong deploy target"),
        ("`.streamlit/`", "Streamlit only"),
    ]:
        add(f"| {path} | {why} |")
    add("")

    (OUT / "PORT_MAP.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    SQL_DIR.mkdir(exist_ok=True)

    # Clear previous output so renames do not leave stale files behind.
    for stale in SQL_DIR.glob("*.sql"):
        stale.unlink()

    sources = [
        "src/db.py", "src/auth.py", "src/crud/__init__.py",
        "src/reports.py", "src/screens.py", "app.py",
    ]

    module_stats: list[dict] = []
    manifest: list[dict] = []
    counter = 0

    for rel in sources:
        path = ROOT / rel
        if not path.exists():
            print(f"  skip {rel} (missing)")
            continue
        text = path.read_text(encoding="utf-8")
        tree, parents = index_source(path)
        statements = collect_statements(tree, parents)

        # Constants first, then inline call literals. Dedupe on the SQL
        # text so a query that is both hoisted into a name and passed
        # to a call is exported once.
        seen: set[str] = set()
        merged: list[dict] = []
        for stmt in (collect_sql_constants(tree) + statements):
            fingerprint = re.sub(r"\s+", " ", stmt["sql"]).strip().lower()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            merged.append(stmt)
        merged.sort(key=lambda r: r["line"])
        statements = merged

        for stmt in statements:
            counter += 1
            name = f"{counter:03d}_{stmt['function']}_{slug(stmt['sql'])}.sql"
            target = SQL_DIR / name
            header = (
                f"-- {rel}:{stmt['line']}  fn={stmt['function']}  "
                f"via db.{stmt['callee']}()\n"
                f"-- kind={stmt['kind']}  "
                f"interpolates_python={stmt['interpolates_python']}\n"
                f"-- COPIED VERBATIM from the Python source. Do not edit by\n"
                f"-- hand; re-run scripts/export_port.py instead.\n\n"
            )
            target.write_text(header + stmt["sql"].strip() + "\n",
                              encoding="utf-8")
            manifest.append({
                "file": name,
                "source": f"{rel}:{stmt['line']}",
                "function": stmt["function"],
                "callee": stmt["callee"],
                "kind": stmt["kind"],
                "interpolates_python": stmt["interpolates_python"],
                "argument_count": stmt["params"],
            })

        module_stats.append({
            "path": rel,
            "loc": len(text.splitlines()),
            "target": TARGETS.get(rel, ""),
            "functions": collect_functions(tree),
            "statement_count": len(statements),
        })

    test_stats = []
    for rel, (covers, target) in TEST_TARGETS.items():
        path = ROOT / rel
        if not path.exists():
            continue
        test_stats.append({
            "path": rel,
            "loc": len(path.read_text(encoding="utf-8").splitlines()),
            "covers": covers,
            "target": target,
        })

    (SQL_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    write_port_map(module_stats, test_stats, len(manifest))

    templated = sum(1 for m in manifest if m["interpolates_python"])
    print(f"  {len(manifest)} SQL statements -> port/sql/")
    print(f"  {templated} of them interpolate Python and need real "
          f"parameter binding in TypeScript")
    print(f"  {sum(len(m['functions']) for m in module_stats)} functions "
          f"-> port/PORT_MAP.md")
    print(f"  {sum(m['loc'] for m in module_stats)} Python lines indexed")
    print("\n  port/ is git-ignored. Delete it freely.")


if __name__ == "__main__":
    main()