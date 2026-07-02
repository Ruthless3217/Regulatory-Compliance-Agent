"""Dump verbatim disclaimer text from docs/Copy of Disclaimers.xlsx so the
registry JSON files are populated by copy, not hand-transcription.

Usage: python backend/scripts/extract_disclaimer_text.py
Prints each row as `=== <Type> ===\\n<Disclaimer text>` to stdout (UTF-8).
"""
import sys
from pathlib import Path
import openpyxl

XLSX = Path(__file__).resolve().parents[2] / "docs" / "Copy of Disclaimers.xlsx"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    for type_, text in rows[1:]:  # skip header
        if not (type_ and text):
            continue
        print(f"=== {str(type_).strip()} ===")
        print(str(text).strip())
        print()


if __name__ == "__main__":
    main()
