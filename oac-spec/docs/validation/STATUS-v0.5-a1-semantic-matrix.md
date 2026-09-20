# OAC A1 Supplier v0.2 Seed-2 Semantic Portability Report

**Validation date**: 2026-08-24  
**Evidence status**: current bounded working-tree validation evidence  
**Source state**: uncommitted working tree on `main`, based on
`e301952ae08986e6398a636f04f883036bc88c8d`  
**Reference package**: `oac-contract 0.3.0a0`  
**Claim ceiling**: two same-repository implementations agree on the exact public seed-2 capability set;
this is not clean-room or organizational independence, complete Supplier/OAC conformance, enterprise
correctness, standard consensus, certification, security evidence, supply-chain attestation, or
production authorization. The installed replay is self-attested and excludes cryptographic
execution provenance plus Python standard-library and operating-system bytes

## Result

ADR 0006 asked whether adjacent semantic disagreements could be resolved through public contract
artifacts instead of treating Python output or majority vote as truth. For the bounded successor set,
the answer is yes:

| Surface | Source-tree result | Isolated-wheel result |
|---|---:|---:|
| sealed-resource admission | 25/25 | 25/25 |
| frozen derivations | 3/3 | 3/3 |
| public generated branch cases | 24/24 | 24/24 |
| total observations | 52/52 | 52/52 |
| within-set disagreements | 0 | 0 |
| canned-three mutant | rejected by `generated:scope-order` | rejected |
| candidate-root forgery mutant | rejected by `generated:candidate-root` | rejected |

This is stronger than seed-1's historical 34 selected observations because the successor binds a
reviewed semantic-rule matrix, a versioned mutation recipe, exact generator source, a bounded
capability set, implementation production-source closures, complete observations, and two concrete
negative controls. A closed v0alpha2 evidence manifest now makes that coordinate machine-checkable and
binds its installed-material ledger. It remains a public representative matrix, not a hidden or
exhaustive suite.

## Exact evidence coordinate

| Material | Identity |
|---|---|
| seed-2 evidence manifest raw bytes | `sha256:83208e728e9203dab52394932f980844421d16995ef07f6a326533090d991324` |
| seed-2 evidence manifest detached digest | `sha256:4fd16e2dfe8eb0306dc67602aeba67a9c86dda1033984c1b339f74fa22552379` |
| evidence manifest schema raw bytes | `sha256:a862f21bd2df0d920f01e7c9b766532a1b3a416253f85a39381472be4fdd515a` |
| replay self-attestation record raw bytes | `sha256:3a2c1d1270c381f52b3aaf0963dd0aa5a0b9d92cc7ca17eac2f15ea8954d51b2` |
| Phase-A bundle | `sha256:5a8f498a1b1be52a1c61eaf5f1f2f073970b7a20f89f65f6aca3158bace3f526` |
| Phase-A RequirementSet | `sha256:c012a80e3528e1ac9fe8cfb2810f0753c406c15825ea1307e7e2f50f2680d17f` |
| Phase-A ResourceProfile raw bytes | `sha256:828c0e3753880d17d6847d613d59209b51eeb4e600f3861167bfd5cfac612dda` |
| capsule | `sha256:6efc45109628314823f078256546d67aae690b1c18d97fddab38dc23be904e79` |
| capsule raw bytes | `sha256:4a5351c6681aa1d0f035c209d6741d6f62e19596adcbfb2bf7d69160e9d608fe` |
| stdio-v2 protocol raw bytes | `sha256:2a608b65059ad4b7f4d3ebf444535e4777f3fe7842e4b8a1d1af2de251a1d7ce` |
| semantic rule set | `sha256:b1f390a1c664b45c1762732aeca6fa7bb4f9238fe25f0183e774146c05ea12e8` |
| bounded capability set | `sha256:0857904b6d920e9995849b9474a5d8c3bdc483231856216e04190fac5735bd2d` |
| recipe set | `sha256:6249d9247b215c8a8fe7dbdbb307877faf5f0c03f1e2f7b09bf081cffe5fd627` |
| recipe artifact raw bytes | `sha256:ad80182d6594cae4e19eca952867eab10ef26ec035acdbf1d176bdb06a9a412e` |
| generator source raw bytes | `sha256:18e27220997399252928d25702517d7ccb78eb79e5db9e3e5104c2592f8924d3` |
| parity harness raw bytes | `sha256:b4e4347fd36d804162be6426350e071acfedba14abecd02a5e9616787d38ff46` |
| source-tree summary raw bytes | `sha256:81141e6cc8dce0e9e30c10ff513746d2f1de37d8f18980e99f89fe6494a39ca7` |
| source-tree observation manifest | `sha256:7dc779b8b469d91b9ad821a4bd0607892cb56ca593948ac108b00c4a8304ab8a` |
| installed summary raw bytes | `sha256:ea762c3c0f6883acbce3d1f311f5c5ceeccb31f87471b54260d2027b11906d8e` |
| installed observation manifest | `sha256:744a5ac7bc421da279ff00a4c08fc77ed127a3a313edf049cf199879bfb56640` |
| Python production closure | `sha256:def6d1b2adb720c7b39eca74d5b56844b2e71e2b750ef0f3f5a674c176eeb429` |
| source Python command | `sha256:d5a24a9e89261fc6de529539d1b04c6d588477a8bed9f17968efe09bdf842393` |
| installed Python command | `sha256:59a239da770cb55e93ec96c912365310bd15230ba57b3445d14bc816d7b7484b` |
| Go production closure | `sha256:db73a67c4133d39c74ce10d8fa99ceaa489487f94603d4359c2bddd8af81b364` |
| Go command | `sha256:f24de353e478899c47a241aa90919974b1e673576a1cdcb02c18500d3237c758` |
| Go executable | `sha256:cd24a7d582ea9e31af20e2b266549a0f9d92fec4a1d549d5dac75a734c5cb238` |
| Go IndependenceStatement | `sha256:5474d934fea1ab7491e9bf7157dd41fe01d5699e11a02379e727618fbf5a552b` |
| Go IndependenceStatement raw bytes | `sha256:9993a0f6af9a8ac0297c6684b70576a3bd2b1cf8a36f4f43f9c64a4a88543054` |
| Go build recipe projection | `sha256:238c5cb23995bdae603d448ec4aa607d37bdd9835324b1dcc0d5e24bc3f0b14e` |
| Go build environment projection | `sha256:06c2a7e7aa87d3885b89645fbc1a53d34332a0a6ab86ab66de64adb34a232d48` |
| Go module declaration raw bytes | `sha256:b0833d88642546c3a64ea45cf07ad53117fb21d452a2ebb3b7e633392882d38f` |
| canned-three mutant | `sha256:f73ff627bbaf075abcd9bd31f19baa531e898b28088a0778c78a7614d0d5e6c6` |
| candidate-root forgery mutant | `sha256:d4039a5e03894dc545515df140ddeacb6ffea86a491f833e81084cf1909e263d` |
| installed wheel | `sha256:a6642e8128062a9858f5227d16095402bdec74dd149c538e44d0abeb0d414298` |
| installed material ledger raw bytes | `sha256:3d142144e53fc044a98c72fa8dd2d3d5299c9ce96dd07850936d595b680de83f` |
| installed material ledger detached digest | `sha256:1fdf96074b09246f58384c4ad6fb18080bdc79e8dbad7186ee6f0fc51e438d4c` |
| installed material ledger schema raw bytes | `sha256:53e9c4c7322b3032940e69abdfed21f4629f20167079cc3ad25d7d274be37c05` |
| installed material ledger builder raw bytes | `sha256:a2d589d239037d1d7d882579fda730ba2b14338629571b78854168b68d38ed14` |
| wheel 116-entry preimage ledger | `sha256:0e0cbe64088877fcc437f9a3e4dd10c002bcc3a8f5625498815d4f7d3ddefe4d` |
| installed 115-file payload closure | `sha256:dce6e206c79ad3c5dd4f77b4583b8e00940d43df1fb052d0fcab9cb39fad162e` |
| installed 13-distribution/442-file environment closure | `sha256:6949883ceaa5444532cba0ca5314352b444ebd136989f064fce739deaec6ee0e` |
| installed distribution file ledger | `sha256:efb27de6f5ec1298fb3c8335111c9d41991b4ba50346569bf0f8f642aa5c9a5c` |
| installed repository-input closure | `sha256:72e5f706d94c64f60ce1c699978058fe8107f90d28c095378539345250a5a84d` |
| installed semantic-production closure | `sha256:b0629cd0a297cb92ccd46f25d8f1755753422cd9fd15e3d68abe602a8c3ffa71` |
| installed Python executable raw bytes | `sha256:71720f1fc66989ebd691e81c96111b47ae6ff3f1a478666084d1cacbf0fccbf2` |
| installed runtime binding | `sha256:c266044a7f0f16f5be8bdb8a323e53e149efbde42da50d988e77ddb0d2059273` |

The capsule and all seven incident/resolution pairs remain byte-for-byte frozen. The refreshed source,
wheel, installed-material, and manifest identities above record the current replay; later global
Kind/reason registry additions are outside this historical capsule and require a new coordinate if
they are ever added to Supplier conformance evidence.

The exact source-tree summary is
`experiments/supplier-v02-portability/v0.2-seed-2/parity-summary.json`. The installed observations and
their raw digests are under `experiments/supplier-v02-portability/v0.2-seed-2/replay/`. Source and
installed summary digests differ by design because command/package identity is evidence; their frozen
contract identities, production closures, semantic observations, and outcomes agree.

`make supplier-parity-exact-check` compares the live source capture byte-for-byte with the stored
exact-JCS-plus-LF summary on the captured host. The default `make check`/CI gate uses a narrowly defined
portable projection: it normalizes only declared command/runtime identities and retains source
closures, public inputs, observations, mutants, claims, capabilities, and contract materials exactly.
That projection is a cross-host regression gate, not the machine evidence identity.

## Durable disagreement lifecycle

Seven real pre-fix disagreements are preserved as immutable incidents, each with its original input,
wire responses, semantic projections, old Go executable identity, and public contract bindings. Seven
separate resolution documents reference those incident digests and bind equal fixed semantic
projections. Incidents were not rewritten after repair.

| ID | Semantic class | Incident digest | Resolution digest |
|---|---|---|---|
| DIS-002 | retracted root | `sha256:4078049e4b02ec4f68ec7e223523cb0581e496191fc919651c2c44c1fcd5ed45` | `sha256:07eabf42e7cde8ce9db3665ff9abb578d87649a9ed596252dd2125bc6b983a1e` |
| DIS-003 | candidate edge | `sha256:2be01bb4360bdd1a5a99369f4299cbb56144cc5e4f60726a8545e99270eddc4c` | `sha256:fbf2b9f652491aa838e048ef37e51d84cd00c819e45b36722abdb9df13631090` |
| DIS-004 | retracted edge ledger | `sha256:a26576c16c6a49f2a373d0ee4fd04cb7e3420736aee300f416108b17d6c36719` | `sha256:075536545135a0fc92f6bbe461dcb29ce3da11afe3b31d1ea437d709da7cb7e4` |
| DIS-005 | depth-one frontier | `sha256:266925b5c017ce4940c2803e3aeb82cb284900781f688eaea386c0a886cf1aa5` | `sha256:59509f8ade599cadc8c5737c7e4dd03f0e4ed723f8ed86029869196b1098e170` |
| DIS-006 | implicit boundary | `sha256:dfec4d69e644f2cdcc450a61f0d643acf6a208c395cdd3ebf9cf9bac7ec4b8c2` | `sha256:f9ac32491d65b2b04eb10497c30ce04d3eff467b28f4c9938ccdcc4fbb00c2f9` |
| DIS-007 | candidate root | `sha256:1cbebcc7e6eabb8b8ca61ddce5f13c71afe803ad73ae96bf8891745aa20e8156` | `sha256:eab8ce5daa54a01916ef6a1e3ab1f4c68cc49b05dd2adfd7017ee4384e2895b3` |
| DIS-008 | candidate residual without cut | `sha256:5a75f59749fce34ea5c01c3494beef27badd8cbc440859d3a69cf36cc40995b7` | `sha256:585849c0231514c58283bfa81cda2f3ab4325a837858bea0d447e030458a0785` |

The resolution observations chronologically bind the earlier repaired Go executable
`sha256:94bc22fd4608f7017d9539ccd8fa9053f90899d3ffba603c77faa5776c7c0f55`.
They remain valid historical resolution evidence. The later final source and installed parity summaries
bind the stable production closure and executable above and replay all seven corresponding branch
classes; they do not overwrite the resolution records.

## Build and quality replay

- `make check`: passed; 470 Python tests, 90.35% coverage, Ruff, schema drift, benchmark drift, bundle
  drift, Phase-A CTK, Go Phase-A differential, seed-2 parity, and legacy TCK all passed.
- Go Supplier kernel: `go test ./...` and `go vet ./...` passed.
- Two wheel builds were byte-identical; the installed wheel was imported from `site-packages` outside
  the repository and invoked from an empty SUT working directory. The hardened harness resolves the
  adapter module through that target interpreter rather than through the harness process. The v0alpha2
  ledger records all 116 wheel entries; 115 installed payload files matched their wheel bytes after
  excluding pip-rewritten `RECORD`; and 13 installed distributions with 442 declared files were bound
  alongside the target interpreter, repository inputs, and semantic production closure.
- Installed replay: seed-2 52/52, legacy TCK 33/33, Phase-A CTK 24/24 required, demo `ACCEPT`.
- Two `go build -trimpath` outputs were byte-identical and matched the disclosed executable digest.

The replay binding class is `self-attested-isolated-process-observation/v1`. Repository bytes and
optional rebuilds make declared byte drift falsifiable, but the stored responses are not
cryptographically linked to the wheel/executable invocation. Python standard-library and
operating-system bytes are not covered. This is not SLSA-style provenance or a supply-chain
attestation. The wheel and final Go executable bytes are not retained in the repository; their raw
digests are external-artifact attestations backed by the replay record and rebuild check.

No standalone static type checker is configured. Therefore the composite acceptance task that requires
lint **and type checking** remains open even though the existing `make check` gate is green. A clean Git
archive was not created from this uncommitted working tree; archive-parity tasks also remain open.

## What this establishes

The result is evidence for a narrow but important OAC thesis: a public organizational semantic
contract can make two different-language, different-runtime kernels converge on status, reasons, and
canonical report bytes without standardizing an Agent topology, while public generated cases can reject
implementations that merely memorize frozen answers or erase Unknown root authority.

The evidence is especially useful because the differential process found real disagreements, changed
public rules and both implementations where necessary, preserved the failures, and then replayed the
resolved contract. That is a prototype of a standard-evolution mechanism, not merely another Agent.

The v0alpha2 manifest strengthens the evidence discipline, not the semantic scope: a checker can now
rebuild the public case matrix, recompute all observations/counts/incident closures, verify both
summary relationships and both mutant gates, and audit the installed-material ledger. It does not
convert self-attested execution into independently witnessed provenance.

## What remains open

1. The Go kernel is same-repository, AI-assisted, reference-source-exposed internal work. A language or
   process boundary is not organizational independence.
2. The 52 observations are public, selected representatives. They do not prove complete Supplier v0.2
   coverage or resist every form of overfitting.
3. `stdio-v2` has not yet demonstrated fixed-plan verification by two kernels, including acceptance of
   two topologically different but contract-valid plans.
4. There is no clean-archive replay, external maintainer, signed suite registry, public errata body, or
   certification process.
5. There is no qualified human organizational Ground Truth, enterprise-twin outcome certificate, ROI,
   security evaluation, or evidence that one contract fits every enterprise.
6. The installed ledger excludes Python standard-library and operating-system bytes and has no
   cryptographic replay-to-artifact execution provenance. A byte-reproducible wheel or Go binary does
   not by itself prove that a stored response was produced by that artifact.

The next evidence-increasing increment is the bounded plural fixed-plan verification relation in Spec
003 A1.1, not a broader enterprise claim. It should compare contract verdicts and required/forbidden
reasons while deliberately accepting multiple legal Agent/WorkUnit topologies.
