# Study AI

A FastAPI + SQLite study dashboard demo.

## Run locally

Windows:

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## Deploy to Render

Create a GitHub repository and upload the contents of this folder (do not upload `.venv` or `study.db`).

Render settings:

- Runtime: Python
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health Check Path: `/health`

Important: this demo uses SQLite and local uploaded files. On a normal ephemeral cloud service, files can be lost when the service is replaced/restarted. For permanent online data, move the database to PostgreSQL and uploads to persistent object storage before treating it as a production app.
