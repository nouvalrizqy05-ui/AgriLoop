import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { checkRateLimit, getClientIdentifier } from "@/lib/rate-limit";

export async function GET(req: Request) {
  const identifier = getClientIdentifier(req);
  const rl = checkRateLimit(`price-history:get:${identifier}`, { limit: 60, windowMs: 60_000 });
  if (!rl.allowed) {
    return NextResponse.json(
      { error: "Terlalu banyak permintaan. Coba lagi sebentar lagi." },
      { status: 429, headers: { "Retry-After": String(Math.ceil((rl.resetAt - Date.now()) / 1000)) } }
    );
  }

  const { searchParams } = new URL(req.url);
  const category = searchParams.get("category");
  const region = searchParams.get("region");

  if (!category) {
    return NextResponse.json({ error: "Parameter 'category' wajib diisi" }, { status: 400 });
  }

  // PEMISAHAN PENTING: sebelumnya endpoint ini mencampur histori harga
  // listing petani (data internal platform) dengan harga acuan pasar
  // (kalau ada) dalam satu array tanpa pembeda. Ini menyesatkan --
  // pengguna tidak bisa tahu mana "harga yang pernah dipasang petani lain
  // di platform ini" vs "harga acuan pasar dari sumber eksternal".
  // Dipisah tegas jadi dua array, masing-masing dengan label sumbernya.
  const [listingHistory, reference] = await Promise.all([
    prisma.priceHistory.findMany({
      where: { category, isReference: false, ...(region ? { region } : {}) },
      orderBy: { recordedAt: "desc" },
      take: 50,
    }),
    prisma.priceHistory.findMany({
      where: { category, isReference: true, ...(region ? { region } : {}) },
      orderBy: { recordedAt: "desc" },
      take: 10,
    }),
  ]);

  return NextResponse.json({
    // Histori harga dari listing produk riil di platform ini sendiri.
    // Berguna kalau sudah banyak petani terdaftar; kosong/tipis di awal.
    listingHistory: listingHistory.map((h) => ({ ...h, price: Number(h.price) })),
    // Harga acuan statis, diisi manual dari sumber publik yang tercatat
    // di field `source` masing-masing baris. BUKAN feed real-time --
    // ketersediaan API terbuka PIHPS/Panel Harga Badan Pangan Nasional
    // belum diverifikasi, jadi ini SENGAJA tidak diklaim live.
    reference: reference.map((h) => ({ ...h, price: Number(h.price) })),
  });
}
