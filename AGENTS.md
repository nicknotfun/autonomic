# Agent Practices

This checkout is centered on the `amp` package. Treat `amp/` as the supported
implementation surface unless the user explicitly asks for experimental MAS/MRAD
work.

## Repository Scope

- `amp/` is the active direct-amplifier codec and transport package.
- `mas/`, `mas_summary.py`, and related MAS/MRAD files are experimental
  prototypes. Keep them marked as unsupported and do not build new integration
  behavior on them by default.
- The old high-level `autonomic` client package is not present in this branch.
  Do not write docs, tests, or examples that assume it exists.

## Protocol Code

- `amp/codec.py` is the protocol catalog. Add or change opcode behavior there
  as dataclasses with `PATTERN` strings.
- Keep `PROTOCOL.md` succinct and mirrored to the current `amp/codec.py`
  dataclasses and pattern syntax. Do not turn it back into a broad historical
  MRAD/MAS reference.
- Use the pattern compiler in `amp/encoder.py` rather than hand-rolled encode or
  parse logic when a row can be described declaratively.
- Use `guid` for Autonomic/Windows wire-order GUID fields (`UUID.bytes_le`).
  Keep `uuid` for RFC UUID byte order only.
- Preserve observed trailing or opaque status bytes in explicit `detail`,
  `payload`, or similarly named fields instead of discarding them.

## Byte Handling

- Prefer `HexBytes` helpers over repeating byte conversion code:
  `HexBytes.from_int()`, `.int()`, `HexBytes.from_uuid()`, `.uuid()`,
  `HexBytes.from_utf8()`, and `.utf8()`.
- Keep ASCII hex formatting uppercase through `str(HexBytes(...))`.
- Validate value ranges at the helper or parse-step boundary so codec classes
  stay simple dataclasses.

## Tests

- Tests live under `amp/tests`.
- Keep a parallel test file for every `amp/*.py` module:
  `test_byte_utils.py`, `test_codec.py`, `test_encoder.py`,
  `test_exceptions.py`, `test_transport.py`, and `test_types.py`.
- Pytest is configured to collect only `amp/tests`; do not recreate the old root
  `tests/` directory for this branch.
- Add focused tests for every new opcode pattern and every new pattern type or
  `HexBytes` helper.
- Run `/home/nick/.venvs/default/bin/python -m pytest` after code changes.
  Run `/home/nick/.venvs/default/bin/python -m compileall -q amp` when changing
  import/module structure or syntax-sensitive files.

## Live Devices

- Read-only probes against `10.1.0.200` and `10.1.0.201` are useful for
  validating decode coverage, but keep them conservative and documented in the
  work summary.
- Do not issue destructive or identity-repair operations during probes. In
  particular, do not add `3A` GUID write/repair behavior unless explicitly
  requested.

## Documentation

- README should describe the current `amp` workflow and explicitly warn that
  MAS/MRAD prototype files are unsupported.
- If MAS files are touched, preserve a top-level note in those files that they
  are experimental and should not be used for integration work.
