"""Small synthetic controls for source preservation and whole-invoice eligibility."""

import hashlib
import io
from decimal import Decimal

import pytest

from scripts.data_adapters.build_retail_quote_sample import (
    NS,
    build_sample,
    invoice_derivations,
    iter_sheet_rows,
    sample_rows,
    write_sample,
)


def record(number, invoice="536365", *, stock="85123A", quantity="6", price="2.5500000000000003", customer="17850", country="United Kingdom", date="40513.3513888889"):
    return number, (invoice, stock, "WHITE HANGING HEART T-LIGHT HOLDER", quantity, date, price, customer, country)


def test_parser_preserves_xml_decimals_physical_coordinates_and_text():
    xml = f'''<worksheet xmlns="{NS[1:-1]}"><sheetData>
      <row r="17"><c r="A17"><v>536365</v></c><c r="B17" t="s"><v>0</v></c>
      <c r="C17" t="inlineStr"><is><t>  ORIGINAL DESCRIPTION </t></is></c>
      <c r="D17"><v>6</v></c><c r="E17"><v>40513.3513888889</v></c>
      <c r="F17"><v>2.5500000000000003</v></c><c r="H17" t="s"><v>1</v></c></row>
      <row r="23"><c r="A23"><v>536366</v></c></row>
    </sheetData></worksheet>'''
    rows = list(iter_sheet_rows(io.BytesIO(xml.encode()), ["85123A", "United Kingdom"]))
    assert rows[0] == (17, ("536365", "85123A", "  ORIGINAL DESCRIPTION ", "6", "40513.3513888889", "2.5500000000000003", "", "United Kingdom"))
    assert rows[1][0] == 23


def test_entire_invalid_invoices_excluded_and_deterministic_sampling():
    rows = [record(2), record(3, stock="71053"), record(4, "536366"),
            record(5, "536367"), record(6, "536367", quantity="0"),
            record(7, "536368"), record(8, "536368", customer=""),
            record(9, "536369"), record(10, "536369"),
            record(11, "C536370", quantity="-6"),
            record(12, "536371"), record(13, "536371", stock="POST"),
            record(14, "536372"), record(15, "536372", country="France", date="40514"),
            record(16, "536373", price="NaN"), record(17, "536374", quantity="1.5")]
    sample = sample_rows(iter(rows), 2)
    expected = sorted(("536365", "536366"), key=lambda v: hashlib.sha256(v.encode()).hexdigest())
    assert [invoice["invoice_id"] for invoice in sample["invoices"]] == expected
    assert sample["invoice_population"]["rows_in_eligible_invoices"] == 3
    assert sample["invoice_population"]["excluded_invoices"] == 8
    quality = sample["quality_counts"]
    assert quality["exact_duplicate_occurrences_after_first"] == 1
    assert quality["exact_duplicate_group_member_rows"] == 2
    assert quality["cancelled_invoice"] == quality["negative_quantity"] == 1
    assert quality["rows_in_invoices_with_inconsistent_country"] == 2
    assert "malformed_unit_price" in sample["excluded_examples"]
    single = sample_rows(iter(rows), 1)
    assert single["invoices"] == sample["invoices"][:1]


def test_exact_decimal_total_matches_independent_scaled_integer_and_half_up():
    lines = [{"quantity": 3, "unit_price_decimal": "0.10000000000000001"},
             {"quantity": 2, "unit_price_decimal": "1.0025"}]
    result = invoice_derivations(lines)
    # Independent fixed scale integer arithmetic, no Decimal products or sums.
    scaled_total = 3 * 10000000000000001 + 2 * 100250000000000000
    expected = f"{scaled_total // 10**17}.{scaled_total % 10**17:017d}"
    assert result["exact_subtotal_gbp"] == expected == "2.30500000000000003"
    assert result["display_subtotal_gbp"] == "2.31"
    assert result["total_quantity"] == 5
    assert Decimal(result["unit_price_min_gbp"]) == Decimal("0.10000000000000001")


def test_wrong_source_and_existing_output_fail_closed(tmp_path):
    source = tmp_path / "wrong.xlsx"
    source.write_bytes(b"not the pinned UCI source")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        build_sample(source)
    output = tmp_path / "sample.json"
    write_sample(output, {"test": 1})
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        write_sample(output, {"test": 2})
    assert output.read_bytes() == original
