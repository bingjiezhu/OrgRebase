# Public enterprise transaction inputs

This directory retains 24 complete historical invoices, 355 lines, from UCI Online Retail (2010–2011, GBP). Data attribution and CC BY 4.0 terms are in [LICENSE.md](LICENSE.md); checksums, selection rules and assumptions are in [dataset-manifest.json](dataset-manifest.json).

`sample.json` is the original retained sample, SHA-256 `eaf5b166a5aa14d5186e69bc5537f4985657c2a9d6d8270cfb2d6492fec8b128`. It was selected by ascending SHA-256 of eligible invoice IDs, before runtime results. It is a small correctness workload, not a representative survey of enterprise demand.

From the repository root:

```bash
.venv/bin/python scripts/run_public_quote_replay.py --output ../.runtime/retail-replay-new
.venv/bin/python scripts/run_public_quote_replay.py --output ../.runtime/retail-demo-new --demo-invoice 557670
```

The default sample is checksum-verified against this manifest. Arbitrary `--sample` files receive structural and arithmetic checks; matching metadata alone does not authenticate their source. To reopen the official XLSX, reconstruct this sample, profile every eligible invoice, independently check the production pricing function, and generate a review page:

```bash
.venv/bin/python scripts/evaluate_public_quote_data.py \
  --source '/path/to/Online Retail.xlsx' \
  --output ../.runtime/retail-data-review-new
```

The review writes `report.json`, `index.html`, a disjoint additional sample (hash ranks 25–48), and all eligible invoices with scientific-notation prices as an explicitly selected regression set. These generated samples are digest-bound when the source is scanned; later replay annotation rejects changed samples. The original source lexeme `7.0000000000000007E-2` is expanded exactly to `0.070000000000000007` at runtime input adaptation, without rounding or changing source records. Whole-invoice row limits remain enforced; unsupported invoices are counted separately, not silently removed from the denominator.

The full source file, run databases and local reports are not redistributed in this directory. The source delivery allowlist includes only these four small data/document files. See [priced quote demo](../../../docs/PRICED-QUOTE-DEMO.md) for the UI journey.
