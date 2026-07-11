# Logistics CRM — Live Deployment Guide

Recommended host: **Render.com** (easiest for Flask + managed PostgreSQL, free tier to start).
The repo already contains: `Procfile`, `runtime.txt`, `render.yaml`, `.gitignore`, `requirements.txt`, `wsgi.py`.

---

## Prerequisites
1. A **GitHub** account — https://github.com (free).
2. **Git** installed — https://git-scm.com/download/win
3. A **Render** account — https://render.com (sign up with GitHub).

---

## Step 1 — Push the code to GitHub
Open a terminal **inside the `logistics_crm` folder** and run:

```bash
git init
git add .
git commit -m "Logistics CRM ready for deploy"
git branch -M main
```

Create a new empty repo on GitHub (e.g. `logistics-crm`), then:

```bash
git remote add origin https://github.com/<your-username>/logistics-crm.git
git push -u origin main
```

> `.gitignore` already excludes `.env`, `.venv`, `instance/`, so your password is NOT uploaded.

---

## Step 2 — Deploy on Render (Blueprint = one click)
1. Render dashboard → **New +** → **Blueprint**.
2. Connect your GitHub and pick the `logistics-crm` repo.
3. Render reads `render.yaml` and shows: 1 Web Service + 1 PostgreSQL database.
4. Set the secret env values when asked:
   - `ADMIN_PASSWORD` = a strong password for the `admin` login
   - `ARUN_PASSWORD` = a strong password for the `arun` login
   - (`CRM_SECRET_KEY` and `DATABASE_URL` are filled automatically.)
5. Click **Apply**. Render will:
   - create the PostgreSQL database,
   - install dependencies,
   - run `wsgi.py` → `init_db()` creates all tables + seed data automatically,
   - start the app with gunicorn.
6. After ~3–5 min you get a URL like **https://logistics-crm.onrender.com** — that's your live CRM.

---

## Step 3 — First login & security
- Open the URL → log in with `admin` / your `ADMIN_PASSWORD`.
- Default demo passwords are overridden by the env values you set, so they're safe.
- HTTPS is automatic on Render; `COOKIE_SECURE=1` is already set.

---

## Step 4 — Custom domain (optional)
1. Buy a domain (GoDaddy / Google Domains / Namecheap).
2. Render service → **Settings → Custom Domains** → add your domain.
3. Add the CNAME record Render shows, in your domain's DNS. HTTPS cert is automatic.

---

## Updating the live app later
Just push changes to GitHub — Render auto-deploys:
```bash
git add .
git commit -m "update"
git push
```

---

## Environment variables reference
| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (auto-set by Render) |
| `CRM_SECRET_KEY` | Session signing secret (auto-generated) |
| `COOKIE_SECURE` | `1` = cookies only over HTTPS |
| `ADMIN_PASSWORD` | Password for the `admin` user |
| `ARUN_PASSWORD` | Password for the `arun` user |
| `PORT` | Provided by the host automatically |

---

## Cost note
- Render **free** web + DB = good for testing/demo. Free Postgres is temporary and the web app sleeps when idle.
- For a real business, upgrade to **Starter** (~$7/mo web + ~$7/mo DB) so it never sleeps and the DB is permanent with backups.

---

## Alternative hosts
- **Railway.app** — similar flow: New Project → Deploy from GitHub → Add PostgreSQL plugin → it sets `DATABASE_URL`. Start command `gunicorn wsgi:app`.
- **Google Cloud Run + Cloud SQL (PostgreSQL)** — most scalable but advanced: containerize, push to Artifact Registry, deploy to Cloud Run, connect Cloud SQL, set `DATABASE_URL`. Use only if you specifically need Google Cloud.
