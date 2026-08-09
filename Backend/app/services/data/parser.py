"""Minimal ingestion for Phase B3 (specs/04): CSV -> defensive clean -> a
queryable per-user data table the SQL layer (B2) executes against.

CSV is the *minimum* for this phase. XLSX/PDF are still valid upload types per
the validator, but their parsing is deferred ("XLSX next, PDF later" per the
planning master B3), so uploading them lands a FileUpload row with
status="failed" and a stored reason. Pinecone/embeddings are skipped entirely
this pass - the parsed table is queried directly.

The table is created with the exact name `user_data_table_name(user_id)`
produces (the B2<->B3 co-design contract), so user-scoping stays structural:
a new upload drops and recreates that user's table (one data file at a time is
the agreed initial scope, specs/04 §4).
"""
import io
import os
import re

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.data.executor import user_data_table_name

CSV_ENCODINGS = ("utf-8-sig", "utf-8", "latin-1")


# --- parsing ---------------------------------------------------------------

def parse_csv_bytes(content: bytes) -> pd.DataFrame:
    """Read a CSV into a DataFrame, tolerating encodings and ragged rows.

    - Encoding: BOM-tolerant UTF-8 first, latin-1 as the catch-all fallback.
    - Ragged rows do not crash parsing (pandas NaN-pads / skips them here);
      a truly empty file raises so we never ingest an empty dataset (specs/04
      edge case 4 pairs with the validator's 0-byte 400).
    """
    raw = io.BytesIO(content)
    for encoding in CSV_ENCODINGS:
        raw.seek(0)
        try:
            return pd.read_csv(raw, encoding=encoding)
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            raise ValueError("CSV file is empty.")
        except pd.errors.ParserError as exc:
            raise ValueError(f"CSV could not be parsed: {exc}") from exc
    raise ValueError("CSV uses an unsupported encoding (tried UTF-8 and latin-1).")


# --- cleaning --------------------------------------------------------------

def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Defensive cleaning pass (specs/04 edge case 2).

    - Normalizes column names to safe lowercase snake_case (the per-user table's
      columns must be addressable from generated SQL without quoting).
    - Drops fully-empty rows and full duplicate rows.
    - Coerces object columns that mostly look like dates or numbers (incl.
      Indian `₹`/`,` formats) to typed columns so the table has real types.
    """
    df = df.copy()
    df.columns = _normalize_columns(df.columns)
    df = df.dropna(how="all")
    df = df.drop_duplicates()
    df = _coerce_dates(df)
    df = _coerce_numeric(df)
    return df


def _normalize_columns(columns) -> list[str]:
    out: list[str] = []
    seen: dict[str, int] = {}
    for name in columns:
        ident = re.sub(r"[^0-9a-z_]", "_", str(name).strip().lower())
        ident = re.sub(r"_+", "_", ident).strip("_")
        if not ident:
            ident = "column"
        if ident[0].isdigit():
            ident = "col_" + ident
        n = seen.get(ident, 0)
        seen[ident] = n + 1
        if n:
            ident = f"{ident}_{n}"
        out.append(ident)
    return out


def _coerce_dates(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if not _is_text_dtype(df[col]):
            continue
        probe = pd.to_datetime(df[col], errors="coerce")
        present = int(df[col].notna().sum())
        if present and int(probe.notna().sum()) >= max(1, int(0.5 * present)):
            df[col] = probe
    return df


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if not _is_text_dtype(df[col]):
            continue
        stripped = (
            df[col]
            .astype(str)
            .str.strip()
            .str.replace(r"^\s*[₹$\s]+", "", regex=True)
            .str.replace(",", "", regex=False)
        )
        probe = pd.to_numeric(stripped, errors="coerce")
        present = int(df[col].notna().sum())
        if present and int(probe.notna().sum()) >= max(1, int(0.5 * present)):
            df[col] = probe
    return df


def _is_text_dtype(series: pd.Series) -> bool:
    """True for columns pandas keeps as strings (object-dtype in older pandas,
    the new `str` dtype in pandas 3). Numeric/date columns are already typed."""
    return pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(
        series
    )


# --- landing into the per-user table ----------------------------------------

async def ingest_file(db: AsyncSession, user_id, filename: str, content: bytes) -> str:
    """Parse + clean `content` and (re)create the user's data table.

    Returns the per-user table name (the reference stored on the FileUpload).
    Raises ValueError for anything that makes the file un-ingestable so the
    route can record status="failed" with a stored reason.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext != ".csv":
        raise ValueError(
            f"{ext.upper() or 'unsupported'} parsing is not supported yet - "
            "CSV is the minimum for this phase."
        )

    df = clean_dataframe(parse_csv_bytes(content))
    if df.empty:
        raise ValueError("CSV contains no data rows to ingest.")

    return await upsert_user_table(db, user_id, df)


async def upsert_user_table(db: AsyncSession, user_id, df: pd.DataFrame) -> str:
    """Drop-and-recreate the user's data table with the parsed rows.

    The table is per-user (user_data_table_name), so a fresh upload replaces
    the user's previous data (agreed one-file-at-a-time scope, specs/04 §4).
    """
    table_name = user_data_table_name(user_id)

    columns = [
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        *[
            sa.Column(name, col_type, nullable=True)
            for name, col_type in zip(df.columns, _sqlalchemy_types(df))
        ],
    ]
    table = sa.Table(table_name, sa.MetaData(), *columns)

    await db.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
    await db.run_sync(lambda sync_conn: table.create(sync_conn.connection(), checkfirst=True))

    records = [
        {col: _py_scalar(value) for col, value in row.items()}
        for _, row in df.iterrows()
    ]
    if records:
        await db.execute(table.insert(), records)

    return table_name


def _sqlalchemy_types(df: pd.DataFrame) -> list:
    types = []
    for col in df.columns:
        dtype = df[col]
        if pd.api.types.is_bool_dtype(dtype):
            types.append(sa.Boolean)
        elif pd.api.types.is_integer_dtype(dtype):
            types.append(sa.Integer)
        elif pd.api.types.is_float_dtype(dtype):
            types.append(sa.Float)
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            types.append(sa.DateTime)
        else:
            types.append(sa.Text)
    return types


def _py_scalar(value):
    """Normalize a cell's value to something bind-param safe (no NaN/pd.NA)."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if value is None:
        return None
    if isinstance(value, float) and np.isnan(value):
        return None
    if value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value