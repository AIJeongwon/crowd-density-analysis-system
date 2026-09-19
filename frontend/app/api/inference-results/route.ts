import { env } from 'cloudflare:workers';
import { postInferenceResult } from '@/lib/server/api';

export function POST(request: Request) {
  return postInferenceResult(request, env);
}
