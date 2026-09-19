import { env } from 'cloudflare:workers';
import { listRecentResults } from '@/lib/server/api';

export function GET(request: Request) {
  return listRecentResults(request, env);
}
