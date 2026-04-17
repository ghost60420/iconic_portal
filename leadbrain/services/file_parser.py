import csv
import os
import re

from openpyxl import load_workbook

try:
    import xlrd
except Exception:
    xlrd = None


HEADER_ALIASES = {
    "company": "company_name",
    "company name": "company_name",
    "business name": "company_name",
    "name": "company_name",
    "website": "website",
    "url": "website",
    "web": "website",
    "email": "email",
    "e mail": "email",
    "phone": "phone",
    "mobile": "phone",
    "telephone": "phone",
    "country": "country",
    "city": "city",
    "linkedin": "linkedin_url",
    "linkedin url": "linkedin_url",
    "notes": "notes",
}


def _normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_header(value):
    text = _normalize_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_headers(headers):
    mapping = {}
    for index, header in enumerate(headers or []):
        normalized = _normalize_header(header)
        mapping[index] = HEADER_ALIASES.get(normalized, normalized.replace(" ", "_"))
    return mapping


def _normalize_website(value):
    website = _normalize_text(value)
    if not website:
        return ""
    if "://" not in website and "." in website and " " not in website:
        website = f"https://{website}"
    return website[:200]


def _clean_email(value):
    return _normalize_text(value).lower()


def extract_company_row(row, row_number):
    cleaned = {
        "row_number": row_number,
        "company_name": _normalize_text(row.get("company_name", "")),
        "website": _normalize_website(row.get("website", "")),
        "email": _clean_email(row.get("email", "")),
        "phone": _normalize_text(row.get("phone", "")),
        "country": _normalize_text(row.get("country", "")),
        "city": _normalize_text(row.get("city", "")),
        "raw_row_json": {str(key): value for key, value in (row or {}).items()},
    }
    return cleaned


def _build_rows_from_records(records):
    if not records:
        raise ValueError("The uploaded file is empty.")

    header_map = normalize_headers(records[0])
    parsed_rows = []

    for row_number, values in enumerate(records[1:], start=1):
        row_dict = {}
        raw_row = {}
        for index, value in enumerate(values):
            header_value = records[0][index] if index < len(records[0]) else f"column_{index + 1}"
            raw_row[str(header_value)] = value
            canonical_key = header_map.get(index, f"column_{index + 1}")
            row_dict[canonical_key] = _normalize_text(value)

        if not any(_normalize_text(value) for value in row_dict.values()):
            continue

        row_dict["raw_row_json"] = raw_row
        parsed_rows.append(extract_company_row(row_dict, row_number))

    if not parsed_rows:
        raise ValueError("No usable rows were found in the uploaded file.")
    return parsed_rows


def _parse_csv(file_path):
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(file_path, "r", encoding=encoding, newline="") as handle:
                reader = csv.reader(handle)
                rows = [row for row in reader]
            return _build_rows_from_records(rows)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError("The CSV file could not be read.") from last_error


def _parse_xlsx(file_path):
    workbook = load_workbook(file_path, data_only=True)
    sheet = workbook.active
    rows = []
    for row in sheet.iter_rows(values_only=True):
        rows.append([_normalize_text(value) for value in row])
    return _build_rows_from_records(rows)


def _parse_xls(file_path):
    if xlrd is None:
        raise ValueError("XLS import is not available on this server. Please upload CSV or XLSX.")

    workbook = xlrd.open_workbook(file_path)
    sheet = workbook.sheet_by_index(0)
    rows = []
    for row_index in range(sheet.nrows):
        rows.append([_normalize_text(sheet.cell_value(row_index, col)) for col in range(sheet.ncols)])
    return _build_rows_from_records(rows)


def parse_uploaded_file(file_path):
    ext = os.path.splitext(file_path or "")[1].lower()
    if ext == ".csv":
        return _parse_csv(file_path)
    if ext == ".xlsx":
        return _parse_xlsx(file_path)
    if ext == ".xls":
        return _parse_xls(file_path)
    raise ValueError("Unsupported file type. Please upload CSV, XLSX, or XLS.")
