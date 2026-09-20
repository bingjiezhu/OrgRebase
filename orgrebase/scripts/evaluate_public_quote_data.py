#!/usr/bin/env python3
"""Check the pinned source, sample coverage and pricing over all eligible invoices.

This is an offline evaluation, not a production capacity or customer-value test.
Original transactions, controlled policies and computed results remain separate.
"""
# ruff: noqa: RUF001
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orgrebase.domain import VersionedObject
from orgrebase.workspace.pricing import PricingPolicy, QuoteBasket, calculate_quote
from scripts.data_adapters.build_retail_quote_sample import build_sample
from scripts.run_public_quote_replay import load_sample, pricing_oracle, quote_basket_input, write_json

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmark/public-retail-quote/v1"


def verify_retained_sample(population: dict, path: Path, manifest_path: Path) -> tuple[dict, str]:
    manifest = json.loads(manifest_path.read_text())
    sample, digest = load_sample(path)
    if digest != "sha256:" + manifest["sample"]["sha256"]:
        raise ValueError("RETAINED_SAMPLE_DIGEST_MISMATCH")
    if sample["source"] != population["source"]:
        raise ValueError("SAMPLE_SOURCE_METADATA_MISMATCH")
    expected = population["invoices"][:len(sample["invoices"])]
    if sample["invoices"] != expected:
        raise ValueError("SAMPLE_ORIGINAL_ROWS_OR_SELECTION_MISMATCH")
    for key in ("rows_scanned", "quality_counts", "excluded_examples"):
        if sample[key] != population[key]:
            raise ValueError(f"SAMPLE_SOURCE_PROFILE_MISMATCH:{key}")
    return sample, digest


def describe(invoices: list[dict]) -> dict:
    sizes = sorted(len(item["lines"]) for item in invoices)
    totals = sorted(Decimal(item["derived"]["exact_subtotal_gbp"]) for item in invoices)

    def quantiles(values):
        if not values:
            return {}
        return {label: str(values[max(0, math.ceil(len(values) * fraction) - 1)])
                for label, fraction in (("min", 0), ("p50", .5), ("p95", .95), ("max", 1))}

    countries = Counter(item["country"] for item in invoices)
    months = Counter((datetime(1899, 12, 30) + timedelta(days=int(Decimal(item["invoice_date_serial"])))).strftime("%Y-%m")
                     for item in invoices)
    return {"invoices": len(invoices), "lines": sum(sizes), "line_count": quantiles(sizes),
            "exact_subtotal_gbp": quantiles(totals), "countries": dict(sorted(countries.items())),
            "months": dict(sorted(months.items())), "quantiles": "nearest-rank; invoices, not individual lines"}


def evaluate_prices(invoices: list[dict]) -> dict:
    results, unsupported, mismatches = [], [], []
    for invoice in invoices:
        body = quote_basket_input(invoice, "source:uci:invoice:" + invoice["invoice_id"])
        try:
            basket = QuoteBasket.model_validate_json(json.dumps(body))
        except ValidationError as exc:
            unsupported.append({"invoice_id": invoice["invoice_id"], "lines": len(invoice["lines"]),
                                "reasons": [{"location": list(e["loc"]), "type": e["type"]}
                                            for e in exc.errors(include_input=False, include_context=False)]})
            continue
        for discount in (0, 1000):
            policy = PricingPolicy(discount_bps=discount, tax_bps=2000,
                                   tax_label="受控演示税率（非历史实际税费）", source_ref="controlled:pricing-evaluation")
            actual = calculate_quote(basket, policy, currency="GBP").model_dump(mode="json")
            expected = pricing_oracle(invoice, discount_bps=discount, tax_bps=2000)
            wrong = [key for key, value in expected.items() if actual[key] != value]
            if wrong:
                mismatches.append({"invoice_id": invoice["invoice_id"], "discount_bps": discount, "fields": wrong})
        # Keep per-invoice totals and original row coordinates for review, not a
        # second copy of every full source line or an aggregate called revenue.
        results.append({"invoice_id": invoice["invoice_id"], "line_count": len(invoice["lines"]),
                        "rows": [line["source_row"] for line in invoice["lines"]],
                        "expanded_scientific_prices": [
                            {"row": line["source_row"], "original": line["unit_price_decimal"], "plain_decimal": item["unit_price"]}
                            for line, item in zip(invoice["lines"], body["items"], strict=True)
                            if line["unit_price_decimal"] != item["unit_price"]],
                        "subtotal_gbp": actual["subtotal"], "discounted_total_gbp": actual["total"]})
    return {"scope": "PRODUCTION_PRICING_FUNCTION_ONLY_NOT_FULL_WORKFLOW_OR_CAPACITY",
            "eligible_invoices": len(invoices), "priced_invoices": len(results),
            "priced_lines": sum(item["line_count"] for item in results),
            "price_calculations": len(results) * 2, "unsupported_invoices": unsupported,
            "mismatches": mismatches, "oracle_status": "FAIL" if mismatches else "PASS" if results else "NOT_RUN",
            "controlled_discount_bps": [0, 1000], "controlled_tax_bps": 2000,
            "results": results}


def verify_replay(report_path: Path, sample: dict) -> dict:
    """Re-read saved quotes and original attachments, not just their PASS labels."""
    report = json.loads(report_path.read_text())
    originals = {item["invoice_id"]: item for item in sample["invoices"]}
    cases = report["cases"]
    if len(cases) != len(originals) or {item["invoice_id"] for item in cases} != set(originals):
        raise ValueError("REPLAY_CASE_COVERAGE_MISMATCH")
    root = report_path.parent.resolve()
    for case in cases:
        directory = (root / case["evidence_directory"]).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("REPLAY_EVIDENCE_OUTSIDE_REPORT")
        source_bytes = (directory / "source-transaction.json").read_bytes()
        if "sha256:" + hashlib.sha256(source_bytes).hexdigest() != case["source_digest"]:
            raise ValueError("REPLAY_SOURCE_DIGEST_MISMATCH")
        invoice = originals[case["invoice_id"]]
        if json.loads(source_bytes)["invoice"] != invoice:
            raise ValueError("REPLAY_SOURCE_ROWS_MISMATCH")
        for side, discount in (("before", 0), ("after", 1000)):
            quote = json.loads((directory / f"quote-{side}.json").read_text())
            admitted = VersionedObject.model_validate(quote)
            if admitted.digest != case[f"{side}_quote_digest"]:
                raise ValueError("REPLAY_QUOTE_DIGEST_MISMATCH")
            actual = admitted.payload["pricing"]
            expected = pricing_oracle(invoice, discount_bps=discount, tax_bps=2000)
            if any(actual[key] != value for key, value in expected.items()):
                raise ValueError("REPLAY_PRICE_MISMATCH")
            if actual != case[f"{side}_pricing"]:
                raise ValueError("REPLAY_REPORTED_PRICE_MISMATCH")
        if case.get("status") != "PASS":
            raise ValueError("REPLAY_REPORTED_FAILURE")
    return {"status": "PASS", "source_rows_and_quote_prices_rechecked": True,
            "workflow_reexecuted_by_this_check": False, "invoices": len(cases),
            "quotes": len(cases) * 2, "file_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest()}


def write_html(path: Path, report: dict) -> None:
    def esc(value):
        return html.escape(str(value))
    population = report["population"]
    engine = report["pricing"]
    profiles = report["coverage"]
    retained, all_eligible = profiles["retained_sample"], profiles["all_eligible"]
    rows = []
    for key in sorted(set(all_eligible["months"]) | set(retained["months"])):
        rows.append(f'<tr><th>{esc(key)}</th><td>{all_eligible["months"].get(key, 0):,}</td>'
                    f'<td>{retained["months"].get(key, 0):,}</td></tr>')
    unsupported = ''.join(f'<tr><td>{esc(item["invoice_id"])}</td><td>{item["lines"]}</td>'
                          f'<td>{esc(json.dumps(item["reasons"]))}</td></tr>' for item in engine["unsupported_invoices"])
    example = report.get("example", {})
    example_html = ""
    if example:
        source_rows = ''.join(f'<tr><td>{line["source_row"]}</td><td>{esc(line["stock_code"])}</td>'
                              f'<td>{esc(line["description"])}</td><td>{line["quantity"]}</td>'
                              f'<td>{esc(line["unit_price_decimal"])}</td></tr>' for line in example["invoice"]["lines"])
        comparisons = ''.join(f'<tr><th>{label}</th><td>{esc(example["before"][field])}</td>'
                             f'<td>{esc(example["after"][field])}</td></tr>' for label, field in
                             (("商品小计", "subtotal"), ("受控折扣", "discount_amount"),
                              ("折后净额", "net_amount"), ("受控税额", "tax_amount"), ("计算总额", "total")))
        example_html = f'''<h2>同一业务输入，查看规则变更的实际结果</h2>
<p>原始发票{esc(example['invoice']['invoice_id'])}。为方便人工核对，从原24单中选择6行示例；没有据此改变评测集合。下方金额来自已保存且复核过的前后报价。</p>
<div style="overflow-x:auto"><table><tr><th>Excel行</th><th>SKU</th><th>原始商品描述</th><th>数量</th><th>原始单价 GBP</th></tr>{source_rows}</table></div>
<p>演示规则：整单折扣0%→10%，折后价外税保持20%；这些设定不是历史实际合同。</p>
<table><tr><th>计算项 GBP</th><th>规则变更前</th><th>批准生效后</th></tr>{comparisons}</table>'''
    additional = report.get("additional_replays", {})
    additional_rows = ''.join(f'<tr><th>{esc(name)}</th><td>{item["invoices"]}</td><td>{item["quotes"]}</td>'
                              f'<td>{esc(item["status"])}</td></tr>' for name, item in additional.items())
    additional_html = (f'<h2>新增完整工作流回放</h2><table><tr><th>集合</th><th>发票</th><th>前后报价</th>'
                       f'<th>独立复核</th></tr>{additional_rows}</table>' if additional else "")
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>公开企业数据评测 · OrgRebase</title><style>
body{{font:16px/1.65 system-ui,sans-serif;color:#212421;background:#f4f3ee;margin:0}}main{{max-width:1100px;margin:auto;padding:40px 24px}}
h1{{font-size:36px;line-height:1.25}}h2{{font-size:23px;margin-top:36px}}a{{color:#275d7a}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}}
article,section{{background:#fff;padding:20px;border:1px solid #deded7;border-radius:8px}}article strong{{display:block;font-size:30px}}small{{color:#555}}
table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left;overflow-wrap:anywhere}}th{{font-weight:600}}
code{{overflow-wrap:anywhere}}details{{margin:20px 0}}.notice{{border-left:4px solid #ba602f;padding:12px 18px;background:#fff}}
@media(max-width:700px){{.cards{{grid-template-columns:1fr}}h1{{font-size:28px}}}} </style><main>
<small>OrgRebase · 可复现数据评测 · 受控本地</small><h1>真实交易输入，明确规则，独立核对</h1>
<p>原始文件已校验固定哈希，并逐行重新生成保留样本。下面分别展示数据范围、实际计算与完整工作流；它们不是同一组分母。</p>
<div class="cards"><article><small>原始企业交易</small><strong>{report["rows_scanned"]:,} 行</strong>{population['all_distinct_invoice_keys']:,}个发票编号，含取消、缺失与非商品记录。</article>
<article><small>完整数据适用范围</small><strong>{population["eligible_invoices"]:,} 单</strong>按明示条件整单筛选；另有{population["excluded_invoices"]:,}单未进入正向计价集合。</article>
<article><small>实际生产计价器独立核对</small><strong>{engine["priced_invoices"]:,} 单</strong>{engine["price_calculations"]:,}次计算，差异{len(engine["mismatches"])}；{len(engine["unsupported_invoices"])}单超出现有输入合同。</article></div>
<h2>先分清数据的三种角色</h2><table><tr><th>原始企业观察</th><th>受控实验规则</th><th>系统计算结果</th></tr>
<tr><td>SKU、描述、数量、原始GBP单价、发票和Excel行坐标。</td><td>组织、负责人、20%价外税、0%→10%折扣。</td><td>按当前规则计算行金额、折扣、税额和报价总额。并非原企业发票总额。</td></tr></table>
<p><a href="https://archive.ics.uci.edu/dataset/352/online%2Bretail">UCI Online Retail</a> · Daqing Chen · 2010–2011年真实零售/批发交易 · DOI 10.24432/C5BW33 · <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>。历史单价不代表当前市场报价。</p>
{example_html}
<h2>原24单评测应该如何解读</h2><section><p>固定摘要抽样保留{retained["invoices"]}单、{retained["lines"]}条明细，覆盖{len(retained["countries"])}个国家/地区值、{len(retained["months"])}个月份。完整合格集合有{len(all_eligible["countries"])}个国家/地区值（含未指定值Unspecified）、{len(all_eligible["months"])}个月份。</p>
<p>原24单保存的来源附件与前后报价复查：{esc(report["retained_replay"]["status"])}。复查旧证据不冒充重新执行审批。新增独立样本是固定排序第25–48单，在执行前确定，且与原24单无重叠；只有核对过保存文件的完整工作流结果才列在下方。</p>
<p>每单行数（最近秩）：完整合格集合P50={all_eligible["line_count"]["p50"]}、P95={all_eligible["line_count"]["p95"]}、最大={all_eligible["line_count"]["max"]}；原样本最大={retained["line_count"]["max"]}。</p></section>
<details><summary>按月份查看样本覆盖</summary><table><tr><th>原始月份</th><th>合格发票</th><th>原24单样本</th></tr>{''.join(rows)}</table></details>
{additional_html}
<h2>异常与限制也计入分母</h2><p>取消、负数量、缺失、特殊费用编号、完全重复行按整单排除。重复行可能有实际含义，不能擅自删除再当作完整订单。各原因重叠，不能相加当作总排除量。</p>
<details><summary>查看原始数据排除原因</summary><pre>{esc(json.dumps(population['excluded_invoice_reason_counts'],ensure_ascii=False,indent=2))}</pre></details>
<details><summary>查看合格数据中超出当前计价合同的订单（{len(engine['unsupported_invoices'])}单）</summary><table><tr><th>发票</th><th>明细行</th><th>确定性拒绝原因</th></tr>{unsupported}</table></details>
<div class="notice">数据真实不等于企业已经接入。全量检查调用实际计价器，不包含整个审批链、原生AgentTeams、外部连接器、生产压力或付费价值。没有把价格差额计作ROI。</div>
<h2>复核入口</h2><p><a href="report.json">完整机器报告</a> · <a href="extension-sample.json">新增24单原始输入</a> · <a href="notation-sample.json">科学计数法回归输入</a>。报告中保留原始行坐标、来源哈希、所有未支持订单和计算差异。</p>
<p>源XLSX SHA-256：<code>{esc(report['source']['xlsx_sha256'])}</code></p></main></html>'''
    path.write_text(body, encoding="utf-8")


def selected_sample(population: dict, invoices: list[dict], selection: str) -> dict:
    result = {**population, "invoices": invoices, "invoice_population": dict(population["invoice_population"]),
              "eligibility": dict(population["eligibility"])}
    result["invoice_population"].update(sampled_invoices=len(invoices), sampled_rows=sum(len(i["lines"]) for i in invoices))
    result["eligibility"].update(sample_size=len(invoices), selection=selection)
    return result


def annotate_replays(output: Path, replay_report: Path | None = None,
                     extension_replay: Path | None = None, notation_replay: Path | None = None) -> dict:
    """Add verified saved-run evidence to a completed source/coverage review."""
    report = json.loads((output / "report.json").read_text())
    if replay_report:
        sample, digest = load_sample(DATASET / "sample.json")
        if digest != report["retained_sample_digest"]:
            raise ValueError("RETAINED_SAMPLE_CHANGED_SINCE_SOURCE_REVIEW")
        report["retained_replay"] = verify_replay(replay_report, sample)
        saved = json.loads(replay_report.read_text())
        case = next(item for item in saved["cases"] if item["invoice_id"] == "557670")
        report["example"] = {"invoice": next(item for item in sample["invoices"] if item["invoice_id"] == "557670"),
                             "before": case["before_pricing"], "after": case["after_pricing"]}
    for label, filename, path in (("固定排序第25–48单", "extension-sample.json", extension_replay),
                                  ("科学计数法原始订单回归", "notation-sample.json", notation_replay)):
        if path:
            sample, digest = load_sample(output / filename)
            if digest != report["evaluation_samples"][filename]["digest"]:
                raise ValueError("EVALUATION_SAMPLE_CHANGED_SINCE_SOURCE_REVIEW")
            report.setdefault("additional_replays", {})[label] = verify_replay(path, sample)
    write_json(output / "report.json", report)
    write_html(output / "index.html", report)
    return report


def evaluate(source: Path, output: Path, replay_report: Path | None = None) -> dict:
    if output.exists():
        raise FileExistsError("OUTPUT_MUST_BE_NEW")
    population = build_sample(source, sample_size=None)
    sample, digest = verify_retained_sample(population, DATASET / "sample.json", DATASET / "dataset-manifest.json")
    output.mkdir(parents=True)
    extension = selected_sample(population, population["invoices"][len(sample["invoices"]):len(sample["invoices"]) + 24],
        "ascending SHA256(invoice_id); positions 25 through 48; disjoint from retained first 24; chosen before execution")
    extension_digest = write_json(output / "extension-sample.json", extension)
    notation = selected_sample(population,
        [item for item in population["invoices"] if any("e" in line["unit_price_decimal"].lower() for line in item["lines"])],
        "all eligible whole invoices containing scientific price notation; explicit regression set, not random or held-out")
    notation_digest = write_json(output / "notation-sample.json", notation)
    pricing = evaluate_prices(population["invoices"])
    report = {"schema_version": "orgrebase.public-quote-data-review.v1", "source": population["source"],
              "source_verification": "PINNED_XLSX_REOPENED_AND_RETAINED_SAMPLE_RECONSTRUCTED",
              "retained_sample_digest": digest, "rows_scanned": population["rows_scanned"],
              "evaluation_samples": {
                  "extension-sample.json": {"digest": extension_digest, "invoices": len(extension["invoices"])},
                  "notation-sample.json": {"digest": notation_digest, "invoices": len(notation["invoices"])},
              },
              "population": population["invoice_population"], "quality_counts": population["quality_counts"],
              "coverage": {"all_eligible": describe(population["invoices"]),
                           "retained_sample": describe(sample["invoices"]), "extension_sample": describe(extension["invoices"])},
              "pricing": pricing, "retained_replay": verify_replay(replay_report, sample) if replay_report else {"status": "NOT_RUN"},
              "claim_ceiling": "VALIDATED_CONTROLLED_LOCAL"}
    write_json(output / "report.json", report)
    write_html(output / "index.html", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-report", type=Path)
    parser.add_argument("--extension-replay", type=Path)
    parser.add_argument("--notation-replay", type=Path)
    parser.add_argument("--annotate-only", action="store_true",
                        help="Only verify saved replay files and update an existing review; source verification is not rerun")
    args = parser.parse_args()
    if not args.annotate_only:
        if args.source is None:
            parser.error("--source is required unless --annotate-only is set")
        evaluate(args.source, args.output, args.replay_report)
    result = annotate_replays(args.output, args.replay_report, args.extension_replay, args.notation_replay)
    print(json.dumps({"output": str(args.output), "source_verification": result["source_verification"],
                      "source_rechecked_this_invocation": not args.annotate_only,
                      "priced_invoices": result["pricing"]["priced_invoices"],
                      "unsupported": len(result["pricing"]["unsupported_invoices"]),
                      "mismatches": len(result["pricing"]["mismatches"])}, ensure_ascii=False))
    if result["pricing"]["mismatches"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
