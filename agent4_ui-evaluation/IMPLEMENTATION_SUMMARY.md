# AskBase UI — What We Built

## Current release

AskBase UI v2.0.0 is a responsive, config-driven frontend for the UI, integration, and evaluation workstream. It uses plain HTML, CSS, JavaScript ES modules, and the existing dependency-free Node.js server.

The application is explicitly labelled **Mock backend**. Evaluation values are **baseline fixtures, not live measurements**.

## Product experience

- Warm cream visual system with orange accents, serif highlights, pill navigation, layered cards, and responsive hero illustrations
- Analyze, Upload, Evaluation, and Docs pages
- Sample retail schema explorer and data-quality warnings
- Natural-language question input with five JSON-configured examples
- Seven mock scenarios: verified, messy data, clarification, empty result, model offline, unsafe SQL, and truncated results
- Five-stage pipeline: Generate SQL, Validate SQL, Execute, Verify, and Summarize
- Result stack containing answer, verification, SQL, actual rows, and chart
- Distinct malformed-response and non-success states

## Dataset upload

- CSV-first multi-file drag/drop and keyboard-accessible file selection
- Optional DB/SQLite metadata selection
- Local-only parsing and profiling with no file upload to the server
- UTF-8 BOM and comma, semicolon, or tab delimiter support
- Quoted fields, escaped quotes, embedded delimiters, and multiline values
- File, encoding, header, size, row, and column validation
- Logical type inference, null/distinct/duplicate/invalid counts, numeric/date statistics, and quality warnings
- Privacy-aware sanitized AI context with sensitive samples omitted
- File, size, row, column, and delimiter summary chips
- Six-row preview and downloadable sample CSV
- Uploaded-dataset analysis receives profile context through the mock boundary and is explicitly marked mock-only

## Evaluation and Docs

- Thirteen baseline evaluation fixtures
- Metrics with explicit denominators
- Search, category, pass, and fail filters
- Expected-versus-observed outcomes
- Schema-validated JSON report export
- Four readable API contract cards with access to raw JSON

## Developer experience

The Developer drawer includes:

- Syntax-highlighted raw response JSON
- Pipeline stages and per-stage durations
- Structured JSON logs
- Active configuration and dataset metadata
- Downloadable debug bundle
- App version and Mock backend mode

## Architecture

JSON files configure the app, theme, scenarios, examples, sample schema, and evaluation fixtures. JSON schemas validate analysis responses, dataset profiles, and evaluation reports.

Shared modules provide schema validation, an event bus, an immutable reducer-style state store, stale-request protection, structured logging, theme loading, and an ordered pipeline.

The asynchronous integration boundary remains:

```js
runAnalysis(question, databaseConfig, options)
```

SQL validation, execution, profiling, and answer generation remain backend responsibilities.

## Run and verify

```sh
cd agent4_ui-evaluation
npm start
npm test
```

Open `http://127.0.0.1:4173`.

All **36 tests pass**, including the original 14. Browser verification covers the verified result, pipeline, Developer telemetry, local profiling contracts, mock profile-context handoff, evaluation filtering, and Docs.

See `README.md` for the Mermaid architecture diagram, complete file map, configuration reference, integration contract, and changelog.
