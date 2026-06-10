# Testing

The suite covers every pipeline module through its public behavior, with no
network or live Ollama dependency. HTTP calls (Ollama, website fetches) are
mocked with `respx`; the database tests use real SQLite in a temp directory.

```bash
make install-dev   # pytest, pytest-cov, respx
make test          # run the suite
make test-cov      # with coverage report
```

## What is covered

| Test module | Focus |
|-------------|-------|
| `test_cluster.py` | keyword classification into all 9 clusters + fallback |
| `test_search.py` | name cleaning, directory detection, dedupe, throttling, library-failure handling |
| `test_website_checker.py` | audit heuristics, directory/no-url short-circuits, fetch success/failure (mocked) |
| `test_email_generator.py` | prompt construction, SUBJECT/BODY parsing, improve vs build path, fallback |
| `test_ollama_client.py` | generate, streaming, model fallback, all-models-missing, health check (mocked) |
| `test_storage.py` | table creation, JSON observation round-trip, idempotent upserts |
| `test_export.py` | header creation, incremental export, `exported_at` column, no-rows case |

## Conventions

- Test data is built with factory functions (`make_business`, `make_email` in
  `conftest.py`), not module-level fixtures with shared mutable state.
- Tests exercise the public API of each module and assert on observable
  behavior, not implementation details.
- Settings are injected per test (temp DB, deterministic Ollama URLs) so tests
  are isolated and parallelizable.

## A bug the suite caught

The streaming path in `ollama_client` originally inspected `response.text` on a
404 to detect a missing model. On a streamed response the body is not read yet,
which raised `ResponseNotRead`. The test
`test_generate_stream_falls_back_on_missing_model` surfaced it; the fix reads
the body before inspecting it on non-200 streamed responses.
