# Deployment

## Vercel + Neon (current, live at https://data-sentinel-ai.vercel.app)

1. `vercel link` the repository root (the entrypoint is `app.py`; `vercel.json` builds the frontend).
2. Add Neon from the Vercel Marketplace on the free plan and connect it to the project; it sets `DATABASE_URL`.
3. Add `SECRET_KEY` (random, sensitive) and `SENTINEL_ENV=production` for Production.
4. `vercel deploy --prod`, then run the E2E suite with `BASE_URL` set to the production URL.

Migrations run automatically on cold start. Serverless mode is enabled by `VERCEL=1`, which Vercel sets.

## Alternative: Render free + Neon free

1. **Database**: create a free project at [neon.com](https://neon.com) and copy the connection string (`postgresql://…?sslmode=require`).
2. **App**: in [Render](https://render.com), choose **New → Blueprint**, connect this GitHub repository, and Render reads `render.yaml` (Docker, free plan, health check `/api/health`, generated `SECRET_KEY`).
3. Set `DATABASE_URL` to the Neon connection string when prompted. Migrations run automatically on boot.
4. Verify:
   ```bash
   curl https://<service>.onrender.com/api/health
   cd e2e && BASE_URL=https://<service>.onrender.com npx playwright test
   ```

Notes
- The first request after 15 minutes idle takes about a minute (free-tier spin-up). The E2E timeouts allow for this.
- `COOKIE_SECURE` defaults to true in production; Render serves HTTPS.
- To upgrade without code changes: Render Starter ($7/month, no spin-down), Neon Launch for more storage.

Any Docker host works the same way: build the root `Dockerfile`, set `SECRET_KEY` and `DATABASE_URL`, and expose `$PORT`.
