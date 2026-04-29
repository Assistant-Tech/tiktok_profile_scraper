import { z } from 'zod';

const envSchema = z.object({
  PORT: z.coerce.number().int().positive().default(8080),
  HOST: z.string().default('0.0.0.0'),
  LOG_LEVEL: z.enum(['fatal', 'error', 'warn', 'info', 'debug', 'trace', 'silent']).default('info'),
  SCRAPER_API_TOKEN: z.string().optional(),
  CACHE_TTL_S: z.coerce.number().int().positive().default(3600),
  NEGATIVE_CACHE_TTL_S: z.coerce.number().int().positive().default(300),
  CACHE_MAX: z.coerce.number().int().positive().default(5000),
  REQUEST_TIMEOUT_MS: z.coerce.number().int().positive().default(45_000),
  NAV_TIMEOUT_MS: z.coerce.number().int().positive().default(20_000),
  MAX_CONCURRENCY: z.coerce.number().int().positive().default(2),
  HEADLESS: z
    .enum(['true', 'false'])
    .default('true')
    .transform((v) => v === 'true'),
  PROXY_URL: z.string().optional(),
  USER_AGENT: z
    .string()
    .default(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    ),
  EXECUTABLE_PATH: z.string().optional(),
  PYTHON_BIN: z.string().default('python/.venv/bin/python'),
  PYTHON_SCRAPER_PATH: z.string().default('python/scraper.py'),
  PYTHON_WORKER_PATH: z.string().default('python/worker.py'),
});

export type Config = z.infer<typeof envSchema>;

export const config: Config = envSchema.parse(process.env);
