// Rate limiter in-memory sederhana untuk endpoint publik.
//
// KETERBATASAN YANG PERLU DIPAHAMI (jangan diklaim lebih dari ini):
// - State disimpan di memori proses Node.js, BUKAN di Redis/database eksternal.
// - Ini berarti limiter ini HANYA benar di deployment single-instance
//   (mis. satu instance Railway/VPS). Kalau di-deploy ke platform serverless
//   dengan multiple instance (Vercel dengan banyak lambda paralel), setiap
//   instance punya counter sendiri-sendiri -- rate limit efektif jadi lebih
//   longgar dari yang terlihat di angka konfigurasi.
// - State hilang setiap kali server restart/redeploy.
// - Untuk produksi sungguhan, ganti dengan solusi terpusat seperti
//   Upstash Redis + @upstash/ratelimit. Ini SENGAJA tidak dipakai sekarang
//   karena menambah dependency infrastruktur (Redis) yang belum ada di
//   Tier 1, sesuai keputusan blueprint untuk tidak menambah kompleksitas
//   di luar yang benar-benar dibutuhkan MVP kompetisi.
//
// Untuk kebutuhan demo/live-judging kompetisi (single instance, traffic
// rendah-menengah), pendekatan ini cukup memadai sebagai lapisan proteksi
// dasar terhadap penyalahgunaan endpoint publik.

interface RateLimitEntry {
  count: number;
  resetAt: number;
}

const store = new Map<string, RateLimitEntry>();

interface RateLimitOptions {
  limit: number;
  windowMs: number;
}

export function checkRateLimit(
  identifier: string,
  options: RateLimitOptions
): { allowed: boolean; remaining: number; resetAt: number } {
  const now = Date.now();
  const entry = store.get(identifier);

  if (!entry || entry.resetAt < now) {
    const resetAt = now + options.windowMs;
    store.set(identifier, { count: 1, resetAt });
    return { allowed: true, remaining: options.limit - 1, resetAt };
  }

  if (entry.count >= options.limit) {
    return { allowed: false, remaining: 0, resetAt: entry.resetAt };
  }

  entry.count += 1;
  store.set(identifier, entry);
  return { allowed: true, remaining: options.limit - entry.count, resetAt: entry.resetAt };
}

export function getClientIdentifier(req: Request): string {
  // x-forwarded-for bisa dipalsukan oleh client, tapi di belakang reverse
  // proxy platform hosting (Vercel/Railway) header ini biasanya di-set ulang
  // oleh proxy itu sendiri sehingga cukup dipercaya untuk kasus demo ini.
  // Untuk keamanan produksi sungguhan, verifikasi platform hosting spesifik
  // mana yang dipakai dan header mana yang benar-benar bisa dipercaya.
  const forwarded = req.headers.get("x-forwarded-for");
  return forwarded?.split(",")[0].trim() || "unknown";
}

// Cleanup periodik supaya Map tidak membengkak tanpa batas di long-running process.
setInterval(() => {
  const now = Date.now();
  for (const [key, entry] of store.entries()) {
    if (entry.resetAt < now) store.delete(key);
  }
}, 60_000);
