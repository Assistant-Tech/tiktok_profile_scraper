import { LRUCache } from 'lru-cache';
import Redis from 'ioredis';
import { config } from './config';
import { logger } from './logger';
import { ScrapedProfile } from './types';

export type NegativeReason = 'WAF' | 'NOT_FOUND' | 'RESTRICTED';

// Two-tier cache: in-memory LRU (L1) in front of optional Redis (L2).
// Redis adds persistence across restarts and sharing between instances;
// when REDIS_URL is unset or Redis is down, L1 alone serves as before.
const memProfiles = new LRUCache<string, ScrapedProfile>({
  max: config.CACHE_MAX,
  ttl: config.CACHE_TTL_S * 1000,
});

const memNegative = new LRUCache<string, NegativeReason>({
  max: config.CACHE_MAX,
  ttl: config.NEGATIVE_CACHE_TTL_S * 1000,
});

const PROFILE_PREFIX = 'tiktok:profile:';
const NEGATIVE_PREFIX = 'tiktok:negative:';

const redis: Redis | null = config.REDIS_URL
  ? new Redis(config.REDIS_URL, {
      maxRetriesPerRequest: 1,
      enableOfflineQueue: false,
      connectTimeout: 3000,
      retryStrategy: (times) => Math.min(30_000, 500 * 2 ** times),
    })
  : null;

if (redis) {
  redis.on('ready', () => logger.info('redis connected'));
  redis.on('error', (err) => logger.warn({ err: err.message }, 'redis error'));
}

async function redisGet<T>(key: string): Promise<T | null> {
  if (!redis || redis.status !== 'ready') return null;
  try {
    const raw = await redis.get(key);
    return raw === null ? null : (JSON.parse(raw) as T);
  } catch (err) {
    logger.warn({ key, err: (err as Error).message }, 'redis get failed');
    return null;
  }
}

async function redisSet(key: string, value: unknown, ttlS: number): Promise<void> {
  if (!redis || redis.status !== 'ready') return;
  try {
    await redis.set(key, JSON.stringify(value), 'EX', ttlS);
  } catch (err) {
    logger.warn({ key, err: (err as Error).message }, 'redis set failed');
  }
}

export const cache = {
  async getProfile(username: string): Promise<ScrapedProfile | null> {
    const mem = memProfiles.get(username);
    if (mem) return mem;
    const hit = await redisGet<ScrapedProfile>(PROFILE_PREFIX + username);
    if (hit) memProfiles.set(username, hit);
    return hit;
  },

  async setProfile(username: string, profile: ScrapedProfile): Promise<void> {
    memProfiles.set(username, profile);
    await redisSet(PROFILE_PREFIX + username, profile, config.CACHE_TTL_S);
  },

  async getNegative(username: string): Promise<NegativeReason | null> {
    const mem = memNegative.get(username);
    if (mem) return mem;
    const hit = await redisGet<NegativeReason>(NEGATIVE_PREFIX + username);
    if (hit) memNegative.set(username, hit);
    return hit;
  },

  async setNegative(username: string, reason: NegativeReason): Promise<void> {
    memNegative.set(username, reason);
    await redisSet(NEGATIVE_PREFIX + username, reason, config.NEGATIVE_CACHE_TTL_S);
  },

  async shutdown(): Promise<void> {
    if (redis) await redis.quit().catch(() => undefined);
  },
};
