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
const ML_REQUEST_TIMEOUT_MS = 20_000; // > cold start 5-15 detik yang diakui README PanenCerdas, tapi tetap terbatas

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  if (!ML_SERVICE_URL) {
    // Fail eksplisit, BUKAN diam-diam fallback ke angka dummy. Kalau
    // layanan ML belum di-deploy/di-set, pengguna harus tahu fitur ini
    // sedang tidak tersedia -- bukan diberi angka yang seolah-olah nyata.
    return NextResponse.json(
      { error: "Layanan prediksi belum dikonfigurasi (ML_SERVICE_URL kosong)" },
      { status: 503 }
    );
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
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
      return NextResponse.json(
        { error: "Layanan prediksi mengembalikan error", detail: data },
        { status: upstream.status }
      );
    }

    return NextResponse.json(data);
  } catch (error: unknown) {
    const isAbort = error instanceof Error && error.name === "AbortError";
    return NextResponse.json(
      {
        error: isAbort
          ? "Layanan prediksi tidak merespons dalam waktu wajar (kemungkinan cold-start di hosting tier gratis)"
          : "Gagal menghubungi layanan prediksi",
      },
      { status: 504 }
    );
  } finally {
    clearTimeout(timeout);
  }
}
