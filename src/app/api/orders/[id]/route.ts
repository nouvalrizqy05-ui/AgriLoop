import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { orderStatusUpdateSchema } from "@/lib/validations";

// GAP FUNGSIONAL YANG DITUTUP: schema.prisma sudah punya enum OrderStatus
// (PENDING/CONFIRMED/COMPLETED/CANCELLED) sejak awal, tapi TIDAK ADA satu
// pun endpoint yang bisa mengubahnya -- ditemukan lewat audit langsung
// (`grep -rn "OrderStatus\|CONFIRMED\|COMPLETED" src/` hasilnya nihil).
// Order selamanya macet di PENDING. Ini menutup gap itu.

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const session = await getServerSession(authOptions);
  const { id } = await params;

  if (!session || (session.user.role !== "DISTRIBUTOR" && session.user.role !== "KONSUMEN")) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
  }

  const parsed = orderStatusUpdateSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  const existing = await prisma.order.findUnique({
    where: { id },
    include: { items: true },
  });

  if (!existing) {
    return NextResponse.json({ error: "Order tidak ditemukan" }, { status: 404 });
  }

  if (existing.buyerId !== session.user.id) {
    return NextResponse.json({ error: "Order tidak ditemukan" }, { status: 404 });
  }

  // KONSUMEN hanya boleh membatalkan pesanan sendiri, TIDAK boleh
  // mengonfirmasi/menyelesaikan (itu wewenang operator titik kumpul di
  // dunia nyata -- belum ada peran/endpoint terpisah untuk itu di MVP ini,
  // ditandai sebagai gap terbuka, bukan diam-diam diizinkan penuh).
  if (session.user.role === "KONSUMEN" && parsed.data.status !== "CANCELLED") {
    return NextResponse.json(
      { error: "Konsumen hanya dapat membatalkan pesanan, bukan mengubah status lain" },
      { status: 403 }
    );
  }

  // Guard awal ini HANYA untuk memberi pesan error cepat & jelas di kasus
  // umum (tidak konkuren) -- BUKAN satu-satunya proteksi. Proteksi
  // sebenarnya ada di dalam $transaction di bawah lewat updateMany
  // kondisional, karena guard di sini sendirian rentan TOCTOU race
  // condition: dua request PATCH bersamaan ke order yang sama bisa
  // sama-sama lolos guard ini (keduanya baca status PENDING), lalu
  // KEDUANYA menjalankan transaksi, mengakibatkan stok ter-restore DUA
  // KALI untuk satu pembatalan. Pola ini persis sama dengan bug race
  // condition yang sudah diperbaiki sebelumnya di POST /api/orders --
  // ditemukan lewat self-review terhadap kode baru ini sendiri.
  if (existing.status === "COMPLETED" || existing.status === "CANCELLED") {
    return NextResponse.json(
      { error: `Order sudah berstatus ${existing.status} dan tidak bisa diubah lagi` },
      { status: 409 }
    );
  }

  const newStatus = parsed.data.status;

  try {
    const updated = await prisma.$transaction(async (tx) => {
      // INI PROTEKSI SEBENARNYA: updateMany dengan WHERE status masih
      // non-terminal, dicek ATOMIC bersamaan dengan penulisan status baru.
      // Kalau request lain sudah lebih dulu mengubah status jadi terminal
      // di antara guard di atas dan baris ini, updateMany akan meng-update
      // 0 baris, dan itu terdeteksi lalu transaksi dibatalkan SEBELUM
      // sempat restore stok -- stok tidak akan pernah ter-restore ganda.
      const statusUpdateResult = await tx.order.updateMany({
        where: {
          id,
          status: { notIn: ["COMPLETED", "CANCELLED"] },
        },
        data: { status: newStatus },
      });

      if (statusUpdateResult.count === 0) {
        throw new Error("ALREADY_TERMINAL");
      }

      if (newStatus === "CANCELLED") {
        for (const item of existing.items) {
          await tx.product.update({
            where: { id: item.productId },
            data: { quantity: { increment: item.quantity } },
          });
        }
      }

      return tx.order.findUniqueOrThrow({
        where: { id },
        include: { items: true },
      });
    });

    const safe = {
      ...updated,
      totalAggregated: Number(updated.totalAggregated),
      items: updated.items.map((i) => ({
        ...i,
        quantity: Number(i.quantity),
        priceAtOrder: Number(i.priceAtOrder),
      })),
    };

    return NextResponse.json(safe);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "UNKNOWN";

    if (message === "ALREADY_TERMINAL") {
      return NextResponse.json(
        { error: "Order sudah diubah statusnya oleh permintaan lain, coba muat ulang halaman" },
        { status: 409 }
      );
    }
    return NextResponse.json({ error: "Gagal memperbarui status order" }, { status: 500 });
  }
}
