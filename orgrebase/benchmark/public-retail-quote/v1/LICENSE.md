# Dataset license and attribution

The data in `sample.json` is a transformed subset of:

Chen, D. (2015). **Online Retail** [Dataset]. UCI Machine Learning Repository.
DOI: https://doi.org/10.24432/C5BW33
Official record: https://archive.ics.uci.edu/dataset/352/online%2Bretail

The publisher licenses this dataset under **Creative Commons Attribution 4.0 International (CC BY 4.0)**:
https://creativecommons.org/licenses/by/4.0/
Legal code: https://creativecommons.org/licenses/by/4.0/legalcode

Changes: whole-invoice eligibility filtering, deterministic invoice sampling, XLSX-to-JSON extraction with original numeric lexemes and row coordinates, quality summaries and derived subtotals. Original transaction values are not repaired. The exact transformations and source checksum are recorded in `dataset-manifest.json` and `sample.json`.

The attribution and license apply to the public dataset subset, not to OrgRebase software. OrgRebase's software license does not replace or restrict the CC BY 4.0 license on this data. The original creator and UCI do not endorse this product or its evaluation.

Discounts, taxes, organization roles and approvals introduced by the replay are controlled experimental assumptions, not part of the original dataset. Historical transactions are not original customer quote requests or current prices.
