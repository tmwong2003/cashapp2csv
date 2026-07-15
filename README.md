# `cashapp2csv`: Convert Cash App statements into CSV for personal finance apps

`cashapp2csv` extracts transaction data from Cash App PDF statements and converts it into a basic CSV format that personal finance apps such as Intuit Quicken or Banktivity can import. It:

- Writes CSV to standard output.
- Omits the header by default.
- Converts dates to `YYYY-MM-DD` (i.e., ISO-8601).
- Detects the year from the statement.
- Removes `$` and leading `+` from amounts (i.e., treats `+` amounts as credits).
- Makes unsigned transaction amounts negative (i.e., treats them as debits).
- Leaves fees as unsigned numeric values.
- Handles transaction tables spanning multiple pages.

## Installation

- [Install the `uv`](https://docs.astral.sh/uv/getting-started/installation/) Python package manager.
- Install the script: `uv sync`

## Usage

```
usage: uv cashapp2csv [-h] [--header] [--year YEAR] pdf

positional arguments:
  pdf          Cash App statement PDF

options:
  -h, --help   show this help message and exit
  --header     include Date,Description,Details,Fee,Amount as the first row
  --year YEAR  override the year detected from the statement
```

When run as:

```bash
uv run ./cashapp2csv.py --header monthly-statement.pdf
```

the script produces the following output:

```
Date,Description,Details,Fee,Amount
2026-06-01,Cash App,Monthly interest,0.00,0.88
2026-06-02,To Cash App,Transfer,0.00,-42.00
```

In general, most personal finance app importers ask you to specify the purpose of each CSV column in their UI, so you do not need to include the header (and doing so will likely confuse the importer).
