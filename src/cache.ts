import { LRUCache } from 'lru-cache';
import { config } from './config';
import { ScrapedProfile } from './types';

export const profileCache = new LRUCache<string, ScrapedProfile>({
  max: config.CACHE_MAX,
  ttl: config.CACHE_TTL_S * 1000,
});

export type NegativeReason = 'WAF' | 'NOT_FOUND';

export const negativeCache = new LRUCache<string, NegativeReason>({
  max: config.CACHE_MAX,
  ttl: config.NEGATIVE_CACHE_TTL_S * 1000,
});
