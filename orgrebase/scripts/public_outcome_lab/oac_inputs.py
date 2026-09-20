"""Author this profile's zero-effect inputs with the current public OAC library."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import oac
from oac.canonical import seal_resource
from oac.compiler import compile_change
from oac.models import (
    AdmissionStatus,
    BoundaryStatus,
    ChangeDelta,
    CompletenessManifest,
    EffectCeiling,
    ImpactRule,
    ObservedValue,
    OrganizationNode,
    OrganizationSnapshot,
    OrganizationSnapshotSpec,
    Principal,
    ResourceMetadata,
    RoleDefinition,
    SemanticChangeSet,
    SemanticChangeSetSpec,
)
from oac.verifier import verify_change

from .artifacts import PROFILE, file_identity, load, seal_directory, verify_directory, write
from .baselines import baseline_roles


def build_inputs(prepared: Path, prepared_root: str, output: Path, source: Path, created_at: str) -> str:
    verify_directory(prepared, prepared_root, "prepared-task")
    if Path(oac.__file__).resolve().parent != (source / "src/oac").resolve():
        raise ValueError("PUBLIC_LAB_OAC_IMPORT_ORIGIN_MISMATCH")
    at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if at.utcoffset() is None:
        raise ValueError("PUBLIC_LAB_EXPLICIT_CREATION_TIME_REQUIRED")
    candidate = load(prepared / "candidate.json")
    provenance = load(prepared / "source.json")
    source_url = provenance["repository"] + "/tree/" + provenance["revision"]
    output.mkdir(exist_ok=False)

    def metadata(identifier):
        return ResourceMetadata(
            id=identifier,
            namespace="orgrebase.lab.retail",
            revision=1,
            ownerRef="role:review",
            governanceRef="governance:local-lab",
            createdAt=at,
            sourceRefs=(source_url, "orgrebase:local-proposed-mapping"),
        )

    roles, principals = [], []
    for role in ("review", "cancel"):
        roles.append(
            RoleDefinition(
                roleId="role:" + role,
                domainRef="domain:retail",
                mission="Review " + role + " obligations in a disposable environment",
                responsibilityTypes=(role,),
                requiredQualifications=("qualification:" + role,),
                effectCeiling=EffectCeiling.ZERO_EFFECT,
            )
        )
        principals.append(
            Principal(
                principalId="principal:" + role,
                principalType="agent",
                status="active",
                eligibleRoleRefs=("role:" + role,),
                qualificationRefs=("qualification:" + role,),
            )
        )
    nodes = [
        OrganizationNode(
            nodeId="request:113",
            nodeType="customer-request",
            domainRef="domain:retail",
            ownerRoleRef="role:review",
        )
    ]
    rules = []
    for order_id in candidate["pending_orders"]:
        if not order_id.startswith("#W") or not order_id[2:].isascii() or not order_id[2:].isdigit():
            raise ValueError("PUBLIC_LAB_ORDER_IDENTIFIER_INVALID")
        key = order_id[2:]
        nodes.append(
            OrganizationNode(
                nodeId="order:" + key,
                nodeType="retail-order",
                domainRef="domain:retail",
                ownerRoleRef="role:cancel",
            )
        )
        rules.append(
            ImpactRule(
                admissionStatus=AdmissionStatus.ADMITTED,
                ruleId="rule:cancel:" + key,
                semanticType="retail.cancel_requested",
                afterValues=("confirmed",),
                targetRef="order:" + key,
                requiredRoleRef="role:cancel",
                obligationType="cancel_order",
                requiredEvidence=("evidence:cancel:" + key,),
            )
        )
    rules.append(
        ImpactRule(
            admissionStatus=AdmissionStatus.ADMITTED,
            ruleId="rule:review",
            semanticType="retail.cancel_requested",
            afterValues=("confirmed",),
            targetRef="request:113",
            requiredRoleRef="role:review",
            obligationType="review_order",
            requiredEvidence=("evidence:review",),
        )
    )
    snapshot = seal_resource(
        OrganizationSnapshot(
            metadata=metadata("snapshot:retail-113"),
            spec=OrganizationSnapshotSpec(
                nodes=tuple(nodes),
                roleDefinitions=tuple(roles),
                principals=tuple(principals),
                dependencyEdges=(),
                impactRules=tuple(rules),
                separationConstraints=(),
                completeness=CompletenessManifest(
                    status=BoundaryStatus.COMPLETE,
                    coveredNodeRefs=tuple(node.node_id for node in nodes),
                    coveredRelationTypes=(),
                    maxDepth=1,
                    knownGaps=(),
                    discoveryRoleRef="role:review",
                ),
            ),
        )
    )
    change = seal_resource(
        SemanticChangeSet(
            metadata=metadata("change:retail-113"),
            spec=SemanticChangeSetSpec(
                admissionStatus=AdmissionStatus.ADMITTED,
                demandRef="demand:retail-113",
                subjectRef="request:113",
                semanticType="retail.cancel_requested",
                deltas=(
                    ChangeDelta(
                        path="/request",
                        operation="replace",
                        before=ObservedValue(state="known", value="pending"),
                        after=ObservedValue(state="known", value="confirmed"),
                        beforeVersion="1",
                        afterVersion="2",
                    ),
                ),
                observedAt=at,
                effectiveAt=at,
                sourceRef=source_url,
                scopeRefs=tuple(node.node_id for node in nodes),
                reason="Controlled scripted confirmation of the mapped public task state operations",
            ),
        )
    )
    plan = compile_change(snapshot, change, profile=PROFILE)
    certificate = verify_change(snapshot, change, plan, profile=PROFILE)
    if certificate.spec.verdict.value != "ACCEPT":
        raise ValueError("PUBLIC_LAB_CURRENT_PLAN_NOT_ACCEPTED")
    for name, value in (
        ("snapshot", snapshot),
        ("change", change),
        ("plan", plan),
        ("plan-certificate", certificate),
    ):
        write(output / (name + ".json"), value.model_dump(mode="json", by_alias=True))
    write(
        output / "baselines.json",
        baseline_roles(
            snapshot.model_dump(mode="json", by_alias=True),
            plan.model_dump(mode="json", by_alias=True),
        ),
    )
    files = [
        *sorted((source / "src/oac").rglob("*.py")),
        source / "profiles/change-profiles/retail-cancellation-review-v0.1/profile.json",
    ]
    write(
        output / "producer.json",
        {
            "prepared_root": prepared_root,
            "profile": PROFILE,
            "oac_sources": {path.relative_to(source).as_posix(): file_identity(path) for path in files},
            "created_at": created_at,
            "claim": "PROJECT_AUTHORED_ORGANIZATION_MAPPING_NOT_DATASET_GOLD",
        },
    )
    return seal_directory(output, "oac-inputs")


def main():
    parser = argparse.ArgumentParser()
    for name in ("prepared", "prepared-root", "output", "oac-source", "created-at"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    root = build_inputs(
        Path(args.prepared), args.prepared_root, Path(args.output), Path(args.oac_source), args.created_at
    )
    print(json.dumps({"artifact_root": root, "output": str(Path(args.output).resolve())}, indent=2))


if __name__ == "__main__":
    main()
