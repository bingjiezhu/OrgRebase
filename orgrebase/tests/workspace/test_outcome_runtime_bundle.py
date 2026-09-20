"""The approved pre-execution package is required before any environment call."""

from __future__ import annotations

from copy import deepcopy

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError
from orgrebase.workspace.evolution_evidence import _seal
from orgrebase.workspace.outcome_runtime import build_runtime_bundle, verify_runtime_bundle
from tests.workspace.test_outcome_lab import setup as setup


def reseal(package):
    for key in ("mapping", "grant", "binding", "bundle"):
        package[key] = _seal(package[key])
    package["digest"] = sha256_digest({key: value for key, value in package.items() if key != "digest"})


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("bundle", "spec", "systemRoles"), ["review", "execute", "administrator"]),
        (("bundle", "spec", "tools", 0, "role"), "administrator"),
        (("bundle", "spec", "tools", 0, "request", "arguments", "order"), "another-order"),
        (("bundle", "spec", "tools", 1, "mutates_state"), False),
        (("binding", "spec", "planEffectCeiling"), "production_write"),
        (("bundle", "spec", "executionScope"), "production"),
        (("bundle", "spec", "productionAuthority"), True),
        (("bundle", "spec", "productionAuthority"), 0),
        (("bundle", "spec", "budget", "write_calls"), 999),
        (("bundle", "spec", "workUnitPredecessors", "wu:execute"), ["wu:review"]),
        (("bundle", "spec", "seedRoot"), sha256_digest("other-seed")),
        (("bundle", "spec", "environmentBuildDigest"), sha256_digest("other-environment")),
        (("binding", "spec", "planRef", "digest"), sha256_digest("other-plan")),
        (("grant", "spec", "approvalReceipt", "controller"), "test:actor"),
        (("grant", "spec", "actingAuthority"), "test:other-actor"),
        (("mapping", "spec", "subjects", "order:one"), "/another-order"),
        (("bundle", "metadata", "ownerRef"), "test:attacker"),
        (("grant", "metadata", "namespace"), "test:another-namespace"),
        (("binding", "spec", "profileBinding", "profileDigest"), sha256_digest("other-profile")),
    ],
)
def test_changed_approved_package_is_rejected_before_any_call(setup, path, value):
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    changed = deepcopy(approval["runtime_bundle"])
    target = changed
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    reseal(changed)
    with pytest.raises(IntegrityError, match="APPROVED_RUNTIME_BUNDLE_MISMATCH"):
        lab.run(approval["token"], requests, runtime_bundle=changed)
    assert environment.call_count == 0
    assert environment.snapshot()["order"]["state"] == "pending"
    # A rejected use consumes the one-use authority; retrying cannot recover it.
    with pytest.raises(IntegrityError, match="EXPLICIT_ONE_USE_APPROVAL"):
        lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    assert environment.call_count == 0


def test_approved_package_precedes_real_effect_and_is_exactly_retained(setup):
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    approved = deepcopy(approval["runtime_bundle"])
    assert environment.call_count == 0
    assert approval["receipt"]["recorded_at"].endswith("Z")
    assert approved["grant"]["spec"]["approvalReceipt"] == approval["receipt"]
    assert approved["binding"]["spec"]["planEffectCeiling"] == "zero_effect"
    assert approved["bundle"]["spec"]["productionAuthority"] is False
    verify_runtime_bundle(approved, lab.mapping, approval["receipt"], acting_authority="test:actor")
    receipt = lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    assert receipt["runtime_bundle"] == approved
    assert receipt["verdict"] == "ACCEPT"
    assert receipt["initial_root"] != receipt["final_root"]
    assert environment.call_count == 2
    assert environment.snapshot()["order"]["state"] == "pending"
    lab.verify_receipt(receipt)


def test_missing_package_has_no_execution_fallback(setup):
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    with pytest.raises(TypeError, match="runtime_bundle"):
        lab.run(approval["token"], requests)
    assert environment.call_count == 0
    # Argument rejection occurs before entering the controller or consuming its token.
    receipt = lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    assert receipt["verdict"] == "ACCEPT"


def test_self_consistent_package_with_replaced_metadata_is_not_an_approval(setup):
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    original = approval["runtime_bundle"]
    metadata = deepcopy(original["binding"]["metadata"])
    metadata["ownerRef"] = "test:attacker"
    alternate = build_runtime_bundle(
        lab.mapping,
        approval["receipt"],
        plan_ref=original["binding"]["spec"]["planRef"],
        certificate_ref=original["binding"]["spec"]["planCertificateRef"],
        metadata=metadata,
        acting_authority="test:actor",
    )
    verify_runtime_bundle(alternate, lab.mapping, approval["receipt"], acting_authority="test:actor")
    with pytest.raises(IntegrityError, match="APPROVED_RUNTIME_BUNDLE_MISMATCH"):
        lab.run(approval["token"], requests, runtime_bundle=alternate)
    assert environment.call_count == 0


def test_mutating_caller_package_during_dispatch_cannot_change_execution_or_receipt(setup):
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    caller_package = approval["runtime_bundle"]
    original_package = deepcopy(caller_package)
    original_call = environment.call

    def mutate_caller_buffer(request, *, timeout):
        if environment.call_count == 0:
            caller_package["bundle"]["spec"]["tools"].clear()
            caller_package["bundle"]["spec"]["budget"]["write_calls"] = 0
            caller_package["bundle"]["spec"]["systemRoles"] = []
            caller_package["grant"]["spec"]["actingAuthority"] = "test:attacker"
            reseal(caller_package)
        return original_call(request, timeout=timeout)

    environment.call = mutate_caller_buffer
    receipt = lab.run(approval["token"], requests, runtime_bundle=caller_package)
    assert caller_package != original_package
    assert receipt["runtime_bundle"] == original_package
    assert receipt["verdict"] == "ACCEPT" and receipt["write_calls"] == 1
    assert environment.call_count == 2
    lab.verify_receipt(receipt)


def test_receipt_package_cannot_be_replaced_after_observed_execution(setup):
    lab, _, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    receipt = lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    changed = deepcopy(receipt)
    changed["runtime_bundle"]["bundle"]["spec"]["budget"]["write_calls"] = 999
    reseal(changed["runtime_bundle"])
    changed["digest"] = sha256_digest({key: value for key, value in changed.items() if key != "digest"})
    with pytest.raises(IntegrityError, match="RUNTIME_BUNDLE"):
        lab.verify_receipt(changed)


@pytest.mark.parametrize("replacement", ("owner", "namespace", "plan-id", "certificate-id", "plan-revision"))
def test_self_consistent_receipt_package_cannot_change_the_controllers_original_metadata(setup, replacement):
    lab, environment, requests = setup
    approval = lab.approve("oac", authority="test:controller")
    receipt = lab.run(approval["token"], requests, runtime_bundle=approval["runtime_bundle"])
    changed = deepcopy(receipt)
    original = changed["runtime_bundle"]
    metadata = deepcopy(original["binding"]["metadata"])
    plan_ref = deepcopy(original["binding"]["spec"]["planRef"])
    certificate_ref = deepcopy(original["binding"]["spec"]["planCertificateRef"])
    if replacement == "owner":
        metadata["ownerRef"] = "test:attacker"
    elif replacement == "namespace":
        metadata["namespace"] = "test:another-namespace"
        plan_ref["namespace"] = metadata["namespace"]
        certificate_ref["namespace"] = metadata["namespace"]
    elif replacement == "plan-id":
        plan_ref["resourceId"] = "test:another-plan"
    elif replacement == "certificate-id":
        certificate_ref["resourceId"] = "test:another-certificate"
    else:
        plan_ref["revision"] += 1
    changed["runtime_bundle"] = build_runtime_bundle(
        lab.mapping,
        changed["approval_receipt"],
        plan_ref=plan_ref,
        certificate_ref=certificate_ref,
        metadata=metadata,
        acting_authority="test:actor",
    )
    changed["digest"] = sha256_digest({key: value for key, value in changed.items() if key != "digest"})
    with pytest.raises(IntegrityError, match="RUNTIME_BUNDLE"):
        lab.verify_receipt(changed)
    assert environment.call_count == 2
