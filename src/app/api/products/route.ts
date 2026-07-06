import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { productSchema } from "@/lib/validations";
import { checkRateLimit, getClientIdentifier } from "@/lib/rate-limit";

export async function GET(req: Request) {
  const identifier = getClientIdentifier(req);
  const rl = checkRateLimit(`products:get:${identifier}`, { limit: 60, windowMs: 60_000 });
  if (!rl.allowed) {
    return NextResponse.json(
      { error: "Terlalu banyak permintaan. Coba lagi sebentar lagi." },
      { status: 429, headers: { "Retry-After": String(Math.ceil((rl.resetAt - Date.now()) / 1000)) } }
    );
  }

  const { searchParams } = new URL(req.url);
  const category = searchParams.get("category");
  const isMine = searchParams.get("mine") === "true";
  // FITUR BARU: filter lokasi. Kolom User.location sudah ada di skema
  // sejak awal tapi TIDAK PERNAH dipakai di query manapun -- ditemukan
  // lewat audit (`grep -rn "location" src/app/api` sebelumnya nihil hasil
  // query). Ini menutup gap itu: query publik sekarang bisa dipersempit
  // ke wilayah tertentu (mis. "Indramayu"), relevan untuk B2C yang
  // dibatasi radius intra-kabupaten -- lihat blueprint §6.
  // Pencocokan pakai `contains` (substring), BUKAN exact match, karena
  // location diisi bebas sebagai teks (bukan dropdown wilayah terstruktur)
  // -- ini keterbatasan yang disengaja untuk MVP, bukan bug.
  const region = searchParams.get("region");

  const whereClause: {
    category?: string;
    petaniId?: string;
    status?: { not: string };
    petani?: { location: { contains: string; mode: "insensitive" } };
  } = {};
  if (category) whereClause.category = category;
  if (region) whereClause.petani = { location: { contains: region, mode: "insensitive" } };

  if (isMine) {
    const session = await getServerSession(authOptions);
    if (!session || session.user.role !== "PETANI") {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }
    whereClause.petaniId = session.user.id;
    // Sengaja TIDAK exclude DELISTED di sini -- petani harus tetap bisa
    // melihat produknya sendiri yang sudah di-nonaktifkan, supaya tahu
    // statusnya, bukan hilang begitu saja dari dashboard mereka.
  } else {
    // Listing publik (dashboard distributor): produk yang di-soft-delete
    // (DELISTED, lihat DELETE /api/products/[id]) tidak boleh muncul di
    // sini -- kalau muncul, distributor bisa mencoba order produk yang
    // petaninya sudah anggap tidak tersedia lagi.
    whereClause.status = { not: "DELISTED" };
  }

  const products = await prisma.product.findMany({
    where: Object.keys(whereClause).length > 0 ? whereClause : undefined,
    include: { petani: { select: { name: true, location: true } } },
    orderBy: { createdAt: "desc" },
  });

  const safe = products.map((p) => ({
    ...p,
    price: Number(p.price),
    quantity: Number(p.quantity),
  }));

  return NextResponse.json(safe);
}

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);

  if (!session || session.user.role !== "PETANI") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
  }

  const parsed = productSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  const product = await prisma.product.create({
    data: {
      ...parsed.data,
      petaniId: session.user.id,
    },
  });

  await prisma.priceHistory.create({
    data: {
      productId: product.id,
      category: product.category,
      price: product.price,
    },
  });

  return NextResponse.json(
    { ...product, price: Number(product.price), quantity: Number(product.quantity) },
    { status: 201 }
  );
}
