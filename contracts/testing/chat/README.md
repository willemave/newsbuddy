# Shared chat scenarios

These YAML files describe synthetic initial state and external HTTP stubs for local evals
and integration tests. The Python generator in `python/evals/src/newsly_evals/chat/generator.py`
compiles them into transactional SQL; Rust migrations remain the schema authority.

See [the harness guide](../../../python/evals/CHAT_EVALS.md) for authoring and running cases.
