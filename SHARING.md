# Sharing And Sync

## Option 1: Zip For A Quick Handoff

Send these files and folders:

```text
app.py
labmate/
README.md
requirements.txt
smoke_test.py
.env.example
```

Do not send:

```text
.env
.venv/
venv/
outputs/
__pycache__/
*.pyc
```

The receiver can run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Option 2: Private GitHub Repo

Best for shared development and syncing changes.

```bash
git init
git add .
git commit -m "Initial LabMate Streamlit MVP"
git remote add origin <your-private-repo-url>
git push -u origin main
```

Keep the repo private if this contains lab-specific workflows or source position conventions.

## Option 3: Streamlit Community Cloud

Good for a demo link, but only if you are comfortable hosting the app externally.

Use Streamlit secrets for API keys instead of committing `.env`:

```toml
OPENROUTER_API_KEY = "YOUR_OPENROUTER_API_KEY"
```

For lab data or unpublished experiments, prefer local running or a private internal deployment.

## Suggested Next Step

Add project-level persistence:

```text
projects/<experiment_name>/
  experiment_design.json
  plate_map.csv
  hamilton_worklist.csv
  validation_report.csv
  corrections.md
  all_results.xlsx

memory/
  lab_defaults.json
  strain_registry.csv
```

That gives each experiment its own record while still allowing long-term defaults.
