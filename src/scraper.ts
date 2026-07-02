import { logger } from './logger';
import { ScrapedProfile, ScrapeError } from './types';
import { workerPool } from './worker';

const USERNAME_RE = /^[A-Za-z0-9._]{1,32}$/;
const MAX_ATTEMPTS = 2;

export function normalizeUsername(raw: string | undefined | null): string | null {
  if (!raw) return null;
  const cleaned = raw.trim().replace(/^@/, '');
  if (!USERNAME_RE.test(cleaned)) return null;
  return cleaned.toLowerCase();
}

function jitter(min: number, max: number): Promise<void> {
  const ms = Math.floor(min + Math.random() * (max - min));
  return new Promise((r) => setTimeout(r, ms));
}

type WorkerErrorCode =
  | 'WAF_BLOCKED'
  | 'PROFILE_NOT_FOUND'
  | 'PROFILE_RESTRICTED'
  | 'SCRAPE_ERROR'
  | 'TIMEOUT';

export async function scrapeProfile(username: string): Promise<ScrapedProfile> {
  let lastErr: ScrapeError | null = null;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    let resp;
    try {
      resp = await workerPool.request(username);
    } catch (err) {
      lastErr = new ScrapeError(
        `worker request failed: ${(err as Error).message}`,
        'SCRAPE_ERROR',
      );
      logger.warn({ username, attempt, err }, 'worker request failed');
      if (attempt < MAX_ATTEMPTS) await jitter(500 * attempt, 1500 * attempt);
      continue;
    }

    if (resp.profile) {
      logger.debug({ username, elapsed_ms: resp.elapsed_ms, attempt }, 'scrape ok');
      return resp.profile;
    }

    const err = resp.error ?? { code: 'SCRAPE_ERROR', message: 'unknown' };
    const code = err.code as WorkerErrorCode;
    lastErr = new ScrapeError(err.message, code);
    logger.warn({ username, attempt, code }, 'scrape attempt failed');

    if (code !== 'WAF_BLOCKED' && code !== 'TIMEOUT') break;
    if (attempt < MAX_ATTEMPTS) await jitter(1500 * attempt, 3000 * attempt);
  }
  throw lastErr ?? new ScrapeError(`scrape failed for @${username}`, 'SCRAPE_ERROR');
}
