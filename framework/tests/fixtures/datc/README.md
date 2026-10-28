# DATC Fixture Corpus

This directory contains committed DATC-style fixtures for deterministic regression coverage.

## Fixture Format
Each fixture is a JSON object:
- `schema_version`: fixture schema version (`"1.0"`)
- `case_id`: stable test id
- `title`: human-readable label
- `initial_state`: board-state payload accepted by `DiplomacyRuleEngine.validate_orders()`
- `orders`: order list payload accepted by `DiplomacyRuleEngine.validate_orders()`
- `expected`: asserted subset of outputs from validation/resolution

Supported `expected` keys:
- `validated_orders`: map of `unit_id -> validation`
- `movement_outcomes`: map of `unit_id -> result`
- `support_outcomes`: map of `unit_id -> status`
- `convoy_outcomes`: map of `army_unit_id -> status`
- `retreat_outcomes`: map of `unit_id -> result`
- `adjustment_outcomes`: map of `power -> result`

## External Full Corpus
Set `LLM_DIPLOMACY_DATC_DIR` to a local DATC corpus directory to run an extended suite.
If the directory is not present, external DATC tests are skipped (normal local runs still pass).
