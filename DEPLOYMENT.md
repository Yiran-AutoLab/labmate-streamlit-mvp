# Private-source demo deployment

The repository can remain private while the compiled frontend and running API are public.
The suggested first deployment is:

- FastAPI: Render web service
- Next.js: Vercel project with `frontend` as the Root Directory

## 1. Deploy the API on Render

Create a Blueprint from the private repository. Render reads `render.yaml` from the repository root.
Set these secret/environment values in Render:

```text
OPENROUTER_API_KEY=your_server_side_key
DEMO_ACCESS_CODE=a-long-code-you-share-with-reviewers
ALLOWED_ORIGINS=https://your-frontend.vercel.app
```

Do not prefix the LLM key with `NEXT_PUBLIC_`. The browser never needs or receives it.
After deployment, verify `https://your-api.onrender.com/health` returns `{"status":"ok"}`.

## 2. Deploy the frontend on Vercel

Import the same private repository and set the project Root Directory to `frontend`.
Add these Vercel environment variables:

```text
NEXT_PUBLIC_API_BASE_URL=https://your-api.onrender.com
NEXT_PUBLIC_LLM_PROVIDER=OpenRouter
NEXT_PUBLIC_LLM_MODEL=openrouter/free
```

Redeploy after changing environment variables. Enter `DEMO_ACCESS_CODE` in the app when testing.

## 3. Local protected-demo test

Backend:

```bash
export OPENROUTER_API_KEY="..."
export DEMO_ACCESS_CODE="labmate-demo"
uvicorn backend.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

The access code and current experiment ID are kept in browser `sessionStorage`. A page refresh restores
the experiment while the same backend process is alive. The current store is in memory, so a backend
restart or free-host sleep/restart clears experiments.

## Security boundaries

- Provider API keys live only in backend environment variables.
- `/api/*` is protected when `DEMO_ACCESS_CODE` is set.
- Requests are rate-limited per client IP in memory.
- CORS accepts only `ALLOWED_ORIGINS`.
- This is invitation-code protection, not per-user authentication.
- For durable experiments or multiple users, replace `backend/store.py` with a database-backed store.
