# LabMate Handoff Notes

This is the current working prototype for the LabMate human-in-the-loop plate layout agent.

The project currently has two Python runnable entry points:

- Streamlit app: full existing review/edit/export workflow
- FastAPI backend: JSON API over the same Python service layer

## What Is In This Version

Core behavior:

- Natural language protocol to draft plate layout
- Human review and manual table editing in Streamlit
- Natural language correction parsing
- Memory/context support in the Streamlit workflow
- Deterministic validation
- Source map and Hamilton transfer table generation
- Excel export
- FastAPI endpoints that reuse the same service modules

Important design boundary:

- LLM code interprets protocols and correction commands.
- Deterministic Python code applies operations, validates tables, builds source maps, compiles transfers, and exports files.

## Project Structure

```text
app.py                         Streamlit app

agents/                        LLM-facing protocol/correction/review code
models/                        Shared data models
workflows/                     Layout, contents, editor, transfer workflows
validators/                    Deterministic validation modules
outputs/                       Excel writer and generated export location

services/                      Streamlit-independent business service layer
  experiment_service.py
  correction_service.py
  validation_service.py
  transfer_service.py

backend/                       FastAPI app
  main.py
  schemas.py
  store.py

tests/                         Pytest regression tests for service behavior
examples/                      Example protocols
```

## What Not To Share

Do not share local secrets or generated caches:

```text
.streamlit/labmate_secrets.json
.streamlit/secrets.toml
.env
__pycache__/
.pytest_cache/
outputs/latest/
outputs/test_run/
```

The packaged zip should exclude these.

## Setup

Python:

```bash
cd /path/to/labmate_streamlit_mvp_remember_refactor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Streamlit demo:

```bash
cd /path/to/labmate_streamlit_mvp_remember_refactor
streamlit run app.py
```

Open:

```text
http://localhost:8501
```

FastAPI backend:

```bash
cd /path/to/labmate_streamlit_mvp_remember_refactor
uvicorn backend.main:app --reload --port 8000
```

Open:

```text
http://127.0.0.1:8000/docs
```

## API Endpoints

Health:

```http
GET /health
```

Generate:

```http
POST /api/generate-layout
```

Typical JSON:

```json
{
  "protocol": "Ld,Mi two-strain combinations",
  "provider": "Mock",
  "model": "mock",
  "api_key": null
}
```

Correction:

```http
POST /api/interpret-correction
POST /api/apply-correction
```

Validate:

```http
POST /api/validate
```

Export:

```http
POST /api/export
```

Use `/docs` for the current exact request and response schemas.

## Current Practical Notes

- Use provider `Mock` for fast local testing without an API key.
- Use OpenRouter/OpenAI with an API key for real LLM behavior.
- Streamlit is still the most complete UI.
- FastAPI keeps experiment state in a simple in-memory store. Restarting the backend clears it.

## Regression Checks

```bash
cd /path/to/labmate_streamlit_mvp_remember_refactor
python3 -m pytest -q
python3 -m py_compile app.py backend/main.py backend/schemas.py backend/store.py services/*.py
```

## Suggested Next Work

- Move the remaining full edit/chat/review UI out of Streamlit gradually after the API contract is stable.
- Add persistent experiment projects only after API behavior is stable.
- Keep service modules free of Streamlit imports so Streamlit and FastAPI continue to share one source of truth.
