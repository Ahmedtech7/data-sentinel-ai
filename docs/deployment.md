# Deployment (Render free + Neon free, $0/month)

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
