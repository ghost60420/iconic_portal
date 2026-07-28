#!/usr/bin/env python3
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


SKIPPED_TABLES = {"django_migrations", "sqlite_sequence"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def table_names(connection, schema="main"):
    return {
        row[0]
        for row in connection.execute(
            f"""
            SELECT name
            FROM {schema}.sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            """
        )
    }


def table_columns(connection, schema, table):
    return list(
        connection.execute(
            f"PRAGMA {quote(schema)}.table_info({quote(table)})"
        )
    )


def primary_key_columns(columns):
    return [
        row[1]
        for row in sorted(columns, key=lambda item: item[5])
        if row[5]
    ]


def join_predicate(columns, target_alias="main", source_alias="source_db"):
    return " AND ".join(
        f"{target_alias}.{quote(column)} IS {source_alias}.{quote(column)}"
        for column in columns
    )


def ensure_integrity(connection):
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result}")
    violations = list(connection.execute("PRAGMA foreign_key_check"))
    if violations:
        raise RuntimeError(
            f"Foreign-key check found {len(violations)} violation(s): {violations[:5]}"
        )


def baseline_copy(connection, state_path, source_path, source_digest):
    source_tables = table_names(connection, "source_db")
    target_tables = table_names(connection)
    shared_tables = sorted((source_tables & target_tables) - SKIPPED_TABLES)

    copy_plan = []
    for table in shared_tables:
        target_columns = table_columns(connection, "main", table)
        source_column_names = {
            row[1] for row in table_columns(connection, "source_db", table)
        }
        missing_required = [
            row[1]
            for row in target_columns
            if row[1] not in source_column_names
            and row[3]
            and row[4] is None
            and not row[5]
        ]
        if missing_required:
            raise RuntimeError(
                f"{table} is missing required baseline columns in the source: "
                f"{missing_required}"
            )
        common_columns = [
            row[1] for row in target_columns if row[1] in source_column_names
        ]
        copy_plan.append((table, common_columns))

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table, columns in copy_plan:
            quoted_columns = ", ".join(quote(column) for column in columns)
            connection.execute(f"DELETE FROM {quote(table)}")
            connection.execute(
                f"""
                INSERT INTO {quote(table)} ({quoted_columns})
                SELECT {quoted_columns}
                FROM source_db.{quote(table)}
                """
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    ensure_integrity(connection)
    state_path.write_text(
        json.dumps(
            {
                "source": str(source_path),
                "source_sha256": source_digest,
                "source_tables": sorted(source_tables),
                "baseline_target_tables": sorted(target_tables),
                "baseline_copied_tables": shared_tables,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def final_sync(connection, state, source_digest):
    if state["source_sha256"] != source_digest:
        raise RuntimeError("Source checksum changed after the baseline copy")

    source_tables = table_names(connection, "source_db")
    target_tables = table_names(connection)
    shared_tables = sorted((source_tables & target_tables) - SKIPPED_TABLES)

    sync_plan = []
    for table in shared_tables:
        target_columns = table_columns(connection, "main", table)
        source_column_names = {
            row[1] for row in table_columns(connection, "source_db", table)
        }
        key_columns = primary_key_columns(target_columns)
        if not key_columns:
            raise RuntimeError(f"{table} has no primary key; refusing automatic sync")
        common_columns = [
            row[1] for row in target_columns if row[1] in source_column_names
        ]
        non_key_columns = [
            column for column in common_columns if column not in key_columns
        ]
        missing_required = [
            row[1]
            for row in target_columns
            if row[1] not in source_column_names
            and row[3]
            and row[4] is None
            and not row[5]
        ]
        predicate = join_predicate(key_columns, "target", "source")
        missing_rows = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM source_db.{quote(table)} AS source
            WHERE NOT EXISTS (
                SELECT 1 FROM {quote(table)} AS target WHERE {predicate}
            )
            """
        ).fetchone()[0]
        if missing_rows and missing_required:
            raise RuntimeError(
                f"{table} has {missing_rows} source row(s) requiring unmapped "
                f"columns: {missing_required}"
            )
        sync_plan.append(
            (table, key_columns, common_columns, non_key_columns, missing_rows)
        )

    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN IMMEDIATE")
    try:
        for table, key_columns, common_columns, non_key_columns, missing_rows in sync_plan:
            predicate = join_predicate(key_columns, "target", "source")
            if non_key_columns:
                assignments = ", ".join(
                    f"{quote(column)} = ("
                    f"SELECT source.{quote(column)} "
                    f"FROM source_db.{quote(table)} AS source "
                    f"WHERE {predicate})"
                    for column in non_key_columns
                )
                connection.execute(
                    f"""
                    UPDATE {quote(table)} AS target
                    SET {assignments}
                    WHERE EXISTS (
                        SELECT 1
                        FROM source_db.{quote(table)} AS source
                        WHERE {predicate}
                    )
                    """
                )
            if missing_rows:
                quoted_columns = ", ".join(
                    quote(column) for column in common_columns
                )
                source_predicate = join_predicate(
                    key_columns, "target", "source"
                )
                connection.execute(
                    f"""
                    INSERT INTO {quote(table)} ({quoted_columns})
                    SELECT {", ".join(f"source.{quote(column)}" for column in common_columns)}
                    FROM source_db.{quote(table)} AS source
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM {quote(table)} AS target
                        WHERE {source_predicate}
                    )
                    """
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")

    ensure_integrity(connection)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("baseline", "finalize"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--target", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--expected-source-sha256", required=True)
    args = parser.parse_args()

    source_path = args.source.expanduser().resolve()
    target_path = args.target.expanduser().resolve()
    state_path = args.state.expanduser().resolve()
    if source_path == target_path:
        raise RuntimeError("Source and target database paths must differ")
    if not source_path.is_file() or not target_path.is_file():
        raise RuntimeError("Source and target databases must already exist")

    source_digest = sha256(source_path)
    if source_digest != args.expected_source_sha256:
        raise RuntimeError("Source checksum does not match the expected checksum")

    connection = sqlite3.connect(target_path)
    try:
        connection.execute("ATTACH DATABASE ? AS source_db", (str(source_path),))
        if args.phase == "baseline":
            baseline_copy(
                connection, state_path, source_path, source_digest
            )
        else:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            final_sync(connection, state, source_digest)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
