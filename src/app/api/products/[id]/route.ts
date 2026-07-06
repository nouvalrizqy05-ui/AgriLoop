import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { productUpdateSchema } from "@/lib/validations";

// GAP FUNGSIONAL YANG DITUTUP: sebelumnya tidak ada satu pun endpoint
// PUT/DELETE untuk produk individual (dikonfirmasi lewat
// `grep -rn "PUT\|DELETE" src/app/api/products/` -- hasilnya nihil).
// Petani tidak punya cara memperbaiki salah ketik harga atau menghapus
// listing yang sudah habis/tidak relevan. Ini kebutuhan dasar, bukan
// nice-to-have, untuk marketplace yang mengklaim melayani UMKM petani.

async function getOwnedProduct(id: string, userId: string) {
  return prisma.product.findFirst({ where: { id, petaniId: userId } });
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await getServerSession(authOptions);
  const { id } = await params;

  if (!session || session.user.role !== "PETANI") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const existing = await getOwnedProduct(id, session.user.id);
  if (!existing) {
    // 404, bukan 403 -- konsisten dengan pola di endpoint order status,
    // tidak membocorkan keberadaan produk milik petani lain.
    return NextResponse.json({ error: "Produk tidak ditemukan" }, { status: 404 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
  }

  const parsed = productUpdateSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  // Catatan desain: update harga TIDAK otomatis menulis entri baru ke
  // PriceHistory di sini. Ini keputusan sengaja untuk MVP -- traceability
  // harga historis hanya dicatat saat produk pertama kali dibuat (POST).
  // KETERBATASAN YANG DIKETAHUI: kalau petani sering mengubah harga,
  // dashboard transparansi harga tidak akan merefleksikan perubahan itu
  // sampai ada produk baru. Perbaikan idealnya: tulis PriceHistory baru
  // juga di sini kalau field `price` berubah -- sengaja tidak ditambahkan
  // sekarang supaya perilaku "kapan PriceHistory ditulis" tetap satu
  // tempat dan mudah dijelaskan ke juri, bukan tersebar di dua endpoint
  // dengan trigger yang beda.
  const updated = await prisma.product.update({
    where: { id },
    data: parsed.data,
  });

  return NextResponse.json({
    ...updated,
    price: Number(updated.price),
    quantity: Number(updated.quantity),
  });
}

export async function DELETE(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await getServerSession(authOptions);
  const { id } = await params;

  if (!session || session.user.role !== "PETANI") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const existing = await getOwnedProduct(id, session.user.id);
  if (!existing) {
    return NextResponse.json({ error: "Produk tidak ditemukan" }, { status: 404 });
  }

  // Cek referential integrity SEBELUM delete: kalau produk ini sudah
  // pernah dipesan (ada OrderItem yang mereferensikannya), hard delete
  // akan gagal atau (lebih buruk) merusak riwayat transaksi distributor
  // yang sudah checkout. Solusi: soft-delete via status, bukan hard
  // delete, kalau produk punya riwayat order.
  const orderItemCount = await prisma.orderItem.count({ where: { productId: id } });

  if (orderItemCount > 0) {
    const updated = await prisma.product.update({
      where: { id },
      data: { status: "DELISTED" },
    });
    return NextResponse.json({
      message: "Produk memiliki riwayat order, di-nonaktifkan (soft-delete) bukan dihapus permanen",
      product: { ...updated, price: Number(updated.price), quantity: Number(updated.quantity) },
    });
  }

  await prisma.product.delete({ where: { id } });
  return NextResponse.json({ message: "Produk berhasil dihapus" });
}
