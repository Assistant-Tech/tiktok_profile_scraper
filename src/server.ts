import express, { NextFunction, Request, Response } from 'express';
import pinoHttp from 'pino-http';
import pLimit from 'p-limit';
import { config } from './config';
import { logger } from './logger';
import { profileCache, negativeCache } from './cache';
import { normalizeUsername, scrapeProfile } from './scraper';
import { workerPool } from './worker';
import { ProfileResponse, ScrapeError } from './types';

const app = express();
app.disable('x-powered-by');
app.use(pinoHttp({ logger }));

const limit = pLimit(config.MAX_CONCURRENCY);

function requireToken(req: Request, res: Response, next: NextFunction): void {
  if (!config.SCRAPER_API_TOKEN) {
    next();
    return;
  }
  const expected = `Bearer ${config.SCRAPER_API_TOKEN}`;
  const auth = req.header('authorization') ?? '';
  if (auth !== expected) {
    res.status(401).json({ error: 'unauthorized', detail: 'invalid token' });
    return;
  }
  next();
}

app.get('/health', (_req, res) => {
  res.json({ status: 'ok' });
});

app.get('/profile/:username', requireToken, async (req, res) => {
  const raw = req.params.username;
  const cleaned = normalizeUsername(typeof raw === 'string' ? raw : null);
  if (!cleaned) {
    res.status(400).json({ error: 'bad_request', detail: 'invalid username' });
    return;
  }

  const negative = negativeCache.get(cleaned);
  if (negative === 'WAF') {
    res.status(503).json({ error: 'service_unavailable', detail: 'WAF_BLOCKED' });
    return;
  }
  if (negative === 'NOT_FOUND') {
    res.status(404).json({ error: 'not_found', detail: 'PROFILE_NOT_FOUND' });
    return;
  }

  const cached = profileCache.get(cleaned);
  if (cached) {
    const body: ProfileResponse = { profile: cached, cached: true, elapsed_ms: 0 };
    res.json(body);
    return;
  }

  const start = Date.now();
  try {
    const profile = await limit(() => scrapeProfile(cleaned));
    profileCache.set(cleaned, profile);
    const body: ProfileResponse = {
      profile,
      cached: false,
      elapsed_ms: Date.now() - start,
    };
    res.json(body);
  } catch (err) {
    if (err instanceof ScrapeError) {
      if (err.code === 'WAF_BLOCKED') {
        negativeCache.set(cleaned, 'WAF');
        logger.warn({ username: cleaned }, 'WAF blocked');
        res.status(503).json({ error: 'service_unavailable', detail: 'WAF_BLOCKED' });
        return;
      }
      if (err.code === 'PROFILE_NOT_FOUND') {
        negativeCache.set(cleaned, 'NOT_FOUND');
        logger.info({ username: cleaned, msg: err.message }, 'profile not found');
        res.status(404).json({ error: 'not_found', detail: 'PROFILE_NOT_FOUND' });
        return;
      }
      logger.error({ username: cleaned, msg: err.message, code: err.code }, 'scrape error');
      res.status(502).json({ error: 'bad_gateway', detail: 'SCRAPE_ERROR' });
      return;
    }
    logger.error({ username: cleaned, err }, 'unexpected error');
    res.status(500).json({ error: 'internal_error', detail: 'INTERNAL_ERROR' });
  }
});

app.use((err: unknown, _req: Request, res: Response, _next: NextFunction) => {
  logger.error({ err }, 'unhandled error');
  res.status(500).json({ error: 'internal_error', detail: 'INTERNAL_ERROR' });
});

async function main(): Promise<void> {
  await workerPool.start();
  const server = app.listen(config.PORT, config.HOST, () => {
    logger.info({ host: config.HOST, port: config.PORT }, 'tiktok-scraper listening');
  });

  const shutdown = (signal: string): void => {
    logger.info({ signal }, 'shutdown initiated');
    server.close(() => logger.info('http server closed'));
    workerPool.shutdown();
    setTimeout(() => process.exit(0), 2000).unref();
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

main().catch((err) => {
  logger.fatal({ err }, 'failed to start');
  process.exit(1);
});
