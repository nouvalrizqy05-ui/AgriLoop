import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { Prisma } from "@prisma/client";
import { authOptions } from "@/lib/auth";
import { prisma } from "@/lib/db";
import { orderCreateSchema, KONSUMEN_MAX_QTY_PER_ITEM } from "@/lib/validations";

export async function GET() {
  const session = await getServerSession(authOptions);

  if (!session) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // GAP FUNGSIONAL YANG DITUTUP: sebelumnya endpoint ini HANYA bisa diakses
  // DISTRIBUTOR. Petani tidak punya cara sama sekali melihat produk mana
  // yang sudah dipesan -- padahal ini kebutuhan dasar marketplace dua sisi.
  //
  // Desain privasi yang disengaja untuk sisi petani: petani TIDAK diberi
  // akses ke objek Order penuh (yang berisi item dari petani lain dalam
  // satu agregasi order yang sama), hanya ke OrderItem miliknya sendiri.
  // Ini konsisten dengan prinsip isolasi data yang sudah ditegakkan di
  // GET /api/products?mine=true -- petani A tidak boleh melihat detail
  // transaksi petani B meski mereka kebetulan ada di order yang sama.
  if (session.user.role === "PETANI") {
    const items = await prisma.orderItem.findMany({
      where: { product: { petaniId: session.user.id } },
      include: {
        order: { select: { id: true, status: true, orderType: true, createdAt: true } },
        product: { select: { name: true, category: true, unit: true } },
      },
      orderBy: { order: { createdAt: "desc" } },
    });

    const safe = items.map((i) => ({
      id: i.id,
      orderId: i.order.id,
      status: i.order.status,
      orderType: i.order.orderType,
      createdAt: i.order.createdAt,
      productName: i.product.name,
      category: i.product.category,
      unit: i.product.unit,
      quantity: Number(i.quantity),
      priceAtOrder: Number(i.priceAtOrder),
      subtotal: Number(i.priceAtOrder) * Number(i.quantity),
    }));

    return NextResponse.json(safe);
  }

  // PERLUASAN B2C: sebelumnya guard ini cuma "role !== DISTRIBUTOR".
  // Sekarang KONSUMEN juga sah melihat order miliknya sendiri lewat
  // endpoint yang sama -- keduanya query dengan buyerId = session.user.id,
  // bedanya cuma orderType yang tersimpan di baris masing-masing.
  if (session.user.role !== "DISTRIBUTOR" && session.user.role !== "KONSUMEN") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const orders = await prisma.order.findMany({
    where: { buyerId: session.user.id },
    include: {
      items: { include: { product: true } },
      pickupPoint: true,
    },
    orderBy: { createdAt: "desc" },
  });

  const safe = orders.map((o) => ({
    ...o,
    totalAggregated: Number(o.totalAggregated),
    items: o.items.map((i) => ({
      ...i,
      quantity: Number(i.quantity),
      priceAtOrder: Number(i.priceAtOrder),
      product: { ...i.product, price: Number(i.product.price), quantity: Number(i.product.quantity) },
    })),
  }));

  return NextResponse.json(safe);
}

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);

  if (!session || (session.user.role !== "DISTRIBUTOR" && session.user.role !== "KONSUMEN")) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const isKonsumen = session.user.role === "KONSUMEN";

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body request tidak valid (bukan JSON)" }, { status: 400 });
  }

  const parsed = orderCreateSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
  }

  // ATURAN B2C YANG TIDAK BISA DIWAKILI ZOD (butuh tahu role sesi):
  // 1. KONSUMEN wajib pilih titik kumpul -- ini keputusan desain untuk
  //    menghindari kesalahan TaniHub (last-mile delivery per rumah tidak
  //    scalable). Tanpa pickupPointId, tidak jelas barang diambil di mana.
  // 2. KONSUMEN dibatasi kuantitas per item (lihat komentar konstanta di
  //    validations.ts) supaya B2C tidak jadi jalur belakang untuk
  //    pemesanan volume besar yang seharusnya lewat jalur DISTRIBUTOR.
  if (isKonsumen) {
    if (!parsed.data.pickupPointId) {
      return NextResponse.json(
        { error: "Konsumen wajib memilih titik kumpul (pickup point) untuk pengambilan pesanan" },
        { status: 400 }
      );
    }

    const pickupPoint = await prisma.pickupPoint.findUnique({
      where: { id: parsed.data.pickupPointId },
    });
    if (!pickupPoint || !pickupPoint.isActive) {
      return NextResponse.json({ error: "Titik kumpul tidak valid atau tidak aktif" }, { status: 400 });
    }

    const overLimit = parsed.data.items.find((i) => i.quantity > KONSUMEN_MAX_QTY_PER_ITEM);
    if (overLimit) {
      return NextResponse.json(
        {
          error: `Kuantitas per item untuk konsumen dibatasi maksimal ${KONSUMEN_MAX_QTY_PER_ITEM} per produk. Untuk pembelian volume besar, gunakan akun distributor.`,
        },
        { status: 400 }
      );
    }
  }

  try {
    // PERBAIKAN RACE CONDITION: validasi stok dan decrement digabung jadi
    // SATU operasi SQL atomic via updateMany dengan WHERE quantity >= X,
    // menutup celah TOCTOU antara "cek" dan "pakai". Diverifikasi empiris
    // sebelumnya lewat simulasi PostgreSQL langsung (lihat proof-of-fix/).
    // Logika ini TIDAK diubah sama sekali saat menambah jalur B2C --
    // KONSUMEN dan DISTRIBUTOR memakai proteksi atomic yang identik.
    const order = await prisma.$transaction(async (tx) => {
      const productIds = parsed.data.items.map((i) => i.productId);

      const products = await tx.product.findMany({
        where: { id: { in: productIds } },
      });

      if (products.length !== productIds.length) {
        throw new Error("NOT_FOUND");
      }

      // PERBAIKAN PRECISION UANG: total dijaga tetap dalam domain
      // Prisma.Decimal sepanjang perhitungan, konversi ke Number cuma
      // terjadi di titik serialisasi response, bukan di titik kalkulasi.
      let total = new Prisma.Decimal(0);
      for (const item of parsed.data.items) {
        const product = products.find((p) => p.id === item.productId)!;
        total = total.plus(product.price.times(item.quantity));
      }

      for (const item of parsed.data.items) {
        const result = await tx.product.updateMany({
          where: {
            id: item.productId,
            quantity: { gte: item.quantity },
          },
          data: { quantity: { decrement: item.quantity } },
        });

        if (result.count === 0) {
          const product = products.find((p) => p.id === item.productId)!;
          throw new Error(`INSUFFICIENT_STOCK:${product.name}`);
        }
      }

      return await tx.order.create({
        data: {
          buyerId: session.user.id,
          orderType: isKonsumen ? "B2C" : "B2B",
          pickupPointId: isKonsumen ? parsed.data.pickupPointId : null,
          totalAggregated: total,
          items: {
            create: parsed.data.items.map((item) => {
              const product = products.find((p) => p.id === item.productId)!;
              return {
                productId: item.productId,
                quantity: item.quantity,
                priceAtOrder: product.price,
              };
            }),
          },
        },
        include: { items: true, pickupPoint: true },
      });
    });

    // PERBAIKAN SERIALISASI DECIMAL (ditemukan lewat audit kode, dampak
    // nyata di frontend saat ini nihil karena respons sukses tidak dibaca
    // isinya -- tapi tetap inkonsistensi kontrak API yang perlu ditutup
    // sebelum ada fitur yang membaca field ini langsung).
    const safeOrder = {
      ...order,
      totalAggregated: Number(order.totalAggregated),
      items: order.items.map((i) => ({
        ...i,
        quantity: Number(i.quantity),
        priceAtOrder: Number(i.priceAtOrder),
      })),
    };

    return NextResponse.json(safeOrder, { status: 201 });
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "UNKNOWN";

    if (message === "NOT_FOUND") {
      return NextResponse.json({ error: "Beberapa produk tidak ditemukan" }, { status: 404 });
    }
    if (message.startsWith("INSUFFICIENT_STOCK")) {
      const productName = message.split(":")[1];
      return NextResponse.json(
        { error: `Stok tidak mencukupi untuk produk: ${productName}` },
        { status: 409 }
      );
    }
    return NextResponse.json({ error: "Terjadi kesalahan sistem saat memproses transaksi" }, { status: 500 });
  }
}
