"""Build or check the new foundation-boundaries coordinate without rewriting old corpora."""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import tempfile
from pathlib import Path

from build_bundle import CONTRACT_SOURCES, expected_manifest
from successor_vectors import successor_cases

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "runner/src/oac_ctk_runner"
CONTRACT = runpy.run_path(str(PACKAGE / "contracts.py"))
PUBLISHED = runpy.run_path(str(PACKAGE / "published.py"))
DESTINATION = ROOT / "bundles/foundation-boundaries-v0.2"


def render(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def files() -> dict[str, bytes]:
    result = {path: source.read_bytes() for path, source in CONTRACT_SOURCES.items()}
    for path in (ROOT / "schemas/v0alpha2").glob("*.schema.json"):
        result[f"contracts/schemas/{path.name}"] = path.read_bytes()
    result["contracts/protocol/successor-boundaries-v1.md"] = (ROOT / "protocol/successor-boundaries-v1.md").read_bytes()
    result["resource-profile.json"] = (ROOT / "bundles/phase-a-v0.1/resource-profile.json").read_bytes()
    cases = [json.loads(path.read_bytes()) for path in sorted((ROOT / "bundles/phase-a-v0.1/cases").glob("*.json"))]
    cases += successor_cases()
    cases = [{**case, "testTarget": "SUT"} for case in cases]
    cases += [{"caseId": "H-" + probe.upper(), "testTarget": "HARNESS", "operation": "harnessProbe",
               "input": {"probe": probe}, "expect": {"harnessStatus": "BOUNDARY_ENFORCED"},
               "requirementRefs": ["REQ-HARNESS-" + ("BUNDLE" if probe.startswith("bundle-") else "ADAPTER")]}
              for probe in CONTRACT["HARNESS_PROBES"]]
    ids = [case["caseId"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("SUCCESSOR_CASE_ID_DUPLICATE")
    for case in cases:
        result[f"cases/{case['caseId']}.json"] = render(case)
    result["requirements.json"] = render({"requirementSetId": "oac-foundation-boundaries-v0.2-required",
        "standardVersion": PUBLISHED["SUCCESSOR_COORDINATE"][3], "profileVersion": "v0.1",
        "required": sorted(ids), "notScored": []})
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Freeze this new, unpublished coordinate")
    args = parser.parse_args()
    if args.publish and PUBLISHED["SUCCESSOR_BUNDLE_DIGEST"] != "UNPUBLISHED":
        parser.error("published coordinates are immutable")
    expected = files()
    with tempfile.TemporaryDirectory(prefix="ctk-successor-build-") as directory:
        root = Path(directory)
        for relative, payload in expected.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        expected["bundle.json"] = expected_manifest(root, coordinate=PUBLISHED["SUCCESSOR_COORDINATE"])
    bundle_digest = json.loads(expected["bundle.json"])["bundleDigest"]
    if args.check:
        actual = {path.relative_to(DESTINATION).as_posix(): path.read_bytes() for path in DESTINATION.rglob("*") if path.is_file()}
        if actual != expected or bundle_digest != PUBLISHED["SUCCESSOR_BUNDLE_DIGEST"]:
            parser.error("successor frozen material or trust anchor drift")
    else:
        if DESTINATION.exists():
            parser.error("never overwrite an existing coordinate; verify with --check")
        for relative, payload in expected.items():
            path = DESTINATION / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        if args.publish:
            if PUBLISHED["SUCCESSOR_BUNDLE_DIGEST"] != "UNPUBLISHED":
                parser.error("published coordinates are immutable")
            path = PACKAGE / "published.py"
            path.write_text(path.read_text().replace('"UNPUBLISHED"', repr(bundle_digest)))
    print(json.dumps({"bundleDigest": bundle_digest, "files": len(expected),
                      "manifestBytesDigest": "sha256:" + hashlib.sha256(expected["bundle.json"]).hexdigest()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
