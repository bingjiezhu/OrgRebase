#!/usr/bin/env python3
"""Extract complete historical invoices as quote-input examples, never original quotes.

Reads the pinned UCI Online Retail XLSX directly, retaining numeric XML lexemes.
No source rows are repaired, deduplicated, or dropped from an accepted invoice.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import zipfile
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from xml.etree import ElementTree as ET

SOURCE_SHA256 = "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
EXPECTED_ROWS = 541909
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
HEADERS = ("InvoiceNo", "StockCode", "Description", "Quantity", "InvoiceDate", "UnitPrice", "CustomerID", "Country")
NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?\Z")
GOODS_CODE = re.compile(r"\d{5}[A-Za-z]{0,2}\Z")


def decimal_value(value: str) -> Decimal | None:
    """Accept only finite decimal numeric lexemes, without whitespace repair."""
    return Decimal(value) if NUMBER.fullmatch(value) else None


def iter_sheet_rows(stream, shared_strings: list[str]):
    """Yield physical row numbers and A:H values; preserve numeric XML text."""
    root = None
    for event, element in ET.iterparse(stream, events=("start", "end")):
        if root is None:
            root = element
        if event != "end" or element.tag != NS + "row":
            continue
        row_number = int(element.attrib["r"])
        values = [""] * len(HEADERS)
        for cell in element.findall(NS + "c"):
            coordinate = cell.attrib["r"]
            match = re.fullmatch(r"([A-Z]+)(\d+)", coordinate)
            if match is None or int(match.group(2)) != row_number:
                raise ValueError(f"Invalid cell coordinate: {coordinate}")
            column = match.group(1)
            if len(column) != 1 or not "A" <= column <= "H":
                if cell.find(NS + "v") is not None or cell.find(NS + "is") is not None:
                    raise ValueError(f"Unexpected nonempty column: {coordinate}")
                continue
            if cell.find(NS + "f") is not None:
                raise ValueError(f"Formula is not source data: {coordinate}")
            value = cell.findtext(NS + "v", default="")
            kind = cell.attrib.get("t", "n")
            if kind == "s":
                value = shared_strings[int(value)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(NS + "t"))
            elif kind not in {"n", "str"}:
                raise ValueError(f"Unsupported cell type {kind}: {coordinate}")
            values[ord(column) - ord("A")] = value
        yield row_number, tuple(values)
        element.clear()
        # The sheetData container otherwise retains hundreds of thousands of rows.
        if root is not None:
            sheet_data = root.find(NS + "sheetData")
            if sheet_data is not None:
                sheet_data.clear()


def workbook_info(archive: zipfile.ZipFile) -> dict:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    sheets = workbook.findall(NS + "sheets/" + NS + "sheet")
    if len(sheets) != 1:
        raise ValueError("Expected exactly one source worksheet")
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relationship = next(r for r in relationships if r.attrib["Id"] == sheets[0].attrib[REL_NS + "id"])
    if relationship.attrib.get("TargetMode") == "External":
        raise ValueError("External worksheet relationship")
    target = relationship.attrib["Target"]
    path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("xl", target))
    properties = workbook.find(NS + "workbookPr")
    date1904 = properties is not None and properties.attrib.get("date1904") in {"1", "true"}
    return {"worksheet_name": sheets[0].attrib["name"], "worksheet_xml_path": path,
            "excel_date_system": "1904" if date1904 else "1900"}


def raw_example(row_number: int, values: tuple[str, ...]) -> dict:
    return {"source_row": row_number, "source_range": f"A{row_number}:H{row_number}",
            "raw": dict(zip(HEADERS, values, strict=True))}


def row_reasons(values: tuple[str, ...]) -> set[str]:
    reasons = set()
    for header, value in zip(HEADERS, values, strict=True):
        if not value.strip():
            reasons.add("missing_" + header)
    invoice, stock, _, quantity, date, price, customer, _ = values
    if invoice.upper().startswith("C"):
        reasons.add("cancelled_invoice")
    elif invoice and not re.fullmatch(r"\d{6}", invoice):
        reasons.add("malformed_invoice_id")
    if stock and not GOODS_CODE.fullmatch(stock):
        reasons.add("non_goods_stock_code")
    for label, raw in (("quantity", quantity), ("unit_price", price), ("invoice_date", date)):
        value = decimal_value(raw)
        if raw and value is None:
            reasons.add("malformed_" + label)
        elif value is not None:
            if value < 0:
                reasons.add("negative_" + label)
            elif value == 0:
                reasons.add("zero_" + label)
            if label == "quantity" and value != value.to_integral_value():
                reasons.add("nonintegral_quantity")
    if customer and not re.fullmatch(r"\d{5}", customer):
        reasons.add("malformed_customer_id")
    return reasons


def invoice_derivations(lines: list[dict]) -> dict:
    prices = [Decimal(line["unit_price_decimal"]) for line in lines]
    # Precision follows input digit lengths, decimal scales and line count, so
    # Decimal multiplication/addition cannot silently round source lexemes.
    lowest_exponent = min(0, *(p.as_tuple().exponent for p in prices))
    largest_adjusted = max(p.adjusted() + len(str(line["quantity"])) for p, line in zip(prices, lines, strict=True))
    with localcontext() as context:
        context.prec = max(28, largest_adjusted - lowest_exponent + len(str(len(lines))) + 4)
        total = sum((p * line["quantity"] for p, line in zip(prices, lines, strict=True)), Decimal(0))
        return {"total_quantity": sum(line["quantity"] for line in lines),
                "exact_subtotal_gbp": format(total, "f"),
                "display_subtotal_gbp": format(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f"),
                "unit_price_min_gbp": format(min(prices), "f"),
                "unit_price_max_gbp": format(max(prices), "f")}


def sample_rows(rows, sample_size: int | None) -> dict:
    """Group complete invoices, then rank only eligible IDs by SHA256."""
    if sample_size is not None and sample_size < 1:
        raise ValueError("sample_size must be positive")
    groups = defaultdict(list)
    quality = Counter()
    examples = defaultdict(list)
    rows_scanned = 0
    for row_number, values in rows:
        rows_scanned += 1
        groups[values[0]].append((row_number, values))
        reasons = row_reasons(values)
        quality.update(reasons)
        if reasons:
            quality["rows_with_individual_quality_issues"] += 1
        for reason in sorted(reasons):
            if len(examples[reason]) < 3:
                examples[reason].append(raw_example(row_number, values))

    eligible = []
    exclusions = Counter()
    excluded_rows = 0
    for invoice_id, records in groups.items():
        reasons = set()
        seen = {}
        duplicate_members = set()
        for row_number, values in records:
            reasons.update(row_reasons(values))
            if values in seen:
                reasons.add("exact_duplicate_rows")
                quality["exact_duplicate_occurrences_after_first"] += 1
                duplicate_members.update((seen[values], row_number))
                if len(examples["exact_duplicate_rows"]) < 3:
                    example = raw_example(row_number, values)
                    example["first_identical_source_row"] = seen[values]
                    examples["exact_duplicate_rows"].append(example)
            else:
                seen[values] = row_number
        quality["exact_duplicate_group_member_rows"] += len(duplicate_members)
        for field, index in (("customer_id", 6), ("country", 7), ("invoice_date", 4)):
            if len({values[index] for _, values in records}) > 1:
                reason = "inconsistent_" + field
                reasons.add(reason)
                quality["rows_in_invoices_with_" + reason] += len(records)
                if len(examples[reason]) < 3:
                    first = records[0]
                    other = next(record for record in records if record[1][index] != first[1][index])
                    examples[reason].append({"invoice_id": invoice_id, "conflicting_rows": [raw_example(*first), raw_example(*other)]})
        if reasons:
            exclusions.update(reasons)
            excluded_rows += len(records)
        else:
            eligible.append(invoice_id)

    eligible.sort(key=lambda key: (hashlib.sha256(key.encode("utf-8")).hexdigest(), key))
    if sample_size is None:
        sample_size = len(eligible)
    if len(eligible) < sample_size:
        raise ValueError(f"Only {len(eligible)} eligible invoices, requested {sample_size}")
    invoices = []
    for invoice_id in eligible[:sample_size]:
        lines = []
        for row_number, values in groups[invoice_id]:
            invoice, stock, description, quantity, date, price, customer, country = values
            lines.append({"source_row": row_number, "source_range": f"A{row_number}:H{row_number}",
                          "invoice_no": invoice, "stock_code": stock, "description": description,
                          "quantity": int(Decimal(quantity)), "quantity_decimal": quantity,
                          "unit_price_decimal": price, "invoice_date_serial": date,
                          "customer_id": customer, "country": country})
        first = lines[0]
        invoices.append({"invoice_id": invoice_id, "customer_id": first["customer_id"],
                         "country": first["country"], "invoice_date_serial": first["invoice_date_serial"],
                         "lines": lines, "derived": invoice_derivations(lines)})
    return {"rows_scanned": rows_scanned, "quality_counts": dict(sorted(quality.items())),
            "invoice_population": {"all_distinct_invoice_keys": len(groups), "eligible_invoices": len(eligible),
                                   "excluded_invoices": len(groups) - len(eligible),
                                   "rows_in_eligible_invoices": rows_scanned - excluded_rows,
                                   "rows_in_excluded_invoices": excluded_rows,
                                   "sampled_invoices": len(invoices), "sampled_rows": sum(len(i["lines"]) for i in invoices),
                                   "excluded_invoice_reason_counts": dict(sorted(exclusions.items()))},
            "eligibility": {"unit_of_exclusion": "whole_invoice",
                            "selection": "ascending SHA256(UTF-8 invoice_id), then invoice_id; first sample_size eligible invoices",
                            "sample_size": sample_size,
                            "goods_stock_code_rule": "five digits followed by zero to two ASCII letters; syntactic filter only",
                            "required_fields": list(HEADERS),
                            "numeric_rules": "positive integral Quantity; positive finite decimal UnitPrice and InvoiceDate",
                            "invoice_id_rule": "six digits, with all C-prefixed cancellation invoices excluded",
                            "customer_id_rule": "five digits as provided by anonymized source",
                            "consistent_within_invoice": ["CustomerID", "Country", "InvoiceDate"],
                            "duplicates": "identical values in all eight columns exclude the whole invoice; business meaning unknown",
                            "quality_counts": "independent reasons can overlap; duplicate counts include original observations",
                            "exact_subtotal": "sum(Quantity * original XML UnitPrice decimal), with no line rounding",
                            "display_subtotal": "presentation assumption only: exact subtotal rounded once to GBP 0.01, ROUND_HALF_UP",
                            "tax_discount_shipping": "not invented; source rows with non-goods codes exclude their whole invoice"},
            "invoices": invoices, "excluded_examples": dict(sorted(examples.items()))}


def build_sample(source: Path, sample_size: int | None = 24) -> dict:
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError(f"Source SHA256 mismatch: {digest}")
    with zipfile.ZipFile(source) as archive:
        metadata = workbook_info(archive)
        strings_xml = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(t.text or "" for t in item.iter(NS + "t")) for item in strings_xml]
        with archive.open(metadata["worksheet_xml_path"]) as stream:
            rows = iter_sheet_rows(stream, strings)
            header_row, headers = next(rows)
            if header_row != 1 or headers != HEADERS:
                raise ValueError(f"Unexpected worksheet headers: {headers}")
            result = sample_rows(rows, sample_size)
    if result["rows_scanned"] != EXPECTED_ROWS:
        raise ValueError(f"Expected {EXPECTED_ROWS} data rows, found {result['rows_scanned']}")
    return {"schema_version": "1.0", "source": {"title": "Online Retail",
            "creator": "Daqing Chen", "repository": "UCI Machine Learning Repository",
            "doi": "10.24432/C5BW33", "official_record": "https://archive.ics.uci.edu/dataset/352/online+retail",
            "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "source_nature": "REAL_HISTORICAL_TRANSACTIONS_NOT_ORIGINAL_QUOTES",
            "currency": "GBP", "xlsx_sha256": digest, "xlsx_size_bytes": source.stat().st_size,
            "original_columns": list(HEADERS), "transformations": "whole-invoice eligibility filtering, deterministic invoice sampling, exact derived subtotals",
            **metadata}, **result}


def write_sample(output: Path, payload: dict) -> None:
    """Exclusive creation prevents silently replacing a previously reviewed sample."""
    with output.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=24)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists; choose a new path")
    payload = build_sample(args.source, args.sample_size)
    write_sample(args.output, payload)
    print(json.dumps({"output": str(args.output), "rows_scanned": payload["rows_scanned"],
                      "invoice_population": payload["invoice_population"]}, indent=2))


if __name__ == "__main__":
    main()
