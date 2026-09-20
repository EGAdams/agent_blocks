# Python tests

Mirrors the package tree: a block at
`src/voice_communication/<block>/<block>.py` is tested at
`tests/<block>/test_<block>.py`.

The mirror is what preserves the beside-its-interface rule under a src layout —
colocating tests inside `src/` would ship them in the wheel. Documentation is
*not* mirrored; it stays with the port it describes, at
`src/voice_communication/<block>/docs/<block>.md`.

```bash
cd voice_communication/py && python -m pytest        # testpaths/pythonpath preconfigured
```

TypeScript tests colocate instead, as `<block>.test.ts` beside the port; `ts/`
has no test runner wired up yet.
