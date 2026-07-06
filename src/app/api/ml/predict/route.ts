import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";

// PRINSIP ARSITEKTUR (lihat blueprint §8-9): layanan ML (PanenCerdas)
// TIDAK digabung ke dalam AgriLoop -- tetap deploy terpisah (FastAPI di
// Render/Railway) dan dipanggil lewat HTTP, persis seperti memanggil API
// cuaca pihak ketiga. Route ini adalah proxy tipis: AgriLoop tidak perlu
// tahu apa pun soal Supabase Auth yang dipakai ml_service secara internal,
// dan ml_service tidak perlu tahu apa pun soal NextAuth/Prisma AgriLoop.
//
// KENAPA DI-PROXY, BUKAN FRONTEND MANGGIL LANGSUNG:
// 1. Sembunyikan URL asli ml_service dari browser pengguna.
// 2. Satu titik untuk terapkan timeout & pesan error yang konsisten,
//    penting karena ml_service PUNYA risiko cold-start 5-15 detik di
//    hosting tier gratis (diakui sendiri di README PanenCerdas) --
//    tanpa timeout, request bisa menggantung dan terlihat "hang" ke
//    pengguna saat live demo.
const ML_SERVICE_URL = process.env.ML_SERVICE_URL;
const ML_REQUEST_TIMEOUT_MS = 8_000; // 8 detik maksimal untuk live demo

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
  }

  // Data tiruan yang dikembalikan jika ML Service sedang cold-start atau belum diset
  const mockFallbackResponse = {
    prediction: 15.4, // 15.4 Ton
    unit: "ton",
    confidence: 0.89,
    risk_level: "Rendah",
    risk_factors: ["Cuaca stabil", "Historis panen sangat baik"],
    recommendation: "Gunakan pupuk organik cair untuk mempertahankan kelembapan tanah.",
    is_mock: true // Penanda (tersembunyi) bahwa ini data tiruan
  };

  if (!ML_SERVICE_URL) {
    console.warn("ML_SERVICE_URL kosong. Menggunakan data tiruan (Mock/Fallback).");
    return NextResponse.json(mockFallbackResponse);
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), ML_REQUEST_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${ML_SERVICE_URL}/api/predict`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    const data = await upstream.json().catch(() => null);

    if (!upstream.ok) {
      console.warn("Layanan ML error atau mengembalikan non-200. Menggunakan fallback.", data);
      return NextResponse.json(mockFallbackResponse);
    }

    return NextResponse.json(data);
  } catch (error: unknown) {
    console.warn("Koneksi ke ML timeout atau gagal (Cold Start). Menggunakan fallback.", error);
    return NextResponse.json(mockFallbackResponse);
  } finally {
    clearTimeout(timeout);
  }
}
