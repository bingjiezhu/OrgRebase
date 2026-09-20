from __future__ import annotations

import orgrebase.workspace.profile as profile_facade
import orgrebase.workspace.profile_admission as profile_admission
import orgrebase.workspace.profile_contracts as profile_contracts
import orgrebase.workspace.reference_profiles as reference_profiles
from orgrebase.workspace.source_admission import (
    admit_enterprise_seed_sources,
    verify_reference_runtime_projections,
)

CONTRACT_EXPORTS = {
    "PROFILE_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "REFERENCE_HANDLER_PROFILE",
    "AdmissionDimension",
    "AdmissionDimensionStatus",
    "AdmittedClaimCeiling",
    "AdmittedGapAction",
    "ChangeFamilyBinding",
    "DefaultTaskBinding",
    "EffectCeiling",
    "EnterpriseSeedAdmissionError",
    "EnterpriseSeedAdmissionReceipt",
    "EnterpriseSeedGap",
    "EnterpriseSeedProfile",
    "FrozenModel",
    "GovernanceBinding",
    "GrowthLevel",
    "ProfileLimitationCode",
    "ReadinessGate",
    "RuntimeCompatibilityDeclaration",
    "RuntimeCompatibilityMode",
    "SeedCompleteness",
    "SeedComponent",
    "SeedComponentKind",
    "SeedDataClass",
    "SeedGapKind",
    "SeedInputValue",
    "SeedSourceRoot",
    "minimum_gap_gates",
}
REFERENCE_EXPORTS = {
    "northstar_acme_quote_profile",
    "supplier_shadow_intake_profile",
}
ADMISSION_EXPORTS = {
    "admit_enterprise_seed_profile",
    "load_enterprise_seed_profile",
    "parse_enterprise_seed_admission_receipt",
    "parse_enterprise_seed_profile",
    "profile_summary",
    "require_reference_runtime_compatible",
}


EXPECTED_DIGEST_LOCKS = {
    "northstar": {
        "profile": "sha256:c3c81dbc91485c7d4e940999af30547c140873c0cb6c2defdc845ef5689da37f",
        "declared_roots": (
            "sha256:48f36b7d1c315b7012e0319dedc72ad045fd78c0610b496ae3d38ed9dd141339",
            "sha256:7a8981032e0a6564b40f92247b0855703f2e3416f7a1c4a54da687673802edff",
            "sha256:fd03a8fbb8b72242c1cca767e7daa3917f93283c41d2dd2c0f5c41b705b9ce2d",
            "sha256:ffe9233079bd1a6d06cb6fa035218803a7ad3ed49503229ac592f2439056d2f8",
            "sha256:8277aab48f2a025d1b4b2be9edf177d73ac955cfe98a90277a6cced7997ec16d",
        ),
        "declared_components": (
            "sha256:2832cbba137cfb90fc7886ad853663dab8d40c11bf9b1b2486e1ab194220262f",
            "sha256:d4fccb465bdedccd94d114e3aeb93d8519cc6ea75dafc162513a49a3e06deb7b",
            "sha256:d282cda751edb338549c8585abaf3f666d15e60eb157657bf750f0be4f459e94",
            "sha256:1806ee840bbe567aec6c003b3f0e2211dbaf23b0c5ab0e0c8b37f5dd982d5e4e",
            "sha256:205135ff759d216d230d1403e8072f5356dd9b11cd71661d7330094688220c36",
        ),
        "gaps": (),
        "source_receipt": "sha256:7bde5725ce38e7e40acc7994d889524880a0925424f661544ea69373de382ebd",
        "source_observations": (
            "sha256:e041a8f4ea70941594d5f4cf009f8fb85b3aab10794793064331a9e2ae30fc3e",
            "sha256:cc2700b1d5891193be06d383b75016bb6edc2d1cd8e6a69c0a8dc4bad7bf0258",
            "sha256:269b1cf936db7182827a9352d62fbbd7e3a11f64a97e99c08044fc7a86492352",
            "sha256:1052d1f5768ccb25dc469063890cd8411c7ef093fe0ae03b780c6562bec3df7a",
            "sha256:6ab72c0ee0914202994be7cd96b564dae4841c92fcc3ca200a80e5553336b3ef",
        ),
        "component_admissions": (
            "sha256:c36e3ee5485dbcad629f87225bf63879f6f65fda3858b06c3d9a8b049f5304ab",
            "sha256:603a95d36f886cfdfce2731e35c895853dfc6b895750a040248d839bd0e37a2b",
            "sha256:5b141b4aa690865ddca1fbb08d58c4db86b5c1ea930b75c6bf1f0d04d050b9f4",
            "sha256:d3f0f1389679723e2e60db8a25dd104a02b4b7ed84fd6faa66a586ad08703a65",
            "sha256:c9fc142820097bca933352878d3cd9d44106d63e9784b0b5f0d425541faf8661",
        ),
        "projection_bindings": (
            "sha256:9925a5c99eee5dd073d151c7cd49bf3db7cc875b7e30500e7209f8359e60b1db",
            "sha256:fddd77ac5bfe6618619f21246a59b360d55b8ec0877144442b95adb8e03e1b90",
            "sha256:5ba9346ebda921925410b89c961112a0bcd56e026685f02f3f9135c12f6c4d5c",
            "sha256:1ee40521e7f9a110b91b47a9af86be44b57306421e7686864f3a1bc24730a5e2",
            "sha256:64263c2a24c1c40af6cf921d114dbcb11965515c95c71fc9eae26b0b6f342519",
        ),
        "source_projection": "sha256:6fdaaa288acd201812ca64c429f78ac316a2b0aa35426ba77a7a52277761ee08",
        "component_projection": "sha256:0243de52088fbb79e3f46c15168dcda413c0d6972b69008e4fb1600b56cddd37",
        "profile_projection": "sha256:f226ea1943d8d86e72f4b6d2efc7d65786ce3c5e4df11189bcca007745610954",
        "admission_receipt": "sha256:88594f8ef78fedbf16068cd2bb7b0a36bce6eb594d01412d31675925a909375a",
        "runtime_receipt": "sha256:00e0fadede15434fc9a1d82556574c3e22f61d48df853b6478e26f285b4237f0",
        "runtime_observations": (
            "sha256:4e067fc9ec2ef037579dff0c87f14563fe0fafb21d986bf37e93a608b70e1238",
            "sha256:4c694544d520895fe579d33984ea2089c9f2a2f439dbb6989a9ec46c6e35e8fe",
            "sha256:c005d31795a1d50511b7c29c4127c471e54c76e9b31bab00f886876615b53cef",
            "sha256:93b0564935a7f266b5fd65a9f876d2334f985931d1f0378d837d2d5985ce4e37",
            "sha256:4a992f9bd6ad845c84178f275866ff2adaff515c56c5908d48b09ce10a529449",
        ),
        "runtime_projection": "sha256:d6a44e125f94a1f03e49d189e8cbed624d646f6a3c125d397aedfae4f2cb2567",
    },
    "veracier": {
        "profile": "sha256:4f60cf1b06451778cfec0e624a57fc5f9d133307a12e4af22dec9d85b94020a6",
        "declared_roots": (
            "sha256:6f6ff65d5fb1ff5ede680374edbe708bb82957f8c17f91084ae6b49a17fcfc91",
            "sha256:3e9eefa9049f91d396436a69979234bad3dcdc4d3bc765e10364980fca0d69bb",
            "sha256:8197f00b5a5b7a9f78c9fe00f727a1cfa69ef0a92e25793af176ea3e971cae26",
            "sha256:0437e4bd129096ae706f1085eabe8d1fc4583bac663db532316dcf51f132440b",
            "sha256:08af649d4861b7ee5ac42bb8e8d2738d2e221101e35a23fc4385c32cad7240bd",
        ),
        "declared_components": (
            "sha256:cbe6a039245d10f09d849cb8194f2a572ce6749f910bf0d97b116794b6348ef8",
            "sha256:0e93023bc4e113768824b624b65902011a7a5d89ae06dd291cee3a1a8b29f0a8",
            "sha256:4aca877b526cf00d578bfcfca927a54231e53e05bfb83264e5d1aca3ab852d3b",
            "sha256:74aaa847fcb086b49d37637cc150e2f6fbe808725b4abc57c296553eb86a782e",
            "sha256:ca9f70c63638694ce6c0569b56a832b376319c9ac8df2011e2f3f2945f043860",
        ),
        "gaps": ("sha256:1061cb28b680e925ca60d3e49e14e5f5dab3c8be6dec04cb641fb073b4620684",),
        "source_receipt": "sha256:3c297f285c6ac41378c5af28cadb41ab9205fa6a290e1a464d6cf5986d4fbd89",
        "source_observations": (
            "sha256:f088d597be94e132d85da3237704a8d2e409dce3f48c08c0a741bf513e860f02",
            "sha256:50848e0164bff52e15885b2c7a3819b4f6ca62295c0ae08767910b72f8856faf",
            "sha256:abee3fb8a34ae0040c682eb9e3009b4ea3473a9bc9c1c51c597393a4409042ef",
            "sha256:a0603b0adafba9ee83a7f9b4f26dc88577dd2154c2b02f721799591fd20332a2",
            "sha256:4e9c548ed4f420d4e30f94d90fd2bb0b1ece91cb1944fa08d03ecffcb14947fc",
        ),
        "component_admissions": (
            "sha256:5f5add34d4b6bf88192611637b502372dc7ffdea1f314f72248a614ac2b514b9",
            "sha256:42a244b7583ce32a3ce5888bd681ac96418ca15c30a43b638f23ec44e30a091e",
            "sha256:8f4e216f9781afcc58d886924c6885b7d811b7339d99bfdebdb3e33d399fd7b4",
            "sha256:5b5920e6578ad1252a49725c761e43b13d0652657f546cf0b04da1d773ad27da",
            "sha256:e5c050bba5a48815ed59cccb19e5d07f95bdff8a02ddf92930769734d120d7c9",
        ),
        "projection_bindings": (
            "sha256:c8000a75f27a1256bcf30f0a33ba7aa202bafb07b1cb02820e10f80eb99d5b36",
            "sha256:d5a222cc787ac21dc4abfd63c97068ef1e2d2b310c9b9519c685b8990e0e8663",
            "sha256:15880cd74eba4db1c6e8557ee3cf89000463ae9cfe626916bddee20807c68ad4",
            "sha256:66580e4289e3032013fbae82eb2ebf131a1363cd4d8ea4f2b7786de95bfad9eb",
            "sha256:34cfe13c77f659637c2bc456ceb1ca6acc736a773b48029e21b3c4cbfea8c329",
        ),
        "source_projection": "sha256:0b5de1d297543946eccb98a08ec1c6036ea3d75e627228d6fd3619e8c97dee10",
        "component_projection": "sha256:c47dc1d42ba0412696b8eafd01b8db54c8af5ccc7720e82fa0a090e892299f9e",
        "profile_projection": "sha256:b92debd9b27385216b95b8ad095e34fb86006239c2e657cdcd8b7e0085d02ff4",
        "admission_receipt": "sha256:e38eca332b8dde69c4895003f3094006e0283395969ab5db319fc3a1cab37ebd",
    },
}


def test_profile_facade_explicitly_preserves_every_public_import() -> None:
    expected = CONTRACT_EXPORTS | REFERENCE_EXPORTS | ADMISSION_EXPORTS
    assert set(profile_facade.__all__) == expected
    assert len(profile_facade.__all__) == len(expected)
    for name in CONTRACT_EXPORTS:
        assert getattr(profile_facade, name) is getattr(profile_contracts, name)
    for name in REFERENCE_EXPORTS:
        assert getattr(profile_facade, name) is getattr(reference_profiles, name)
    for name in ADMISSION_EXPORTS:
        assert getattr(profile_facade, name) is getattr(profile_admission, name)


def _actual_digest_lock(profile, *, include_runtime: bool) -> dict[str, object]:
    source = admit_enterprise_seed_sources(profile)
    admission = profile_facade.admit_enterprise_seed_profile(
        profile,
        source_admission=source,
    )
    actual: dict[str, object] = {
        "profile": profile.digest,
        "declared_roots": tuple(item.declared_digest for item in profile.source_roots),
        "declared_components": tuple(item.declared_digest for item in profile.components),
        "gaps": tuple(item.digest for item in profile.gaps),
        "source_receipt": source.digest,
        "source_observations": tuple(item.digest for item in source.root_observations),
        "component_admissions": tuple(item.digest for item in source.component_admissions),
        "projection_bindings": tuple(item.digest for item in source.admitted_projection_bindings),
        "source_projection": source.source_projection_digest,
        "component_projection": source.component_projection_digest,
        "profile_projection": source.profile_projection_digest,
        "admission_receipt": admission.digest,
    }
    if include_runtime:
        runtime = verify_reference_runtime_projections(profile, source)
        actual.update(
            runtime_receipt=runtime.digest,
            runtime_observations=tuple(item.digest for item in runtime.observations),
            runtime_projection=runtime.runtime_projection_digest,
        )
    return actual


def test_split_preserves_frozen_profile_source_admission_and_runtime_digests() -> None:
    assert (
        _actual_digest_lock(profile_facade.northstar_acme_quote_profile(), include_runtime=True)
        == EXPECTED_DIGEST_LOCKS["northstar"]
    )
    assert (
        _actual_digest_lock(profile_facade.supplier_shadow_intake_profile(), include_runtime=False)
        == EXPECTED_DIGEST_LOCKS["veracier"]
    )
