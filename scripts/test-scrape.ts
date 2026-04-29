import { scrapeProfile } from '../src/scraper';

(async () => {
  const username = process.argv[2] || 'soorazz019';
  try {
    const r = await scrapeProfile(username);
    console.log(JSON.stringify(r, null, 2));
    process.exit(0);
  } catch (e) {
    console.error('ERR', (e as Error).message);
    process.exit(1);
  }
})();
