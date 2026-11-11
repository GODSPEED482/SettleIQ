"""Shared CSV reading for discovery and file ingestion (spec 7.1, 10.3.1).

Discovery profiles the stored copy of a file, and ingestion turns the same stored bytes into
records. Both use this parser so a file is read the same way at every stage: UTF-8 with an
optional BOM, a header of distinct named columns, a sniffed delimiter and equal-width rows.
Unknown columns are kept; empty lines are ignored.
"""

import csv
import io

from app.errors import conflict

CSV_CONTENT_TYPES = ("text/csv", "application/csv")


def read_csv(data: bytes) -> tuple[list[str], list[list[str]], str]:
    """Header, data rows and delimiter of one UTF-8 CSV file."""
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise conflict("CSV_UNREADABLE", "A file is not UTF-8 encoded CSV.") from None
    try:
        delimiter = csv.Sniffer().sniff(decoded[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    reader = csv.reader(io.StringIO(decoded), delimiter=delimiter)
    header = [name.strip() for name in next(reader, [])]
    if not header or "" in header or len(set(header)) != len(header):
        raise conflict("CSV_HEADER_INVALID", "A file needs a header of distinct, named columns.")
    width = len(header)
    rows = [(row + [""] * width)[:width] for row in reader if any(cell.strip() for cell in row)]
    return header, rows, delimiter
