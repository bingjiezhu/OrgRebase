#!/usr/bin/env python3
"""Replay public transaction baskets through the existing governed quote runtime.

The production renderer calculates prices. An independent rational arithmetic
oracle verifies them under explicit, controlled discount and tax assumptions.
"""
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import platform
import re
import shlex
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError
from orgrebase.workspace.change_proposals import ChangeProposalInput, change_options, submit_change
from orgrebase.workspace.models import DomainPack, EnterpriseBinding, EnterpriseResourceBinding
from orgrebase.workspace.pilot import load_enterprise_quote_pilot_pack
from orgrebase.workspace.pilot_authoring import (
    initialize_enterprise_quote_pilot_draft,
    seal_enterprise_quote_pilot_pack,
)
from orgrebase.workspace.service import WorkspaceService
from orgrebase.workspace.templates import TemplateRegistry, default_capability_cards

INITIAL_DATE = "2026-10-01"
TAX_LABEL = "受控演示税率（非历史实际税费）"
CLAIM_CEILING = "VALIDATED_CONTROLLED_LOCAL"
SOURCE_PINS = {
    "doi": "10.24432/C5BW33", "currency": "GBP",
    "source_nature": "REAL_HISTORICAL_TRANSACTIONS_NOT_ORIGINAL_QUOTES",
    "xlsx_sha256": "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d",
    "xlsx_size_bytes": 23715344,
}
RETAINED_DATASET = Path(__file__).resolve().parents[1] / "benchmark/public-retail-quote/v1"
DEFAULT_SAMPLE = RETAINED_DATASET / "sample.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _public_error(exc: BaseException, phase: str) -> dict[str, str]:
    # Exception messages can contain source text, credentials or private paths.
    # The phase is a fixed runner identifier, never input or exception text.
    return {"exception_type": type(exc).__name__, "code": (
        "PUBLIC_REPLAY_INTERRUPTED" if isinstance(exc, KeyboardInterrupt)
        else f"PUBLIC_REPLAY_{phase.upper()}_FAILED")}


@contextmanager
def _phase(measurement: dict[str, Any], name: str):
    record = {"phase": name, "status": "RUNNING"}
    measurement["phases"].append(record)
    start = time.perf_counter_ns()
    try:
        yield
    except BaseException as exc:
        record.update(status="INCOMPLETE" if isinstance(exc, KeyboardInterrupt) else "FAILED",
                      error=_public_error(exc, name))
        raise
    else:
        record["status"] = "PASS"
    finally:
        record["elapsed_ns"] = max(0, time.perf_counter_ns() - start)


def _distribution(values: list[int]) -> dict[str, int | None]:
    ordered = sorted(values)
    return {"count": len(ordered), "total_ns": sum(ordered),
            **{label: ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None
               for label, fraction in (("p50_ns", .5), ("p95_ns", .95), ("max_ns", 1))}}


def _measurement_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(case["status"] == status for case in cases)
              for status in ("PASS", "FAILED", "INCOMPLETE")}
    measured = [case["measurement"]["elapsed_ns"] for case in cases
                if case.get("measurement", {}).get("elapsed_ns") is not None]
    phases = sorted({phase["phase"] for case in cases
                     for phase in case.get("measurement", {}).get("phases", [])})
    phase_results = {}
    for name in phases:
        records = [phase for case in cases for phase in case.get("measurement", {}).get("phases", [])
                   if phase["phase"] == name]
        phase_results[name] = {
            "outcomes": {status: sum(record["status"] == status for record in records)
                         for status in ("PASS", "FAILED", "INCOMPLETE")},
            "completed": _distribution([record["elapsed_ns"] for record in records if record["status"] == "PASS"]),
        }
    return {"planned_cases": len(cases), "outcomes": counts,
            "attempted_cases": sum(case.get("execution_state") != "NOT_STARTED" for case in cases),
            "qualified_completion_rate": str(Decimal(counts["PASS"]) / len(cases)),
            "all_attempted_elapsed": _distribution(measured),
            "passed_case_elapsed": _distribution([case["measurement"]["elapsed_ns"] for case in cases
                                                   if case["status"] == "PASS"]),
            "phases": phase_results, "quantiles": "NEAREST_RANK_BY_INVOICE_NOT_BY_TOOL_CALL"}


def _write_checkpoint(output: Path, report: dict[str, Any]) -> None:
    report["measurement_summary"] = _measurement_summary(report["cases"])
    temporary = output / "report.json.tmp"
    write_json(temporary, report)
    temporary.replace(output / "report.json")
    write_replay_html(output / "index.html.tmp", report)
    (output / "index.html.tmp").replace(output / "index.html")


def write_replay_html(path: Path, report: dict[str, Any]) -> None:
    def esc(value):
        return html.escape(str(value), quote=True)
    def seconds(ns):
        return "未测量" if ns is None else f"{ns / 1_000_000_000:.3f} 秒"
    summary = report["measurement_summary"]
    rows = []
    for case in report["cases"]:
        attachments = "".join(f'<li><a href="{esc(case["evidence_directory"] + "/" + name)}">{esc(name)}</a></li>'
                              for name in case.get("evidence_files", {}))
        links = ""
        if case.get("execution_state") == "FINISHED":
            links = f'<a href="{esc(case["evidence_directory"])}/case-report.json">逐单报告</a>'
        if attachments:
            links += f'<details><summary>原始附件（{len(case["evidence_files"])}）</summary><ul>{attachments}</ul></details>'
        error = case.get("error", {})
        before, after = case.get("before_pricing", {}), case.get("after_pricing", {})
        amounts = (f'{esc(before["total"])} → {esc(after["total"])}'
                   if case["status"] == "PASS" and before.get("currency") == after.get("currency") == "GBP"
                   and isinstance(before.get("total"), str) and isinstance(after.get("total"), str)
                   else "未验证")
        rows.append(f'<tr><th class="nowrap">{esc(case["invoice_id"])}</th><td>{case["planned_line_count"]}</td>'
                    f'<td>{esc(case["status"])} / {esc(case["execution_state"])}</td>'
                    f'<td class="nowrap">{amounts}</td>'
                    f'<td class="nowrap">{seconds(case.get("measurement", {}).get("elapsed_ns"))}</td>'
                    f'<td>{esc(error.get("code", ""))} {esc(error.get("exception_type", ""))}</td><td class="evidence">{links}</td></tr>')
    phase_rows = []
    for name, value in summary["phases"].items():
        timing, outcomes = value["completed"], value["outcomes"]
        phase_rows.append(f'<tr><th>{esc(name)}</th><td>{outcomes["PASS"]} / {outcomes["FAILED"]} / {outcomes["INCOMPLETE"]}</td>'
                          f'<td>{seconds(timing["p50_ns"])}</td><td>{seconds(timing["p95_ns"])}</td></tr>')
    counts = summary["outcomes"]
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>公开报价工作流实测 · OrgRebase</title><style>
body{{font:16px/1.65 system-ui,sans-serif;color:#212421;background:#f4f3ee;margin:0}}main{{max-width:1150px;margin:auto;padding:32px 20px}}
main p{{overflow-wrap:anywhere}}.nowrap{{white-space:nowrap;overflow-wrap:normal}}.evidence{{min-width:125px;max-width:260px}}details summary{{cursor:pointer}}details ul{{padding-left:20px}}
h1{{font-size:30px}}h2{{font-size:22px;margin-top:32px}}section{{background:white;border:1px solid #ddd;padding:18px;margin:18px 0}}
table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;overflow-wrap:anywhere}}
.scroll{{overflow-x:auto}}a{{color:#275d7a}}code{{overflow-wrap:anywhere}}@media(max-width:700px){{table{{min-width:650px}}h1{{font-size:25px}}}}
</style><main><p>OrgRebase · 本次受控本地工作流实测</p><h1>公开交易输入，逐单治理结果与实际耗时</h1>
<section><strong>总状态：{esc(report['status'])}</strong><p>预登记 {summary['planned_cases']} 单；通过 {counts['PASS']}，失败 {counts['FAILED']}，未完成 {counts['INCOMPLETE']}。
实际启动 {summary['attempted_cases']} 单；合格完成率分母始终为全部预登记样本。预期的越权拒绝是独立负向检查，不计作工作流失败。</p>
<p>已启动样本总测量时间：{seconds(summary['all_attempted_elapsed']['total_ns'] if summary['all_attempted_elapsed']['count'] else None)}；通过样本 P50：{seconds(summary['passed_case_elapsed']['p50_ns'])}，P95：{seconds(summary['passed_case_elapsed']['p95_ns'])}。
未开始样本没有耗时数值；失败耗时包含在已启动总时间中。</p></section>
<h2>输入、规则与测量分别解释</h2><p>SKU、数量、原始 GBP 单价来自保留的 UCI Online Retail 历史交易样本。组织与负责人是受控配置，20%价外税、0%→10%折扣是实验规则。批准由脚本调用现有负责人身份，不是人工审批观测。</p>
<p>时间由 Python perf_counter_ns 单调时钟围绕实际调用测得；逐单总耗时包含准备、治理、独立核对与证据写出，不含样本预检和总报告生成。分段耗时不使用冻结业务时钟，不代表人时、外部服务延迟或生产容量。</p>
<p>同一商品明细在规则改变前后独立核对金额，并检查未批准、错误负责人和拒绝三种对照。没有人工作业基线、费用观测或ROI；金额变化不计作收益。</p>
<h2>实际执行分段</h2><p>分位数只对该分段完成的记录计算，失败和中断数量单列，不能据此隐藏失败。</p><div class="scroll"><table><tr><th>分段</th><th>通过 / 失败 / 未完成</th><th>P50</th><th>P95</th></tr>{''.join(phase_rows)}</table></div>
<h2>全部预登记样本</h2><p>GBP 合计直接来自独立核验通过的前后报价：折扣从 0% 改为 10%，两次均使用受控 20% 价外税；金额变化不是已实现的节省或收益。失败、未完成或金额缺失的样本显示“未验证”。</p><div class="scroll"><table><tr><th>发票</th><th>明细</th><th>结果 / 执行状态</th><th>GBP 合计<br>变更前 → 变更后</th><th>实测耗时</th><th>失败类型</th><th>证据</th></tr>{''.join(rows)}</table></div>
<h2>复核入口</h2><p><a href="run-plan.json">执行前固定的计划</a> · <a href="report.json">完整机器报告</a></p>
<p>样本摘要：<code>{esc(report['sample_digest'])}</code><br>原始 XLSX 固定 SHA-256：<code>{esc(report['source']['xlsx_sha256'])}</code></p>
<p>本次来源验证：{esc(report['source_verification'])}。本回放器验证保留样本及逐单附件，没有重新打开原始XLSX；全源复核仍使用现有独立评测入口。</p>
</main></html>'''
    path.write_text(body, encoding="utf-8")


def write_json(path: Path, payload: Any) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def basket_oracle(invoice: dict[str, Any]) -> dict[str, Any]:
    """Check observed invoice arithmetic without assigning pricing authority to runtime."""
    invoice_id = str(invoice["invoice_id"])
    if not re.fullmatch(r"[0-9]{6}", invoice_id):
        raise ValueError("ELIGIBLE_INVOICE_ID_REQUIRED")
    if not re.fullmatch(r"[0-9]{5}", str(invoice["customer_id"])):
        raise ValueError("ANONYMOUS_SOURCE_CUSTOMER_REQUIRED")
    date_serial = Decimal(invoice["invoice_date_serial"])
    if not date_serial.is_finite() or date_serial <= 0 or not str(invoice["country"]).strip():
        raise ValueError("SOURCE_DATE_AND_COUNTRY_REQUIRED")
    lines = invoice["lines"]
    if not lines:
        raise ValueError("NONEMPTY_INVOICE_REQUIRED")
    prices, quantity, subtotal = [], 0, Fraction(0)
    source_rows, observations = set(), set()
    for line in lines:
        if not isinstance(line["unit_price_decimal"], str):
            raise ValueError("ORIGINAL_DECIMAL_LEXEME_REQUIRED")
        count, price = line["quantity"], Decimal(line["unit_price_decimal"])
        if type(count) is not int or count <= 0 or not price.is_finite() or price <= 0:
            raise ValueError("POSITIVE_OBSERVED_QUANTITY_AND_PRICE_REQUIRED")
        if (str(line["invoice_no"]) != invoice_id
                or str(line["customer_id"]) != str(invoice["customer_id"])
                or line["country"] != invoice["country"]
                or line["invoice_date_serial"] != invoice["invoice_date_serial"]):
            raise ValueError("INVOICE_LINE_IDENTITY_MISMATCH")
        if (type(line["source_row"]) is not int or line["source_row"] < 2
                or line["source_row"] in source_rows
                or not re.fullmatch(r"[0-9]{5}[A-Za-z]{0,2}", str(line["stock_code"]))
                or not str(line["description"]).strip()):
            raise ValueError("ELIGIBLE_SOURCE_LINE_REQUIRED")
        source_rows.add(line["source_row"])
        observation = (str(line["stock_code"]), line["description"], count, price)
        if observation in observations:
            raise ValueError("DUPLICATE_SOURCE_OBSERVATION")
        observations.add(observation)
        if "quantity_decimal" in line and Decimal(line["quantity_decimal"]) != count:
            raise ValueError("SOURCE_QUANTITY_MISMATCH")
        prices.append(price)
        quantity += count
        subtotal += Fraction(price) * count
    derived = invoice["derived"]
    if (subtotal != Fraction(Decimal(derived["exact_subtotal_gbp"]))
            or quantity != derived["total_quantity"]
            or min(prices) != Decimal(derived["unit_price_min_gbp"])
            or max(prices) != Decimal(derived["unit_price_max_gbp"])):
        raise ValueError("SAMPLE_ORACLE_MISMATCH")
    return {
        "role": "EXTERNAL_EVALUATION_ORACLE_NOT_RUNTIME_PRICING",
        "line_count": len(lines), "total_quantity": quantity,
        "exact_subtotal_gbp": derived["exact_subtotal_gbp"],
        "independent_arithmetic": "EXACT_RATIONAL_FROM_OBSERVED_DECIMAL_LEXEMES",
        "unit_price_min_gbp": str(min(prices)), "unit_price_max_gbp": str(max(prices)),
    }


def make_pack(root: Path, invoice: dict[str, Any], source_ref: str,
              policy_ref: str, oracle: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    draft, sealed = root / "draft", root / "pack"
    initialize_enterprise_quote_pilot_draft(draft)
    prior = load_enterprise_quote_pilot_pack(draft)
    manifest = json.loads((draft / "pack.json").read_text())
    profile = json.loads((draft / "profile.json").read_text())
    domain = json.loads((draft / "components/domain.json").read_text())
    knowledge = json.loads((draft / "components/knowledge.json").read_text())
    capability = json.loads((draft / "components/capability.json").read_text())
    template = TemplateRegistry().get("template:enterprise_quote@v2")
    owners = {item.kind: item.owner_id for item in prior.profile.change_family}
    domain_pack = DomainPack.enterprise_quote(template.ref)
    binding = EnterpriseBinding(
        organization_id=prior.profile.organization_id, quote_object_id=prior.quote_object_id,
        domain_pack_digest=domain_pack.digest,
        resources=tuple(EnterpriseResourceBinding(slot_id=slot, object_id=object_id,
                        domain_id=domain_id, owner_id=owners[owner_slot])
                        for slot, object_id, domain_id, owner_slot in (
                            ("launch_date", "claim:product.launch_date", "product", "launch_date"),
                            ("currency", "policy:finance.currency", "finance", "currency"),
                            ("product_plan", "claim:product.enterprise_plan", "product", "launch_date"),
                            ("quote_basket", "claim:product.quote_basket", "product", "launch_date"),
                            ("pricing_policy", "policy:finance.pricing", "finance", "currency"),
                        )),
    )
    manifest.update(schema_version="orgrebase.enterprise-quote-pilot-pack.v2",
                    enterprise_binding=binding.model_dump(mode="json"))
    # These literals are required by the existing v2 Pack schema. The report records
    # actual BODY_ACTOR_COMPATIBILITY execution and never claims authenticated use.
    manifest["boundaries"].update(execution_profile="AUTHENTICATED_SINGLE_TENANT",
                                   deployment_maturity="AUTHENTICATED_SINGLE_TENANT")
    manifest["scenario"]["label"] = "Controlled-local UCI transaction basket quote replay"
    manifest["runtime"]["quote_label"] = f"Observed UCI invoice {invoice['invoice_id']} basket quote replay"
    profile["change_family"] = []
    domain["projection"]["change_family"] = []
    knowledge["projection"]["proposed_values"] = []
    customer = f"customer:uci-anonymous-{invoice['customer_id']}"
    profile["default_task"]["customer_id"] = customer
    domain["projection"]["default_task"]["customer_id"] = customer
    profile["default_task"]["template_ref"] = template.ref
    domain["projection"]["default_task"]["template_ref"] = template.ref
    capability["projection"]["template"] = {"ref": template.ref, "digest": template.digest}
    capability["projection"]["capability_cards"] = [
        {"ref": card.ref, "digest": card.digest} for card in default_capability_cards(template.ref)]
    capability["projection"]["runtime_components"]["renderer"] = (
        "orgrebase.workspace.execution.QuoteRenderer@2.0.0")
    skus = sorted({str(line["stock_code"]) for line in invoice["lines"]})
    product_plan = f"Observed UCI invoice {invoice['invoice_id']} basket; SKUs: {', '.join(skus)}"
    values = {
        "product_plan": product_plan, "launch_date": INITIAL_DATE,
        "currency": "GBP",
        "price_band": ("Observed historical GBP unit price range "
                       f"{Decimal(oracle['unit_price_min_gbp']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)} to "
                       f"{Decimal(oracle['unit_price_max_gbp']).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}; "
                       "rounded for display; not a pricing rule"),
        "data_residency": "CONTROLLED TEST CONTEXT: no data residency policy supplied by UCI",
        "notice_required": False,
        "partner_terms": "CONTROLLED-TEST-NO-CUSTOMER-TERMS",
        "quote_compose_skill": "skill:enterprise-quote-compose@1.0",
        "public_message": "CONTROLLED TEST CONTEXT: discount and tax are simulated; no customer notice sent.",
        "quote_basket": quote_basket_input(invoice, source_ref),
        "pricing_policy": controlled_pricing_policy(policy_ref),
    }
    sources = knowledge["projection"]["source_values"]
    for slot, parent, object_id in (("quote_basket", "product_plan", "claim:product.quote_basket"),
                                    ("pricing_policy", "currency", "policy:finance.pricing")):
        source = next(item for item in sources if item["slot_id"] == parent)
        sources.append({**source, "slot_id": slot, "object_ref": object_id + "@v1", "sensitivity": "INTERNAL"})
    attribution = {}
    for item in knowledge["projection"]["source_values"]:
        slot = item["slot_id"]
        item["value"] = values[slot]
        item["raw_private_value"] = None
        observed = slot in {"product_plan", "currency", "price_band", "quote_basket"}
        item["source_id"] = source_ref if observed else policy_ref
        item["source_version"] = "r1"
        attribution[slot] = {"classification": "OBSERVED_SOURCE" if observed else "CONTROLLED_TEST_CONTEXT",
                             "source_ref": item["source_id"], "value": values[slot]}
    attribution["customer_id"] = {"classification": "ANONYMOUS_SOURCE_IDENTIFIER", "source_ref": source_ref,
                                   "value": customer}
    attribution["quote_basket"]["transformation"] = (
        "Scientific notation expanded exactly to plain decimal without rounding; original XML lexemes remain in source-transaction.json")
    for name, value in (("pack.json", manifest), ("profile.json", profile),
                        ("components/domain.json", domain), ("components/knowledge.json", knowledge),
                        ("components/capability.json", capability)):
        write_json(draft / name, value)
    seal_enterprise_quote_pilot_pack(draft, sealed)
    return sealed, attribution


def controlled_pricing_policy(source_ref: str, *, discount_bps: int = 0) -> dict[str, Any]:
    return {"discount_bps": discount_bps, "tax_bps": 2000, "tax_label": TAX_LABEL,
            "tax_mode": "EXCLUSIVE", "rounding": "HALF_UP", "source_ref": source_ref}


def quote_basket_input(invoice: dict[str, Any], source_ref: str) -> dict[str, Any]:
    """Adapt source notation, never its numeric value or immutable source record."""
    return {"currency": "GBP", "source_ref": source_ref, "items": [
        {"line_id": f"row{line['source_row']}", "sku": line["stock_code"],
         "description": line["description"], "quantity": line["quantity"],
         "unit_price": format(Decimal(line["unit_price_decimal"]), "f")}
        for line in invoice["lines"]]}


def pricing_oracle(invoice: dict[str, Any], *, discount_bps: int, tax_bps: int) -> dict[str, Any]:
    """Independent integer-pence calculation; never calls the production engine."""
    def half_up(value: Fraction) -> int:
        whole, remainder = divmod(value.numerator, value.denominator)
        return whole + (2 * remainder >= value.denominator)

    def money(pence: int) -> str:
        return f"{pence // 100}.{pence % 100:02d}"

    lines, subtotal = [], 0
    for line in invoice["lines"]:
        amount = half_up(Fraction(line["unit_price_decimal"]) * line["quantity"] * 100)
        subtotal += amount
        lines.append({"line_id": f"row{line['source_row']}", "sku": line["stock_code"],
                      "description": line["description"], "quantity": line["quantity"],
                      "unit_price": format(Decimal(line["unit_price_decimal"]), "f"), "line_total": money(amount)})
    discount = half_up(Fraction(subtotal * discount_bps, 10000))
    net = subtotal - discount
    tax = half_up(Fraction(net * tax_bps, 10000))
    return {"currency": "GBP", "minor_units": 2, "lines": lines, "subtotal": money(subtotal),
            "discount_rate_bps": discount_bps, "discount_amount": money(discount),
            "net_amount": money(net), "tax_rate_bps": tax_bps, "tax_amount": money(tax),
            "tax_label": TAX_LABEL, "total": money(net + tax)}


def verify_pricing(pricing: dict[str, Any], invoice: dict[str, Any], basket: dict[str, Any],
                   policy: dict[str, Any]) -> dict[str, Any]:
    expected = pricing_oracle(invoice, discount_bps=policy["discount_bps"], tax_bps=policy["tax_bps"])
    expected.update(basket_source_ref=basket["source_ref"], policy_source_ref=policy["source_ref"],
                    basket_digest=sha256_digest(basket), policy_digest=sha256_digest(policy))
    for key, value in expected.items():
        if pricing.get(key) != value:
            raise AssertionError(f"RUNTIME_PRICING_ORACLE_MISMATCH:{key}")
    return {"status": "PASS", "arithmetic": "INDEPENDENT_FRACTION_AND_INTEGER_PENCE_HALF_UP", **expected}


def _proposal(workspace: WorkspaceService, event_id: str, value: dict[str, Any], source_ref: str) -> ChangeProposalInput:
    field = next(item for item in change_options(workspace)["fields"] if item["slot_id"] == "pricing_policy")
    return ChangeProposalInput(event_id=event_id, slot_id="pricing_policy", value=value, source_ref=source_ref,
                               base_version=field["current"]["version"], base_digest=field["current"]["digest"])


def _refused(action: Callable[[], Any], expected: str, exception_type: type[Exception]) -> dict[str, str]:
    try:
        action()
    except exception_type as exc:
        if expected not in str(exc):
            raise
        return {"status": "REFUSED", "reason": expected}
    raise AssertionError(f"EXPECTED_REFUSAL_MISSING:{expected}")


def prepare_case(root: Path, invoice: dict[str, Any], source: dict[str, Any], sample_digest: str) -> dict[str, Any]:
    """Seal source-bound demo inputs without executing approvals or forming a quote."""
    oracle = basket_oracle(invoice)
    root.mkdir()
    source_path = root / "source-transaction.json"
    source_digest = write_json(source_path, {"source": source, "sample_digest": sample_digest, "invoice": invoice})
    source_path.chmod(0o444)
    source_ref = f"source:uci-online-retail:invoice:{invoice['invoice_id']}#{source_digest}"
    policy = {"classification": "CONTROLLED_TEST_CONTEXT", "initial_launch_date": INITIAL_DATE,
              "initial_discount_bps": 0, "proposed_discount_bps": 1000, "rejected_discount_bps": 2000,
              "tax_bps": 2000, "tax_label": TAX_LABEL,
              "real_customer_policy": False, "approval_actor": "SCRIPTED_EXISTING_OWNER_ID_NOT_A_HUMAN_APPROVAL",
              "notice_required": False, "data_residency_policy": None, "partner_terms": None}
    policy_digest = write_json(root / "controlled-policy.json", policy)
    policy_ref = f"source:controlled-replay-policy#{policy_digest}"
    pack_path, attribution = make_pack(root, invoice, source_ref, policy_ref, oracle)
    write_json(root / "field-attribution.json", attribution)
    return {"pack_path": str(pack_path), "oracle": oracle, "attribution": attribution,
            "source_path": str(source_path), "source_digest": source_digest, "source_ref": source_ref,
            "policy_digest": policy_digest, "policy_ref": policy_ref}


def run_case(root: Path, invoice: dict[str, Any], source: dict[str, Any], sample_digest: str,
             *, measurement: dict[str, Any] | None = None) -> dict[str, Any]:
    measurement = measurement if measurement is not None else {"phases": []}
    def measured(name, action):
        with _phase(measurement, name):
            return action()
    prepared = measured("prepare_inputs", lambda: prepare_case(root, invoice, source, sample_digest))
    attribution = prepared["attribution"]
    policy_ref = prepared["policy_ref"]
    basket = attribution["quote_basket"]["value"]
    initial_policy = attribution["pricing_policy"]["value"]
    changed_policy = controlled_pricing_policy(policy_ref, discount_bps=1000)
    with _phase(measurement, "initialize_runtime"):
        runtime = load_enterprise_quote_pilot_pack(prepared["pack_path"])
        workspace = WorkspaceService(store_path=root / "workspace.sqlite", runtime_configuration=runtime,
                                     review_duration_seconds=0)
    try:
        formation = measured("form_quote", workspace.form_quote)
        before = workspace.current_quote()
        write_json(root / "quote-before.json", before.model_dump(mode="json"))
        with _phase(measurement, "verify_initial_quote"):
            for slot in ("product_plan", "launch_date", "currency", "price_band", "customer_id"):
                assert before.payload[slot] == attribution[slot]["value"], f"INITIAL_FIELD_MISMATCH:{slot}"
            before_oracle = verify_pricing(before.payload["pricing"], invoice, basket, initial_policy)
        change = _proposal(workspace, "controlled-discount", changed_policy, policy_ref)
        submitted = measured("submit_change", lambda: submit_change(workspace, change))
        preview = measured("preview_change", lambda: workspace.preview_change(change.event_id))
        with _phase(measurement, "verify_preview_read_only"):
            assert workspace.current_quote().digest == before.digest, "PREVIEW_MUTATED_QUOTE"
        with _phase(measurement, "refuse_unapproved_apply"):
            unapproved = _refused(lambda: workspace.apply_approved_change(
                change.event_id, approval_digest="sha256:" + "0" * 64), "WORKSPACE_APPROVAL_REQUIRED", RuntimeError)
            assert workspace.current_quote().digest == before.digest, "UNAPPROVED_APPLY_MUTATED_QUOTE"
        with _phase(measurement, "refuse_wrong_owner"):
            wrong_owner = _refused(lambda: workspace.approve_change(
                change.event_id, actor_id="controlled:wrong-owner", preview_digest=preview.preview.digest),
                "WORKSPACE_APPROVER_MISMATCH", AuthorizationError)
            assert workspace.current_quote().digest == before.digest, "WRONG_OWNER_MUTATED_QUOTE"
        approval = measured("approve_change", lambda: workspace.approve_change(
            change.event_id, actor_id=workspace.change_owner[change.event_id], preview_digest=preview.preview.digest))
        outcome = measured("apply_change", lambda: workspace.apply_approved_change(
            change.event_id, approval_digest=approval["approval_digest"]))
        after = workspace.current_quote()
        with _phase(measurement, "verify_changed_quote"):
            assert after.digest != before.digest, "APPROVED_DISCOUNT_DID_NOT_CHANGE_QUOTE"
            after_oracle = verify_pricing(after.payload["pricing"], invoice, basket, changed_policy)
            assert before.payload["pricing"]["lines"] == after.payload["pricing"]["lines"], "BASKET_CHANGED"
            assert before.payload["pricing"]["basket_digest"] == after.payload["pricing"]["basket_digest"]
            unchanged_fields = [slot for slot in before.payload
                                if slot not in {"pricing", "rebased_from", "rebase_change_set", "context_manifest"}]
            assert all(before.payload[slot] == after.payload[slot] for slot in unchanged_fields), "UNRELATED_QUOTE_FIELD_CHANGED"
        rejected = _proposal(workspace, "rejected-discount", controlled_pricing_policy(policy_ref, discount_bps=2000), policy_ref)
        measured("submit_rejected_change", lambda: submit_change(workspace, rejected))
        measured("preview_rejected_change", lambda: workspace.preview_change(rejected.event_id))
        rejection = measured("reject_change", lambda: workspace.reject_change(
            rejected.event_id, actor_id=workspace.change_owner[rejected.event_id],
            reason="CONTROLLED TEST: retain previously approved 10 percent discount"))
        with _phase(measurement, "verify_rejection_and_source"):
            assert workspace.current_quote().digest == after.digest, "REJECTION_MUTATED_QUOTE"
            assert "sha256:" + hashlib.sha256(Path(prepared["source_path"]).read_bytes()).hexdigest() == prepared["source_digest"]
        with _phase(measurement, "persist_evidence"):
            write_json(root / "quote-after.json", after.model_dump(mode="json"))
            write_json(root / "runtime-evidence.json", {"formation": formation.model_dump(mode="json"),
                       "submission": submitted, "preview": preview.model_dump(mode="json"), "approval": approval,
                       "outcome": outcome, "rejection": rejection})
        result = {"invoice_id": str(invoice["invoice_id"]), "status": "PASS", "claim_ceiling": CLAIM_CEILING,
                  "source_digest": prepared["source_digest"], "source_ref": prepared["source_ref"],
                  "policy_digest": prepared["policy_digest"],
                  "pack_digest": runtime.pack_digest, "synthetic_organization": runtime.profile.synthetic,
                  "initial_formation_status": formation.status, "initial_deliverable_ref": formation.deliverable_ref,
                  "before_quote_ref": f"{before.id}@{before.version}", "before_quote_digest": before.digest,
                  "after_quote_ref": f"{after.id}@{after.version}", "after_quote_digest": after.digest,
                  "preview_digest": preview.preview.digest, "approval_digest": approval["approval_digest"],
                  "changed_field": "pricing_policy", "before_discount_bps": 0, "after_discount_bps": 1000,
                  "controlled_tax_bps": 2000, "tax_label": TAX_LABEL,
                  "before_pricing": before.payload["pricing"], "after_pricing": after.payload["pricing"],
                  "pricing_verification": {"before": before_oracle, "after": after_oracle},
                  "unchanged_fields": unchanged_fields, "basket_unchanged": True, "currency_unchanged": "GBP",
                  "price_band_unchanged": before.payload["price_band"], "external_oracle": prepared["oracle"],
                  "runtime_price_calculation": "DETERMINISTIC_QUOTE_RENDERER",
                  "negative_checks": {"unapproved_apply": unapproved, "wrong_owner_approval": wrong_owner,
                                      "rejection_preserves_quote": {"status": "PASS", "digest": after.digest}},
                  "negative_check_count": 3,
                  "evidence_directory": root.name, "measurement": measurement}
        write_json(root / "case-report.json", result)
        return result
    finally:
        workspace.close()


def load_sample(sample_path: Path) -> tuple[dict[str, Any], str]:
    raw = sample_path.read_bytes()
    if sample_path.resolve() == DEFAULT_SAMPLE.resolve():
        manifest = json.loads((RETAINED_DATASET / "dataset-manifest.json").read_text())
        if (hashlib.sha256(raw).hexdigest() != manifest["sample"]["sha256"]
                or len(raw) != manifest["sample"]["bytes"]):
            raise ValueError("RETAINED_PUBLIC_SAMPLE_DIGEST_MISMATCH")
    sample = json.loads(raw)
    if any(sample.get("source", {}).get(key) != value for key, value in SOURCE_PINS.items()):
        raise ValueError("PUBLIC_SOURCE_PIN_OR_NATURE_MISMATCH")
    if sample.get("schema_version") != "1.0" or sample.get("rows_scanned") != 541909:
        raise ValueError("PUBLIC_SAMPLE_SCHEMA_OR_SCAN_COUNT_MISMATCH")
    invoices = sample["invoices"]
    population = sample.get("invoice_population", {})
    if (population.get("sampled_invoices") != len(invoices)
            or population.get("sampled_rows") != sum(len(item["lines"]) for item in invoices)):
        raise ValueError("PUBLIC_SAMPLE_POPULATION_MISMATCH")
    rows = [line["source_row"] for item in invoices for line in item["lines"]]
    if len(rows) != len(set(rows)):
        raise ValueError("PUBLIC_SAMPLE_SOURCE_ROWS_REUSED")
    if not invoices or len({str(item["invoice_id"]) for item in invoices}) != len(invoices):
        raise ValueError("NONEMPTY_UNIQUE_INVOICES_REQUIRED")
    for invoice in invoices:
        basket_oracle(invoice)
    return sample, "sha256:" + hashlib.sha256(raw).hexdigest()


def prepare_demo(sample_path: Path, output: Path, invoice_id: str) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"OUTPUT_MUST_BE_NEW:{output}")
    sample, digest = load_sample(sample_path)
    selected = next((item for item in sample["invoices"] if item["invoice_id"] == invoice_id), None)
    if selected is None:
        raise ValueError(f"DEMO_INVOICE_NOT_IN_SAMPLE:{invoice_id}")
    output.parent.mkdir(parents=True, exist_ok=True)
    prepared = prepare_case(output, selected, sample["source"], digest)
    pack = str(Path(prepared["pack_path"]).resolve())
    database = str((output / "demo-workspace.sqlite").resolve())
    command = shlex.join(["env", f"ORGREBASE_ENTERPRISE_PACK={pack}", f"ORGREBASE_WORKSPACE_DB={database}",
                          "ORGREBASE_CHANGE_MODEL_PROVIDER=local-deterministic", sys.executable,
                          "-m", "orgrebase.cli", "serve", "--local-demo", "--host", "127.0.0.1", "--port", "8081"])
    result = {"status": "PREPARED", "invoice_id": invoice_id, "pack_path": pack, "sample_digest": digest,
              "claim_ceiling": CLAIM_CEILING, "formation": "NOT_RUN", "approval": "NOT_RUN",
              "source_verification": "NOT_RUN_SOURCE_BINDING_AND_SAMPLE_INTEGRITY_ONLY",
              "tax_label": TAX_LABEL, "start_command": command, "browser_url": "http://127.0.0.1:8081"}
    write_json(output / "demo.json", result)
    return result


def run_replay(sample_path: Path, output: Path, *, limit: int | None = None) -> dict[str, Any]:
    if os.environ.get("ORGREBASE_CHANGE_MODEL_PROVIDER", "local-deterministic") != "local-deterministic":
        raise ValueError("PUBLIC_REPLAY_REQUIRES_LOCAL_DETERMINISTIC_PROVIDER")
    if output.exists():
        raise FileExistsError(f"OUTPUT_MUST_BE_NEW:{output}")
    sample, digest = load_sample(sample_path)
    invoices = sample["invoices"]
    if limit is not None:
        if limit < 1:
            raise ValueError("LIMIT_MUST_BE_POSITIVE")
        invoices = invoices[:limit]
    output.mkdir(parents=True)
    runner_source = Path(__file__).read_bytes()
    (output / "runner-source.py").write_bytes(runner_source)
    cases = [{"plan_index": index, "invoice_id": str(invoice["invoice_id"]),
              "planned_line_count": len(invoice["lines"]), "input_digest": sha256_digest(invoice),
              "source_rows": [line["source_row"] for line in invoice["lines"]],
              "evidence_directory": f"case-{index:03d}-{invoice['invoice_id']}",
              "status": "INCOMPLETE", "execution_state": "NOT_STARTED"}
             for index, invoice in enumerate(invoices, 1)]
    plan = {"schema_version": "orgrebase.public-quote-replay-plan.v1", "created_at": _utc_now(),
            "source": sample["source"], "sample_digest": digest,
            "sample_invoice_count": len(sample["invoices"]), "explicit_limit": limit,
            "selection": "EXISTING_SAMPLE_ORDER_WITH_OPTIONAL_EXPLICIT_PREFIX_LIMIT",
            "cases": [{key: value for key, value in case.items() if key not in {"status", "execution_state"}}
                      for case in cases],
            "runner_sha256": hashlib.sha256(runner_source).hexdigest(), "runner_source": "runner-source.py",
            "runtime": {"python": platform.python_version(), "system": platform.system(),
                        "machine": platform.machine()},
            "timing": {"clock": "time.perf_counter_ns", "unit": "NANOSECOND", "concurrency": 1,
                       "case_boundary": "PREPARE_INPUTS_THROUGH_RUNTIME_CLOSE_AND_CASE_EVIDENCE",
                       "excluded": ["SAMPLE_PRECHECK", "PLAN_AND_AGGREGATE_REPORT_WRITES"],
                       "human_labor": "NOT_MEASURED", "monetary_cost": "NOT_MEASURED",
                       "production_capacity": "NOT_MEASURED", "enterprise_roi_proven": False}}
    plan_digest = write_json(output / "run-plan.json", plan)
    report = {"schema_version": "orgrebase.public-quote-replay.v2", "status": "RUNNING",
              "claim_ceiling": CLAIM_CEILING, "source": sample["source"], "sample_digest": digest,
              "source_verification": "NOT_RUN_SOURCE_BINDING_AND_SAMPLE_INTEGRITY_ONLY",
              "source_verification_note": "Pinned metadata is checked, but this runner does not re-open or authenticate the original XLSX",
              "sample_invoice_count": len(sample["invoices"]), "executed_invoice_count": 0,
              "negative_check_count": 0, "run_plan_digest": plan_digest,
              "measurement": {"schema_version": "orgrebase.public-quote-workflow-measurement.v1",
                              "started_at": _utc_now(), "finished_at": None, **plan["timing"]},
              "live_model": "NOT_RUN", "native_agentteams": "NOT_RUN", "external_effects": "DISABLED",
              "execution_profile": "CONTROLLED_LOCAL_DETERMINISTIC",
              "approval_identity_mode": "BODY_ACTOR_COMPATIBILITY",
              "approval_actor": "SCRIPTED_EXISTING_OWNER_ID_NOT_A_HUMAN_APPROVAL",
              "pack_boundary_note": "v2 schema uses AUTHENTICATED_SINGLE_TENANT literals; this run does not validate authentication",
              "runtime_price_calculation": "DETERMINISTIC_QUOTE_RENDERER",
              "controlled_tax_bps": 2000, "tax_label": TAX_LABEL,
              "discount_change_bps": {"before": 0, "after": 1000},
              "limitations": ["Historical transactions reconstructed as quote inputs, not original customer quote requests",
                              "Organization, owners, schedule, notice and legal context remain synthetic controlled fixtures",
                              "Line-rounded prices, discount and tax are calculated by the production quote renderer and independently checked",
                              "Quantity retains source SKU packaging units; summed quantities do not count individual physical pieces",
                              "20 percent tax and 10 percent discount are controlled assumptions, not historical customer terms or tax advice",
                              "No FX, tax-law inference, live customer, production deployment, or ROI validation"],
              "cases": cases}
    _write_checkpoint(output, report)
    interrupted = False
    for index, (invoice, case) in enumerate(zip(invoices, cases, strict=True), 1):
        case_root = output / case["evidence_directory"]
        measurement = {"started_at": _utc_now(), "finished_at": None, "elapsed_ns": None, "phases": []}
        case.update(execution_state="RUNNING", measurement=measurement)
        report["executed_invoice_count"] += 1
        _write_checkpoint(output, report)
        started = time.perf_counter_ns()
        try:
            result = run_case(case_root, invoice, sample["source"], digest, measurement=measurement)
            case.update(result)
        except (Exception, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            phase = measurement["phases"][-1]["phase"] if measurement["phases"] else "case"
            case.update(status="INCOMPLETE" if interrupted else "FAILED", error=_public_error(exc, phase))
        finally:
            measurement.update(finished_at=_utc_now(), elapsed_ns=max(0, time.perf_counter_ns() - started))
            case["execution_state"] = "FINISHED"
            case["measurement"] = measurement
            case["evidence_files"] = {
                name: "sha256:" + hashlib.sha256((case_root / name).read_bytes()).hexdigest()
                for name in ("source-transaction.json", "controlled-policy.json", "field-attribution.json",
                             "pack/pack.json", "quote-before.json", "quote-after.json", "runtime-evidence.json")
                if (case_root / name).is_file()}
            case_root.mkdir(exist_ok=True)
            write_json(case_root / "case-report.json", case)
            report["negative_check_count"] = sum(item.get("negative_check_count", 0) for item in cases)
            _write_checkpoint(output, report)
        print(json.dumps({"case": index, "invoice_id": invoice["invoice_id"], "status": case["status"]}), flush=True)
        if interrupted:
            break
    report["status"] = "INCOMPLETE" if interrupted else "FAIL" if any(case["status"] != "PASS" for case in cases) else "PASS"
    report["measurement"].update(finished_at=_utc_now(), interrupted=interrupted)
    _write_checkpoint(output, report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE,
                        help="Default: repository-retained, checksum-verified public UCI sample")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Explicit smoke-run subset; full sample is the default")
    parser.add_argument("--demo-invoice", help="Prepare one sealed priced pack only; no quote formation or approval")
    args = parser.parse_args()
    if args.demo_invoice:
        if args.limit is not None:
            parser.error("--limit cannot be combined with --demo-invoice")
        print(json.dumps(prepare_demo(args.sample, args.output, args.demo_invoice), ensure_ascii=False, indent=2))
        return
    try:
        report = run_replay(args.sample, args.output, limit=args.limit)
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "INCOMPLETE" if isinstance(exc, KeyboardInterrupt) else "FAIL",
                          "error": _public_error(exc, "setup_or_checkpoint")}))
        raise SystemExit(130 if isinstance(exc, KeyboardInterrupt) else 1) from None
    print(json.dumps({"status": report["status"], "executed_invoice_count": report["executed_invoice_count"],
                      "report": str(args.output / "report.json")}))
    if report["status"] != "PASS":
        raise SystemExit(130 if report["measurement"].get("interrupted") else 1)


if __name__ == "__main__":
    main()
