#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "pdfplumber>=0.11,<0.12",
# ]
# ///

"""Extract Cash App statement transactions as CSV on stdout.

Usage:
    uv run cashapp_statement_to_csv.py statement.pdf > transactions.csv

By default the output has no header, dates are ISO-8601, currency symbols and
positive signs are removed, and unsigned transaction amounts are treated as
negative debits.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence, TextIO

import pdfplumber
from pdfplumber.pdf import PDF

DATE_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})$")
YEAR_RE = re.compile(r"\b(20\d{2})\b")
MONEY_RE = re.compile(r"^([+-]?)\s*\$?\s*([\d,]+(?:\.\d{2})?)$")


@dataclass(frozen=True)
class Columns:
    description: float
    details: float
    fee: float
    amount: float


@dataclass(frozen=True)
class Transaction:
    date: str
    description: str
    details: str
    fee: str
    amount: str

    def as_row(self) -> list[str]:
        return [self.date, self.description, self.details, self.fee, self.amount]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Cash App statement PDF")
    parser.add_argument(
        "--header",
        action="store_true",
        help="include Date,Description,Details,Fee,Amount as the first row",
    )
    parser.add_argument(
        "--year",
        type=int,
        help="override the year detected from the statement",
    )
    return parser.parse_args(argv)


def normalize_spaces(value: str) -> str:
    return " ".join(value.split())


def detect_year(pdf: PDF, override: int | None) -> int:
    if override is not None:
        return override
    for page in pdf.pages[:2]:
        text = page.extract_text() or ""
        match = YEAR_RE.search(text)
        if match:
            return int(match.group(1))
    raise ValueError("Could not detect statement year; pass --year YYYY")


def find_header(words: list[dict]) -> tuple[float, Columns] | None:
    """Return header baseline and x-starts for the five columns."""
    by_text: dict[str, list[dict]] = {}
    for word in words:
        by_text.setdefault(word["text"].strip().lower(), []).append(word)

    required = ["date", "description", "details", "fee", "amount"]
    if not all(name in by_text for name in required):
        return None

    # Find a set of header words sharing roughly the same vertical position.
    for date_word in by_text["date"]:
        y = float(date_word["top"])
        selected: dict[str, dict] = {"date": date_word}
        for name in required[1:]:
            candidates = [w for w in by_text[name] if abs(float(w["top"]) - y) <= 4]
            if not candidates:
                break
            selected[name] = min(candidates, key=lambda w: abs(float(w["top"]) - y))
        if len(selected) == len(required):
            # Use the midpoint of the whitespace between adjacent headers as
            # each column boundary. This includes detached signs (for example,
            # the "+" printed just to the left of an amount).
            columns = Columns(
                description=(float(selected["date"]["x1"]) + float(selected["description"]["x0"])) / 2,
                details=(float(selected["description"]["x1"]) + float(selected["details"]["x0"])) / 2,
                fee=(float(selected["details"]["x1"]) + float(selected["fee"]["x0"])) / 2,
                amount=(float(selected["fee"]["x1"]) + float(selected["amount"]["x0"])) / 2,
            )
            return max(float(w["bottom"]) for w in selected.values()), columns
    return None


def group_words_into_lines(words: Iterable[dict], tolerance: float = 3.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (float(w["top"]), float(w["x0"]))):
        top = float(word["top"])
        for line in lines:
            if abs(float(line[0]["top"]) - top) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: float(w["x0"]))
    return lines


def text_in_range(line: list[dict], left: float, right: float | None) -> str:
    selected = []
    for word in line:
        center = (float(word["x0"]) + float(word["x1"])) / 2
        if center >= left and (right is None or center < right):
            selected.append(word["text"])
    return normalize_spaces(" ".join(selected))


def parse_money(raw: str, *, debit_if_unsigned: bool) -> str:
    compact = normalize_spaces(raw)
    match = MONEY_RE.match(compact)
    if not match:
        raise ValueError(f"Unrecognized money value: {raw!r}")
    sign, digits = match.groups()
    digits = digits.replace(",", "")
    if sign == "+":
        return digits
    if sign == "-":
        return f"-{digits}"
    return f"-{digits}" if debit_if_unsigned and float(digits) != 0 else digits


def parse_date(raw: str, year: int) -> str:
    match = DATE_RE.match(normalize_spaces(raw))
    if not match:
        raise ValueError(f"Unrecognized date: {raw!r}")
    month, day = match.groups()
    return datetime.strptime(f"{year} {month} {day}", "%Y %b %d").date().isoformat()


def extract_transactions(pdf_path: Path, year_override: int | None) -> list[Transaction]:
    transactions: list[Transaction] = []
    with pdfplumber.open(pdf_path) as pdf:
        year = detect_year(pdf, year_override)
        for page_number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
            found = find_header(words)
            if found is None:
                continue
            header_bottom, columns = found
            body_words = [w for w in words if float(w["top"]) > header_bottom + 4]
            for line in group_words_into_lines(body_words):
                date_raw = text_in_range(line, 0, columns.description)
                if not DATE_RE.match(date_raw):
                    continue

                description = text_in_range(line, columns.description, columns.details)
                details = text_in_range(line, columns.details, columns.fee)
                fee_raw = text_in_range(line, columns.fee, columns.amount)
                amount_raw = text_in_range(line, columns.amount, None)

                if not all((description, details, fee_raw, amount_raw)):
                    raise ValueError(
                        f"Incomplete transaction on page {page_number}: "
                        f"{date_raw!r}, {description!r}, {details!r}, "
                        f"{fee_raw!r}, {amount_raw!r}"
                    )

                transactions.append(
                    Transaction(
                        date=parse_date(date_raw, year),
                        description=description,
                        details=details,
                        fee=parse_money(fee_raw, debit_if_unsigned=False),
                        amount=parse_money(amount_raw, debit_if_unsigned=True),
                    )
                )

    if not transactions:
        raise ValueError("No transaction table found")
    return transactions


def write_csv(transactions: Iterable[Transaction], output: TextIO, header: bool) -> None:
    writer = csv.writer(output, lineterminator="\n")
    if header:
        writer.writerow(["Date", "Description", "Details", "Fee", "Amount"])
    writer.writerows(transaction.as_row() for transaction in transactions)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        transactions = extract_transactions(args.pdf, args.year)
        write_csv(transactions, sys.stdout, args.header)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
