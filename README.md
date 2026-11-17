# Research Scout — Phase 2 (new Supabase API keys)

This is the cloud-discovery starter. It saves Tavily/RSS candidates to the existing Supabase `findings` table and sends a Discord **status** notification. Local Ollama classification, Brave fallback, and category-specific digests are **not implemented yet**.

## Set up
1. In Supabase SQL Editor, run the Phase 1 database schema if not already created. `findings.url` must have a UNIQUE constraint.
2. In GitHub Actions repository secrets, create `SUPABASE_URL`, `SUPABASE_SECRET_KEY` (new `sb_secret_...` key), `TAVILY_API_KEY`, and `DISCORD_WEBHOOK_STATUS`.
3. Remove the old `SUPABASE_SERVICE_ROLE_KEY` secret only after confirming the new workflow succeeds.
4. Push the `.github/workflows/daily-discovery.yml` file to the default branch and run it manually from Actions > Daily Research Discovery > Run workflow.
5. Check the job logs, Supabase `findings` table, and Discord `#research-status`.

## Local test (Windows)
Copy `.env.example` to `.env`, enter real credentials, then run:

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 -m cloud.main
```

Never commit `.env`. Use `.gitignore` entry `.env`.

## Schedule
`0 0 * * *` UTC = 08:00 Asia/Manila daily. GitHub Actions scheduled runs may be delayed.

## Security
`SUPABASE_SECRET_KEY` is privileged. Keep it in GitHub Actions Secrets or a private local `.env`; never expose it to browser JavaScript, Discord, or a public repository. `SUPABASE_PUBLISHABLE_KEY` is reserved for the future authenticated dashboard, not used by the cloud collector.

## Current limitations
- Patent candidates are web-search results, not validated patent records.
- Competition eligibility is not yet verified.
- The deduplication normalizer removes URL query strings, which may conflate distinct pages.
- Counts in the status notification are submitted candidates, not confirmed new inserts.
