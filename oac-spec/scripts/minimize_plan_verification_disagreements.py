#!/usr/bin/env python3
"""Freeze and replay deletion-minimal reproductions of three fixed-Plan incidents."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

import check_plan_verification_parity as parity
import rfc8785

ROOT = Path(__file__).resolve().parents[1]
COORDINATE = "v0.1-repro-5"
OUTPUT = ROOT / "experiments/plan-verification-portability" / COORDINATE
PREDECESSOR = "experiments/plan-verification-portability/v0.1-repro-4"
PREDECESSOR_DIGEST = "sha256:808944c845587955f91446b2db34fb6406a1c8447006cff44c86b4f163451e54"
PREDECESSOR_RAW_DIGEST = "sha256:4a19d6fbc37cd727872b0ab709040636852797c9be6a90404a20fb326cb2c508"
ALGORITHM = "oac.fixed-plan.deletion-sweep/v1"
RESOURCES = ("snapshot", "change", "plan")
INCIDENTS = {
    "DIS-PV-001": "sha256:de9c1e3f55e72b4f7cb58be71dfd606b9c39a6373e34e9bc107797d3eb14440c",
    "DIS-PV-002": "sha256:a6b5773c40a4292288755f602564d9baa1db0b2f3eb97c6e14e16a57a0efdea9",
    "DIS-PV-003": "sha256:34db13526d53fcb22f67ecfa32381a2a783491a471957350718d5df0210f5ec6",
}
MAX_ATTEMPTS = 10_000
MAX_SECONDS = 3_600
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TREE_BYTES = 96 * 1024 * 1024
MAX_HISTORY_DEPTH = 16


class ReproductionFailure(RuntimeError):
    """The frozen evidence cannot establish its stated reproduction claim."""


def canonical(value: object) -> bytes:
    return rfc8785.dumps(value) + b"\n"


def digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def descriptor(raw: bytes) -> dict:
    return {"rawSha256": digest(raw), "sizeBytes": len(raw)}


def seal(resource: dict) -> dict:
    result = copy.deepcopy(resource)
    result.pop("digest", None)
    result["digest"] = digest(rfc8785.dumps(result))
    return result


def input_descriptor(inputs: Mapping[str, dict]) -> dict:
    return {key: {**descriptor(canonical(inputs[key])), "digest": inputs[key]["digest"]}
            for key in RESOURCES}


def _schema_at(schema: dict, root: dict) -> dict:
    """Resolve only local direct references; union branches stay conservative."""
    seen = set()
    while "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise ReproductionFailure("unsupported or cyclic schema reference")
        seen.add(ref)
        schema = root
        for token in ref[2:].split("/"):
            schema = schema[token.replace("~1", "/").replace("~0", "~")]
    return schema


def _pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def operations(inputs: Mapping[str, dict], schemas: Mapping[str, dict]) -> list[dict]:
    """Preorder fields by Unicode key, array indices descending, resource order fixed."""
    result = []

    def visit(value: object, schema: dict, root: dict, resource: str,
              parts: tuple[str, ...]) -> None:
        schema = _schema_at(schema, root)
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            for key in sorted(value):
                child = (*parts, key)
                if key in properties and key not in required and child != ("digest",):
                    result.append({"resource": resource, "pointer": _pointer(child),
                                   "kind": "optional-field",
                                   "deletedValueDigest": digest(canonical(value[key]))})
                visit(value[key], properties.get(key, {}), root, resource, child)
        elif isinstance(value, list):
            item_schema = schema.get("items", {})
            if not isinstance(item_schema, dict):
                item_schema = {}
            for index in reversed(range(len(value))):
                child = (*parts, str(index))
                result.append({"resource": resource, "pointer": _pointer(child),
                               "kind": "array-item",
                               "deletedValueDigest": digest(canonical(value[index]))})
                visit(value[index], item_schema, root, resource, child)

    for resource in RESOURCES:
        visit(inputs[resource], schemas[resource], schemas[resource], resource, ())
    return result


def delete(inputs: Mapping[str, dict], operation: Mapping) -> dict[str, dict] | None:
    """Delete one matching value, reseal raw maps, preserve deliberately wrong root refs."""
    candidate = copy.deepcopy(dict(inputs))
    resource = operation["resource"]
    parts = [part.replace("~1", "/").replace("~0", "~")
             for part in operation["pointer"][1:].split("/")]
    parent = candidate[resource]
    try:
        for part in parts[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        if digest(canonical(parent[key])) != operation["deletedValueDigest"]:
            return None
        del parent[key]
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    candidate[resource] = seal(candidate[resource])
    if resource in ("snapshot", "change"):
        reference = candidate["plan"]["spec"][resource + "Ref"]
        if reference.get("digest") == inputs[resource]["digest"]:
            reference["digest"] = candidate[resource]["digest"]
        candidate["plan"] = seal(candidate["plan"])
    return candidate


def preserves(observations: Mapping, incident: Mapping, requirement: Mapping) -> bool:
    for side in ("python", "go"):
        observation = observations[side]
        if observation.get("sutStatus") != "COMPLETED":
            return False
        if observation["result"]["verdict"] != "REJECT":
            return False
        if not parity._score(observation, requirement)[0]:
            return False
        if observation["result"]["reasonCodes"] != incident[side + "Observation"]["result"]["reasonCodes"]:
            return False
    return True


def minimize(inputs: dict[str, dict], schemas: Mapping[str, dict], incident: Mapping,
             requirement: Mapping, observe: Callable, *, max_attempts: int = MAX_ATTEMPTS,
             max_seconds: float = MAX_SECONDS) -> tuple[dict[str, dict], dict]:
    started = time.monotonic()
    initial = observe(inputs)
    if not preserves(initial, incident, requirement):
        raise ReproductionFailure("original diagnostic disagreement did not reproduce")
    current = copy.deepcopy(inputs)
    trials = []
    passes = []
    final_observations = initial
    while True:
        accepted = 0
        indexes = []
        enumerated = operations(current, schemas)
        for operation in enumerated:
            candidate = delete(current, operation)
            if candidate is None:
                continue  # A prior accepted parent deletion made this operation stale.
            if len(trials) >= max_attempts or time.monotonic() - started > max_seconds:
                raise ReproductionFailure("minimization budget exhausted before a complete certificate")
            observation = observe(candidate)
            retained = preserves(observation, incident, requirement)
            trial = {"index": len(trials), "operation": operation,
                     "parentInputs": input_descriptor(current),
                     "candidateInputs": input_descriptor(candidate),
                     "observations": observation, "retained": retained}
            indexes.append(len(trials))
            trials.append(trial)
            if retained:
                if sum(len(canonical(v)) for v in candidate.values()) >= sum(
                        len(canonical(v)) for v in current.values()):
                    raise ReproductionFailure("accepted deletion did not strictly reduce raw byte size")
                current = candidate
                final_observations = observation
                accepted += 1
            if len(trials) % 100 == 0:
                print(f"{incident['disagreementId']}: {len(trials)} trials, "
                      f"{sum(item['retained'] for item in trials)} retained", file=sys.stderr)
        passes.append({"enumerated": len(enumerated), "trialIndexes": indexes,
                       "accepted": accepted})
        if accepted == 0:
            if len(indexes) != len(enumerated):
                raise ReproductionFailure("terminal sweep omitted a declared one-step deletion")
            break
    return current, {
        "algorithm": ALGORITHM, "originalInputs": input_descriptor(inputs),
        "originalObservations": initial, "minimizedInputs": input_descriptor(current),
        "minimizedObservations": final_observations, "passes": passes, "trials": trials,
        "certificate": {"claim": "1-minimal-under-declared-deletion-operators",
                        "globalMinimumClaimed": False,
                        "terminalTrialIndexes": passes[-1]["trialIndexes"],
                        "remainingOneStepDeletions": passes[-1]["enumerated"],
                        "preservingOneStepDeletions": 0},
    }


def _read_regular(path: Path) -> bytes:
    if any(parent.is_symlink() for parent in path.parents):
        # macOS exposes /tmp through /private/tmp; callers pass a resolved root.
        raise ReproductionFailure(f"symlink ancestor: {path}")
    descriptor_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor_fd, "rb") as stream:
        status = os.fstat(stream.fileno())
        if (not stat.S_ISREG(status.st_mode) or status.st_nlink != 1
                or status.st_size > MAX_FILE_BYTES):
            raise ReproductionFailure(f"unsafe or oversized artifact: {path}")
        raw = stream.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ReproductionFailure(f"artifact grew beyond ceiling: {path}")
    return raw


def _source_materials() -> dict[str, bytes]:
    paths = {
        "scripts/minimize_plan_verification_disagreements.py",
        "scripts/check_plan_verification_parity.py", "pyproject.toml", "uv.lock",
        "implementations/python-plan-verifier-reference/adapter.py",
        "implementations/go-plan-verifier-v01-internal/go.mod",
        "schemas/OrganizationSnapshot.schema.json", "schemas/SemanticChangeSet.schema.json",
    }
    paths.update(path.relative_to(ROOT).as_posix() for path in (ROOT / "src/oac").rglob("*.py"))
    paths.update(path.relative_to(ROOT).as_posix()
                 for path in (ROOT / "implementations/go-plan-verifier-v01-internal").glob("*.go"))
    return {path: _read_regular(ROOT / path) for path in sorted(paths)}


def _read_tree(directory: Path) -> dict[str, bytes]:
    if directory.is_symlink() or not directory.is_dir():
        raise ReproductionFailure("reproduction lineage must be a real directory")
    result = {}
    total = 0
    for path in sorted(directory.rglob("*")):
        status = path.lstat()
        if stat.S_ISDIR(status.st_mode):
            continue
        raw = _read_regular(path)
        total += len(raw)
        if total > MAX_TREE_BYTES or len(result) >= 256:
            raise ReproductionFailure("reproduction lineage exceeds its material ceiling")
        result[path.relative_to(directory).as_posix()] = raw
    return result


def verify_tree(actual: Mapping[str, bytes], expected: Mapping[str, bytes]) -> None:
    if set(actual) != set(expected):
        raise ReproductionFailure(
            "reproduction artifact inventory differs: "
            f"missing={sorted(set(expected) - set(actual))}; extra={sorted(set(actual) - set(expected))}"
        )
    for name in sorted(expected):
        if actual[name] != expected[name]:
            raise ReproductionFailure(f"reproduction bytes differ: {name}")


def _published_history(
    manifest_path: str,
    manifest_digest: str,
    raw_digest: str,
    *,
    seen: tuple[str, ...] = (),
    consumed_bytes: int = 0,
) -> tuple[dict, bytes]:
    """Verify one anchored historical tree and its recursively committed ancestors."""
    if not isinstance(manifest_path, str):
        raise ReproductionFailure("predecessor manifest path must be a string")
    relative = PurePosixPath(manifest_path)
    if (
        relative.as_posix() != manifest_path
        or len(relative.parts) != 4
        or relative.parts[:2] != ("experiments", "plan-verification-portability")
        or relative.name != "manifest.json"
        or any(part in (".", "..") for part in relative.parts)
        or "\\" in manifest_path
        or "\x00" in manifest_path
    ):
        raise ReproductionFailure("predecessor manifest path escapes the publication namespace")
    if manifest_path in seen or len(seen) >= MAX_HISTORY_DEPTH:
        raise ReproductionFailure("predecessor history is cyclic or exceeds its depth ceiling")
    directory = ROOT
    for part in relative.parts[:-1]:
        directory /= part
        if directory.is_symlink():
            raise ReproductionFailure("predecessor directory must not be a symlink")
    files = _read_tree(directory)
    consumed_bytes += sum(len(value) for value in files.values())
    if consumed_bytes > MAX_TREE_BYTES:
        raise ReproductionFailure("predecessor history exceeds its aggregate material ceiling")
    raw = files.get("manifest.json", b"")
    if digest(raw) != raw_digest:
        raise ReproductionFailure("predecessor manifest raw digest differs")
    manifest = parity._loads(raw)
    parity._verify_detached(manifest, "manifestDigest", "predecessor reproduction")
    if manifest["manifestDigest"] != manifest_digest:
        raise ReproductionFailure("predecessor manifest digest differs")
    if (
        manifest.get("kind") != "FixedPlanDisagreementReproductions"
        or manifest.get("coordinate") != relative.parent.name
    ):
        raise ReproductionFailure("predecessor coordinate or kind differs")
    expected = manifest.get("artifacts")
    if not isinstance(expected, dict):
        raise ReproductionFailure("predecessor artifact inventory must be an object")
    if set(files) != {*expected, "manifest.json"}:
        raise ReproductionFailure("predecessor artifact inventory differs")
    for name, item in expected.items():
        if descriptor(files[name]) != item:
            raise ReproductionFailure(f"predecessor artifact bytes differ: {name}")
    record = {
        "coordinate": manifest["coordinate"],
        "manifestPath": manifest_path,
        "manifestDigest": manifest_digest,
        "manifest": descriptor(raw),
        "verifiedArtifacts": len(expected),
        "preservation": "original-manifest-and-complete-artifact-tree-byte-verified",
    }
    if "predecessor" in manifest:
        previous = manifest["predecessor"]
        if not isinstance(previous, dict) or not isinstance(previous.get("manifest"), dict):
            raise ReproductionFailure("predecessor ancestor commitment is invalid")
        ancestor, ancestor_raw = _published_history(
            previous.get("manifestPath"),
            previous.get("manifestDigest"),
            previous["manifest"].get("rawSha256"),
            seen=(*seen, manifest_path),
            consumed_bytes=consumed_bytes,
        )
        if previous != ancestor or files.get("predecessor-manifest.json") != ancestor_raw:
            raise ReproductionFailure("predecessor ancestor record or captured manifest differs")
        record["ancestors"] = [ancestor]
    return record, raw


def _predecessor() -> tuple[dict, bytes]:
    """Anchor the latest published tree; older generations are verified by the same rule."""
    return _published_history(
        PREDECESSOR + "/manifest.json", PREDECESSOR_DIGEST, PREDECESSOR_RAW_DIGEST
    )


def _build_go() -> tuple[tempfile.TemporaryDirectory, list[str]]:
    directory = tempfile.TemporaryDirectory(prefix="oac-plan-repro-build-")
    binary = Path(directory.name) / "oac-go-plan-verifier"
    environment = parity._allowed_environment()
    environment["GOCACHE"] = str(Path(directory.name) / "go-build-cache")
    try:
        completed = subprocess.run(
            ["go", "build", "-trimpath", "-buildvcs=false", "-o", str(binary), "."],
            cwd=ROOT / "implementations/go-plan-verifier-v01-internal",
            capture_output=True, text=True, timeout=60, check=False, env=environment,
        )
        if completed.returncode:
            raise ReproductionFailure(f"Go build failed: {completed.stderr}")
    except BaseException:
        directory.cleanup()
        raise
    return directory, [str(binary)]


def generate() -> dict[str, bytes]:
    predecessor, predecessor_raw = _predecessor()
    capsule, _, requirements, materials = parity._load_capsule(verify_source_drift=False)
    result_validator = parity._schema_validator(capsule, materials)
    reason_codes = parity._registered_reason_codes(capsule, materials)
    source_materials = _source_materials()
    schemas = {
        "snapshot": parity._loads(source_materials["schemas/OrganizationSnapshot.schema.json"]),
        "change": parity._loads(source_materials["schemas/SemanticChangeSet.schema.json"]),
        "plan": parity._loads(next(materials[f"contract:{index}"]
                                  for index, value in enumerate(capsule["contracts"])
                                  if value["sourcePath"] == "schemas/OrganizationPlan.schema.json")),
    }
    artifacts = {"materials/" + key: raw for key, raw in source_materials.items()}
    artifacts["predecessor-manifest.json"] = predecessor_raw
    for index, contract in enumerate(capsule["contracts"]):
        artifacts["original-contracts/" + contract["sourcePath"]] = materials[f"contract:{index}"]
    artifacts["original-capsule.json"] = parity.CAPSULE_PATH.read_bytes()
    build_directory, go_command = _build_go()
    try:
        commands = {
            "python": [sys.executable, str(ROOT / "implementations/python-plan-verifier-reference/adapter.py")],
            "go": go_command,
        }
        build = {
            "sourceMaterials": {key: descriptor(raw) for key, raw in source_materials.items()},
            "python": {"version": platform.python_version(),
                       "executable": descriptor(Path(sys.executable).resolve().read_bytes()),
                       "dependencies": {name: importlib.metadata.version(name)
                                        for name in ("pydantic", "pydantic-core", "rfc8785", "jsonschema")}},
            "go": {"version": subprocess.check_output(["go", "version"], text=True).strip(),
                   "binary": descriptor(Path(go_command[0]).read_bytes()),
                   "buildRecipe": "go build -trimpath -buildvcs=false -o <temporary-binary> .; fresh GOCACHE"},
            "capabilities": {side: parity._check_capability(command)
                             for side, command in commands.items()},
            "implementationRelationship": "same-repository-internal; not independent organizations",
        }
        artifacts["build.json"] = canonical(build)
        summaries = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for incident_id, incident_digest in INCIDENTS.items():
                incident_raw = _read_regular(parity.CAPSULE_PATH.parent / "disagreements"
                                             / f"{incident_id}.incident.json")
                incident = parity._loads(incident_raw)
                parity._verify_detached(incident, "incidentDigest", "original incident")
                if incident["incidentDigest"] != incident_digest:
                    raise ReproductionFailure("original incident differs from its trust anchor")
                case = next(item for item in capsule["cases"] if item["caseId"] == incident["caseId"])
                requirement = next(item for item in requirements["cases"] if item["caseId"] == case["caseId"])
                raw_inputs = {"snapshot": materials[case["rootSet"] + ":snapshot"],
                              "change": materials[case["rootSet"] + ":change"],
                              "plan": materials["plan:" + case["caseId"]]}
                inputs = {key: parity._loads(raw) for key, raw in raw_inputs.items()}
                if any(canonical(inputs[key]) != raw for key, raw in raw_inputs.items()):
                    raise ReproductionFailure("original resources are not exact JCS plus LF")
                forbidden = (case["caseId"], case["mutationClass"], case["plan"]["transform"],
                             case["plan"]["sourcePath"])

                def observe(values: Mapping[str, dict], forbidden_tokens: tuple = forbidden) -> dict:
                    raw = [canonical(values[key]) for key in RESOURCES]
                    request, request_digest = parity._request(*raw, forbidden_tokens=forbidden_tokens)
                    input_digests = {key + "Digest": values[key]["digest"] for key in RESOURCES}
                    futures = {side: pool.submit(parity._invoke, command, request)
                               for side, command in commands.items()}
                    admitted = {side: parity._admit_response(future.result(), request["requestId"],
                                                            input_digests, result_validator, reason_codes)
                                for side, future in futures.items()}
                    return {"requestDigest": request_digest, **admitted}

                minimized, trace = minimize(inputs, schemas, incident, requirement, observe)
                artifacts[f"{incident_id}/original-incident.json"] = incident_raw
                artifacts[f"{incident_id}/requirement.json"] = canonical(requirement)
                for key in RESOURCES:
                    artifacts[f"{incident_id}/original/{key}.json"] = raw_inputs[key]
                    artifacts[f"{incident_id}/minimized/{key}.json"] = canonical(minimized[key])
                artifacts[f"{incident_id}/trace.json"] = canonical(trace)
                summary = {"disagreementId": incident_id, "caseId": case["caseId"],
                           "trials": len(trace["trials"]),
                           "acceptedDeletions": sum(item["retained"] for item in trace["trials"]),
                           "originalBytes": sum(len(value) for value in raw_inputs.values()),
                           "minimizedBytes": sum(len(canonical(value)) for value in minimized.values()),
                           "certificate": trace["certificate"]}
                summaries.append(summary)
                print(json.dumps(summary, ensure_ascii=False), file=sys.stderr)
        if source_materials != _source_materials():
            raise ReproductionFailure("build source materials changed during minimization")
        if _predecessor() != (predecessor, predecessor_raw):
            raise ReproductionFailure("predecessor changed during minimization")
        artifacts["summary.json"] = canonical({"algorithm": ALGORITHM, "cases": summaries})
        artifacts["README.md"] = README.encode()
        manifest = {
            "apiVersion": "oac.plan-verification.reproduction/v0alpha2",
            "kind": "FixedPlanDisagreementReproductions", "coordinate": COORDINATE,
            "predecessor": predecessor,
            "algorithm": ALGORITHM, "originalCapsuleDigest": parity.CAPSULE_DIGEST,
            "originalIncidentDigests": INCIDENTS,
            "limits": {"attemptsPerIncident": MAX_ATTEMPTS, "secondsPerIncident": MAX_SECONDS},
            "artifacts": {key: descriptor(raw) for key, raw in sorted(artifacts.items())},
        }
        manifest["manifestDigest"] = digest(rfc8785.dumps(manifest))
        artifacts["manifest.json"] = canonical(manifest)
        return artifacts
    finally:
        build_directory.cleanup()


README = """# Fixed-Plan diagnostic disagreement reproductions v0.1-repro-5

This successor binds the current implementation. The latest published predecessor
manifest is externally anchored by both raw and detached digests. One recursive
check verifies its full artifact tree and every committed ancestor before and
after generation. All historical directories remain byte-for-byte unchanged.
predecessor-manifest.json retains the exact immediate predecessor manifest; the
new manifest records its coordinate, material count and verified ancestor chain.
Historical evidence is never regenerated with current code and relabeled as old.

These three controlled-local reproductions preserve the original required/forbidden
policy and each implementation's exact reason-code set. Both real process adapters
must complete with REJECT. Parse failures, a different rejection, timeout, or protocol
failure cannot establish a retained reduction. The original incidents remain resolved
as PERMITTED_DIAGNOSTIC_VARIANCE; this lineage does not claim an implementation fix.
The Python reference and Go verifier are maintained in this repository. They are not
evidence of organizational independence, production adoption, or enterprise readiness.

## Deletion operators and claim

The algorithm visits snapshot, change, then plan; object keys use Unicode order and
array indexes descend. Every array item is deletable, including arrays in union or
untyped schema positions. A present object field is deletable only when direct local
schema-reference resolution lists it in properties and omits it from required. It does
not infer optional fields through anyOf/oneOf/allOf. Resource-root digest is excluded
because resealing adds it back. Field/array deletion is preorder; accepted parent
deletions make now-missing or changed-value descendant operations stale. A full sweep
without an accepted deletion is the terminal certificate. No global minimum is claimed.

Each candidate's resource digest is recomputed as SHA-256(JCS(raw-map minus digest)).
After deleting from a root resource, an originally matching Plan root digest follows
the new root; an already incorrect digest stays incorrect. The Plan is then resealed.
No semantic references, IDs, topology, or compiler output are regenerated or repaired.
Original resources remain byte-for-byte frozen under original/. Minimized resources
are separately sealed raw maps under minimized/. All resource bytes use JCS plus LF.

trace.json records every executed candidate, its deletion pointer and deleted-value
digest, parent/candidate raw and resource digests, both admitted observations, and the
retention decision. Candidate bytes reconstruct deterministically from the original
inputs and preceding retained deletions. Terminal trial indexes enumerate every
remaining declared one-step deletion. Observations are normalized by the existing
parity reader: COMPLETED results are kept intact, error detail is stripped to its
stable code, and requestDigest commits the real opaque stdin request. No case labels
or expected outcomes are disclosed to either adapter.

## Build and verification

The original capsule is checked against its existing external digest anchor. Its
historical source-drift check is deliberately disabled: this run binds current adapter
source/build material separately in build.json and materials/. Frozen contracts and
incidents remain copied verbatim. The existing parity runner is reused, not forked.
The Go adapter is rebuilt with -buildvcs=false and a fresh cache, so repository
VCS presence does not alter the executable. Executable digests, runtime versions,
dependency versions, capabilities, and source bytes are recorded without temporary
absolute paths. Changes in these materials require a new reproduction lineage.

Run from the repository root with the same Python environment and Go toolchain:

    .venv/bin/python scripts/minimize_plan_verification_disagreements.py --check

Check first compares the complete frozen source-material paths and bytes to the
current tree. Known source drift is rejected before building or invoking either
adapter. This preflight is not a successful replay and does not replace any
subsequent check. Check then reads the complete existing artifact inventory, independently repeats every
candidate with both real adapters, rebuilds all certificates/materials in memory,
and compares every byte. A recomputed manifest alone cannot launder a modified trace,
input, observation, or certificate. It does not rewrite this directory or either
historical seed. Temporary process/build directories are cleaned on exit. Generation
without --check refuses an existing output directory. Budget exhaustion fails without
publishing partial material. This is an exact-build local reproducibility check;
it is not a promise that another runtime version will produce byte-identical evidence.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    directory = args.output.absolute()
    try:
        if directory.is_symlink():
            raise ReproductionFailure("output must not be a symlink")
        directory = directory.resolve()
        if not args.check and directory.exists():
            raise ReproductionFailure("refusing to overwrite an existing reproduction lineage")
        actual = _read_tree(directory) if args.check else None
        if actual is not None:
            frozen_sources = {name: raw for name, raw in actual.items() if name.startswith("materials/")}
            current_sources = {"materials/" + name: raw for name, raw in _source_materials().items()}
            try:
                verify_tree(frozen_sources, current_sources)
            except ReproductionFailure as exc:
                raise ReproductionFailure(f"source preflight failed before replay: {exc}") from exc
        expected = generate()
        if actual is not None:
            verify_tree(actual, expected)
            print("PASS: all three exact-build reproductions and deletion certificates replayed")
        else:
            directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".plan-repro-", dir=directory.parent) as temp:
                staged = Path(temp) / directory.name
                staged.mkdir()
                for name, raw in expected.items():
                    path = staged / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw)
                if directory.exists():
                    raise ReproductionFailure("output appeared during generation; refusing overwrite")
                staged.rename(directory)
            print(f"PASS: frozen three deletion-minimal reproductions at {directory}")
    except (ReproductionFailure, parity.HarnessFailure, OSError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
