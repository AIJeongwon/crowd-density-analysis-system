// Extend the runtime declarations supplied by @cloudflare/workers-types.
declare namespace Cloudflare {
  interface Env {
    DB: D1Database;
    SENSOR_API_TOKEN?: string;
  }
}
