import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { pickupPointSchema } from "@/lib/validations";

// CATATAN DESAIN: tidak ada peran ADMIN formal di skema (UserRole cuma
// PETANI/DISTRIBUTOR/KONSUMEN) -- menambah peran admin penuh (dengan
// dashboard, dst) di luar scope MVP ini. Sebagai jalan pintas yang JUJUR
// didokumentasikan (bukan disembunyikan sebagai "sudah aman"): pembuatan
// titik kumpul dilindungi header rahasia sederhana yang dibaca dari env
// var, dikelola manual oleh tim lewat curl/Postman/skrip -- BUKAN lewat
// UI publik. Ini keputusan sadar untuk MVP, ditandai eksplisit sebagai
// gap di blueprint (§5A) supaya tidak dianggap fitur admin yang lengkap.
function isAuthorizedAdmin(req: Request): boolean {
  const secret = process.env.ADMIN_SETUP_SECRET;
  if (!secret) return false; // fail-closed: kalau env var tidak diset, endpoint POST selalu ditolak
  return req.headers.get("x-admin-secret") === secret;
}

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const kecamatan = searchParams.get("kecamatan");

  const pickupPoints = await prisma.pickupPoint.findMany({
    where: {
      isActive: true,
      ...(kecamatan ? { kecamatan: { contains: kecamatan, mode: "insensitive" } } : {}),
    },
    orderBy: { name: "asc" },
  });

  return NextResponse.json(pickupPoints);
}

export async function POST(req: Request) {
  if (!isAuthorizedAdmin(req)) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
  }

  const parsed = pickupPointSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  const pickupPoint = await prisma.pickupPoint.create({ data: parsed.data });
  return NextResponse.json(pickupPoint, { status: 201 });
}
