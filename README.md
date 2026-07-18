# LabMate Plate Layout Agent MVP

Streamlit MVP for a human-in-the-loop lab automation plate layout agent.

The core goal is layout-first:

1. Understand how each destination well should be arranged on a 96-well plate.
2. Understand what liquids and volumes should be added to each well.
3. Let a human review and correct the layout.
4. Validate the corrected layout deterministically.
5. Export a Hamilton-compatible transfer table.

The included example protocol is PCR setup, but the app is designed as a general liquid-handling plate layout agent.

## Quick Start

```bash
cd labmate_streamlit_mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open:

```text
http://localhost:8501
```

## Run

Streamlit demo:

```bash
streamlit run app.py
```

FastAPI backend:

```bash
uvicorn backend.main:app --reload --port 8000
```

React frontend:

```bash
cd frontend
npm install
npm run dev
```

Then open:

```text
http://127.0.0.1:3000
```

## Private Hosted Demo

The Next.js client no longer sends LLM provider keys. FastAPI reads them from server environment
variables, and the hosted API can be protected with `DEMO_ACCESS_CODE`, CORS restrictions, and basic
per-IP rate limiting. A browser refresh restores the current in-memory experiment when the backend
process is still running.

See [DEPLOYMENT.md](DEPLOYMENT.md) for the Render + Vercel setup.

## Current MVP Behavior

The app now uses a real LLM for protocol interpretation, correction parsing, and review. Configure the provider in the Streamlit sidebar:

- `OpenRouter`
- `OpenAI`

The sidebar can remember your API key on this computer. The key is saved to:

```text
.streamlit/labmate_secrets.json
```

That file is ignored by git. Do not share it.

LLM agents are used only for:

- interpreting the natural language protocol
- generating the initial draft layout
- interpreting natural language correction commands
- reviewing whether the layout appears consistent with the protocol

Deterministic Python code is used for:

- applying layout operations
- calculating well contents and source usage
- validating wells, labels, volumes, sources, optional workflow-specific rules, and pipetting range
- compiling the Hamilton transfer table
- exporting Excel files

## Project Structure

```text
app.py

models/
  plate_models.py

agents/
  protocol_to_layout_agent.py
  correction_command_agent.py
  llm_reviewer.py

workflows/
  layout_generator.py
  well_contents_generator.py
  layout_editor.py
  transfer_compiler.py

validators/
  layout_validator.py
  volume_validator.py
  source_validator.py

outputs/
  excel_writer.py

examples/
  pcr_example.txt
```

## Core Tables

- Plate Layout
- Well Contents
- Source Map
- Summary Matrix
- Validation Report
- Hamilton Transfer Table

## Supported Correction Operations

The LLM correction parser should return these structured operations:

- `MOVE_WELL`
- `SWAP_WELLS`
- `MOVE_BY_LABEL`
- `AVOID_WELLS`
- `GROUP_BY`
- `FILL_BY_ROW`
- `FILL_BY_COLUMN`
- `CHANGE_VOLUME`
- `CHANGE_SOURCE`
- `ADD_REPLICATE`
- `REMOVE_REPLICATE`
- `REGENERATE_LAYOUT_WITH_CONSTRAINTS`

Example commands:

```text
move A1 to H12
swap A1 and A2
avoid A1,A2
change buffer volume to 6 uL
change reagent source to Reservoir_2 B1
fill by column
group by condition
```

## Test

```bash
python3 smoke_test.py
```

Expected output:

```text
Smoke test passed.
```
