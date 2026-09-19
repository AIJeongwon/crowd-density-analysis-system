import { env } from 'cloudflare:workers';
import { listLocationStatuses } from '@/lib/server/api';

export function GET(request: Request) {
  return listLocationStatuses(request, env);
}
