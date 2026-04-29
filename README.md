# tiktok-scraper-service

Standalone TikTok profile scraper microservice. Puppeteer + stealth plugin behind an Express HTTP API. Designed to run on a VPS and be called from the Chatblix backend (`TikTokScraperClient`).

## API

`GET /health` → `{ "status": "ok" }`

`GET /profile/:username` (optional `Authorization: Bearer <SCRAPER_API_TOKEN>`)

Success `200`:
```json
{
  "profile": {
    "username": "khaby.lame",
    "name": "Khabane lame",
    "avatar_url": "https://...",
    "bio": "...",
    "verified": true,
    "follower_count": 162400000,
    "following_count": 80,
    "like_count": 2700000000,
    "region": "IT"
  },
  "cached": false,
  "elapsed_ms": 1834
}
```

Errors:
- `400` — invalid username
- `401` — invalid bearer token
- `404` — `PROFILE_NOT_FOUND`
- `429` — reserved (rate limit)
- `502` — `SCRAPE_ERROR`
- `503` — `WAF_BLOCKED`

## Local development

```bash
pnpm install        # or npm install / yarn
cp .env.example .env
pnpm run dev
```

Test:
```bash
curl http://localhost:8080/health
curl http://localhost:8080/profile/khaby.lame
```

## Docker

```bash
docker build -t tiktok-scraper-service .
docker run --rm -p 8080:8080 \
  -e SCRAPER_API_TOKEN=changeme \
  tiktok-scraper-service
```

The runtime image installs Debian Chromium and points Puppeteer at `/usr/bin/chromium` via `PUPPETEER_EXECUTABLE_PATH`.

## VPS deployment

Two recommended paths:

1. **Docker on the VPS** — `docker run -d --restart=unless-stopped -p 8080:8080 ...` behind nginx/Caddy with TLS. Set `SCRAPER_API_TOKEN`.
2. **systemd service** — `npm ci && npm run build && node dist/server.js` under a non-root user, fronted by nginx.

Open `:8080` only to your backend's egress IP, or terminate behind a reverse proxy with auth.

## Configuration

See `.env.example`. Notable knobs:

- `MAX_CONCURRENCY` — concurrent scrape jobs (default 2). Puppeteer is heavy; raise carefully.
- `CACHE_TTL_S` / `NEGATIVE_CACHE_TTL_S` — LRU cache windows for hits and 404/WAF responses.
- `PROXY_URL` — upstream proxy if TikTok blocks the VPS IP.
- `HEADLESS` — set `false` to debug locally with a visible browser.

## Backend wiring

The Nest backend already has `TikTokScraperClient`. Set:

```
TIKTOK_SCRAPER_URL=https://scraper.your-vps.example.com
TIKTOK_SCRAPER_TOKEN=<same value as SCRAPER_API_TOKEN>
```

Response shape and status codes match what the client expects (no client changes needed).

## Notes

- Stealth plugin masks common automation fingerprints; not a guarantee against TikTok's WAF. Add a residential proxy via `PROXY_URL` for production reliability.
- Image, font, media, and stylesheet requests are blocked at request-interception to cut latency and bandwidth.
- The browser is shared across requests; pages are created/destroyed per scrape.
