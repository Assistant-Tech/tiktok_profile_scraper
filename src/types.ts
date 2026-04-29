export interface ScrapedProfile {
  username: string;
  name: string;
  avatar_url: string | null;
  bio: string | null;
  verified: boolean;
  follower_count: number | null;
  following_count: number | null;
  like_count: number | null;
  region: string | null;
}

export interface ProfileResponse {
  profile: ScrapedProfile;
  cached: boolean;
  elapsed_ms: number;
}

export interface ErrorResponse {
  error: string;
  detail: string;
}

export class ScrapeError extends Error {
  constructor(
    message: string,
    public readonly code: 'WAF_BLOCKED' | 'PROFILE_NOT_FOUND' | 'SCRAPE_ERROR' | 'TIMEOUT',
  ) {
    super(message);
    this.name = 'ScrapeError';
  }
}
