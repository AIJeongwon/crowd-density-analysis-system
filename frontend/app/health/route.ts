import { env } from 'cloudflare:workers';
import { health } from '@/lib/server/api';

export function GET() {
  return health(env);
}
