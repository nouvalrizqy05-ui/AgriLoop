import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

const redisUrl = process.env.UPSTASH_REDIS_REST_URL;
const redisToken = process.env.UPSTASH_REDIS_REST_TOKEN;

// Buat instance Redis jika env terkonfigurasi
const redis = redisUrl && redisToken ? new Redis({
  url: redisUrl,
  token: redisToken,
}) : null;

// Cache untuk menyimpan instance ratelimit agar tidak perlu recreate setiap saat
const ratelimitCache = new Map<string, Ratelimit>();

interface RateLimitOptions {
  limit: number;
  windowMs: number;
}

export async function checkRateLimit(
  identifier: string,
  options: RateLimitOptions
): Promise<{ allowed: boolean; remaining: number; resetAt: number }> {
  // Jika Redis belum terkonfigurasi, kita bypass rate limit sementara (untuk development)
  // Untuk production, pastikan credentials ini diset dengan benar.
  if (!redis) {
    console.warn("Upstash Redis credentials are not set. Bypassing rate limit.");
    return { allowed: true, remaining: options.limit, resetAt: Date.now() + options.windowMs };
  }

  // Hitung window dalam detik/menit agar sesuai format Upstash
  const windowSecs = Math.max(1, Math.floor(options.windowMs / 1000));
  const cacheKey = `${options.limit}:${windowSecs}s`;

  let ratelimit = ratelimitCache.get(cacheKey);
  if (!ratelimit) {
    ratelimit = new Ratelimit({
      redis: redis,
      limiter: Ratelimit.slidingWindow(options.limit, `${windowSecs} s`),
      analytics: true,
      prefix: "@upstash/ratelimit",
    });
    ratelimitCache.set(cacheKey, ratelimit);
  }

  const { success, limit, reset, remaining } = await ratelimit.limit(identifier);

  return {
    allowed: success,
    remaining: remaining,
    resetAt: reset,
  };
}

export function getClientIdentifier(req: Request): string {
  // x-forwarded-for bisa dipalsukan oleh client, tapi di belakang reverse
  // proxy platform hosting (Vercel/Railway) header ini biasanya di-set ulang
  // oleh proxy itu sendiri sehingga cukup dipercaya untuk kasus demo ini.
  const forwarded = req.headers.get("x-forwarded-for");
  return forwarded?.split(",")[0].trim() || "unknown";
}
