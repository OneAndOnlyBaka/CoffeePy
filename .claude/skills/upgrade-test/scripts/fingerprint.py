#!/usr/bin/env python3
"""A fingerprint of every table in the Pi's database, to prove an update lost nothing.

    fingerprint.py OUT.json              snapshot (the baseline, before the first update)
    fingerprint.py OUT.json BASE.json    snapshot, compared with BASE - exit 1 on a loss

Per table: the row count, a hash of all rows, and - for tables with an integer id - the
highest id and a hash of the rows up to it. Compared with a baseline, the comparison is taken
over the *baseline's* columns and ids, so a migration that adds a column or new rows (the
update's own audit entries) doesn't count as a change, while an old row that changed or
vanished does. Only hashes leave the device - no member data in the scratchpad.

Rows are hashed as Postgres renders them (row(...)::text) - stable across PostgreSQL major
versions for the types used here, so the same comparison also covers a PG upgrade.
"""
import json
import subprocess
import sys
from pathlib import Path

PI = Path(__file__).resolve().parents[2] / "pi-device" / "scripts" / "pi.sh"
PSQL = ("cd /opt/coffeepy && sudo docker compose exec -T db sh -c "
        "'psql -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\" -X -q -At -F \"|\" -v ON_ERROR_STOP=1'")
# Expected to change through normal operation (or the update itself), not data a member owns.
VOLATILE = {"django_session", "django_migrations", "coffee_nfcscan", "coffee_nfcreaderstatus"}


def psql(sql):
    out = subprocess.run([str(PI), PSQL], input=sql, capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"psql failed: {out.stderr.strip()}")
    return [line.split("|") for line in out.stdout.splitlines() if line]


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def digest(cols, where=""):
    row = f"row({', '.join(f't.{ident(c)}' for c in cols)})::text"
    return f"md5(coalesce(string_agg({row}, E'\\n' order by {row}){where}, ''))"


def snapshot(base):
    columns = {}
    for table, column, kind in psql(
            "select c.table_name, c.column_name, c.data_type from information_schema.columns c "
            "join information_schema.tables t using (table_schema, table_name) "
            "where c.table_schema = 'public' and t.table_type = 'BASE TABLE' "
            "order by c.table_name, c.ordinal_position;"):
        columns.setdefault(table, {})[column] = kind
    tables, queries = sorted(columns), []
    for table in tables:
        before = (base or {}).get(table)

        # Its own hash over all its columns (what a later run compares with, when this file is
        # the baseline), and the comparison over the baseline's - the two differ after a migration.
        own = digest(columns[table])
        has_id = columns[table].get("id") in ("integer", "bigint", "smallint")
        cmp = cmp_upto = "null"
        if before:
            cols = [c for c in before["columns"] if c in columns[table]]
            cmp = digest(cols)
            if has_id and before["max_id"] is not None:
                cmp_upto = digest(cols, f" filter (where t.id <= {int(before['max_id'])})")
        queries.append(f"select count(*), {own}, {'max(t.id)' if has_id else 'null'}, {cmp}, {cmp_upto} "
                       f"from {ident(table)} t;")
    result = {}
    for table, (count, own, max_id, cmp, cmp_upto) in zip(tables, psql("\n".join(queries))):
        result[table] = {"count": int(count), "md5": own, "max_id": int(max_id) if max_id else None,
                         "cmp": cmp or None, "cmp_upto": cmp_upto or None, "columns": list(columns[table])}
    return result


def compare(base, now):
    lost, lines = False, []
    for table in sorted(set(base) | set(now)):
        b, n = base.get(table), now.get(table)
        if n is None:
            lines.append(f"  {table:40} GONE (had {b['count']} rows)")
            lost |= b["count"] > 0 and table not in VOLATILE
            continue
        if b is None:
            lines.append(f"  {table:40} new table, {n['count']} rows")
            continue
        dropped = [c for c in b["columns"] if c not in n["columns"]]
        if table in VOLATILE:
            verdict = "volatile"
        elif b["max_id"] is not None and n["max_id"] is not None:
            # Rows up to the baseline's highest id, hashed over the baseline's columns.
            same = n["cmp_upto"] == b["md5"] and n["count"] >= b["count"]
            verdict = "old rows unchanged" if same else "OLD ROWS CHANGED OR MISSING"
            lost |= not same
        else:
            # No id to tell old rows from new ones: on a device left idle during the test,
            # any difference is one to explain.
            same = n["cmp"] == b["md5"]
            verdict = "unchanged" if same else ("CHANGED - explain" if n["count"] >= b["count"] else "ROWS MISSING")
            lost |= not same
        if dropped:
            verdict += f", columns dropped: {', '.join(dropped)} - check the migration"
        if verdict in ("unchanged", "old rows unchanged", "volatile") and b["count"] == n["count"]:
            continue
        lines.append(f"  {table:40} {b['count']:>7} -> {n['count']:<7} {verdict}")
    print(f"{len(now)} tables, {sum(t['count'] for t in now.values())} rows; differences from the baseline:")
    print("\n".join(lines) or "  none")
    print("RESULT: DATA LOST OR CHANGED" if lost else "RESULT: nothing lost")
    return lost


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    base = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) == 3 else None
    now = snapshot(base)
    Path(sys.argv[1]).write_text(json.dumps(now, indent=1))
    if base is None:
        print(f"{len(now)} tables, {sum(t['count'] for t in now.values())} rows -> {sys.argv[1]}")
        return 0
    return 1 if compare(base, now) else 0


if __name__ == "__main__":
    sys.exit(main())
