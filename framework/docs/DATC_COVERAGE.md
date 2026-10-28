# DATC coverage and project rule policy

The offline corpus covers all 164 official DATC v3 Chapter 6 case IDs. Its 172 indexed entries comprise 171 pinned upstream entries plus the missing 6.D.16 case. Seven entries represent historical alternative editions. The unchanged upstream JSON is MIT-licensed; provenance, checksum and separately permitted official-case supplements are in [the corpus README](../tests/fixtures/datc/upstream/README.md).

Run from the checkout:

```powershell
.\.venv\Scripts\python.exe scripts/run_datc.py
.\.venv\Scripts\python.exe -m pytest tests/test_datc_coverage.py tests/test_compliance_harness.py tests/test_diplomacy_movement_baseline.py
```

The selected corpus run reports 155 matching entries, nine declared project-policy divergences, one adapter limitation, and seven historical alternatives. This is not an unqualified standard-rules conformance claim. Every case is enumerated in `tests/fixtures/datc/upstream/coverage.json`. Missing entries, unexplained failures, changed declared outcomes and duplicate active occupancy fail the check. Declared differences retain the source expectations and pin the complete resulting state, rather than replacing the source expectation with whatever the engine returns.

## Existing policy divergences

- 6.F.16–6.F.18: route availability is frozen without the army's own support cuts or dislodgements. This can preserve a route whose fleet is subsequently dislodged by the army's effects.
- 6.F.22: both routes remain available at the initial per-army fixed point.
These existing project decisions differ from DATC paradox rules. The manifest gives case-specific reasoning. Cyclic availability (6.F.23) fails only the varying convoy moves; independent moves remain. See [rule specification](DIPLOMACY_RULE_SPEC.md).
- Upstream J04, J06, J07, J08 and J11 (official references are in the manifest): automatic disband uses distance to owned HOME centers on the unit-legal graph, then reverse lexical province/unit IDs. DATC2023 uses different center, distance and tie-breaking rules.
- Seven 1971/1982 expectation entries are tracked alternatives, not silently passed or treated as adapter failures. The runner reports them separately from nine project-policy divergences and the one adapter limitation.

Changing these policies requires a deliberate rules decision and a reviewed manifest update. Do not regenerate golden outcomes to hide a failure.

## Adapter limitation

Ordinary 6.G.11 omits explicit adjacent convoy intent. The converter infers `via_convoy` from a matching own-country convoy order, so its engine input includes information absent from the source notation and its result differs from the DATC expectation. The coverage manifest classifies and pins this as an adapter limitation. The explicit-intent variant passes separately. This convention is not specified as an existing project gameplay rule.

## Repairs established by the corpus

Movement resolves vacating chains and circular moves, prevents supported self-dislodgement, distinguishes prevention strength from dislodging strength, and handles opposed moves even when a third party contests a destination. A dislodged direct attack no longer prevents movement into its dislodger's origin; convoyed exchanges retain their separate behavior. Support can reach a split-coast province through either coast while an explicitly specified supported coast must match. Impossible convoy topology produces a substituted hold; a possible but unsuccessful convoy move cannot receive hold support. Convoy dislodgement records permit the corresponding legal retreat to the army's origin. Invalid builds do not consume quotas, and explicit disbands retain order-level outcomes.

The adapter preserves retreat identity across the preceding movement phase, translates source notation, restores source omissions and duplicate orders, and distinguishes fleet survival, route availability, explicit removal and automatic removal. For ordinary 6.G.11 it also applies the documented adapter convention above. Regression checks exercise the complete corpus with reversed order and unit arrays. Offline evidence establishes deterministic behavior under these cases, not gameplay strength or effectiveness of LLM prompts.
