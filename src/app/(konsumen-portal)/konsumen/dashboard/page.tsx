"use client";
/* eslint-disable react-hooks/set-state-in-effect --
   Pola fetch-on-mount dipertahankan konsisten dengan dashboard distributor
   (lihat komentar identik di sana) -- ini bukan oversight, ini konsistensi
   yang disengaja untuk MVP. */

import { useEffect, useState, useCallback } from "react";
import Card from "@/components/ui/Card";
import Button from "@/components/ui/Button";

interface Product {
  id: string;
  name: string;
  category: string;
  price: number;
  quantity: number;
  unit: string;
  petani: { name: string; location: string | null };
}

interface PickupPoint {
  id: string;
  name: string;
  kecamatan: string;
  address: string;
}

interface OrderItem {
  id: string;
  quantity: number;
  priceAtOrder: number;
  product: { name: string; unit: string };
}

interface Order {
  id: string;
  status: string;
  totalAggregated: number;
  createdAt: string;
  items: OrderItem[];
  pickupPoint: PickupPoint | null;
}

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Menunggu Konfirmasi",
  CONFIRMED: "Dikonfirmasi",
  COMPLETED: "Selesai",
  CANCELLED: "Dibatalkan",
};

// Batas kuantitas per item HANYA untuk tampilan (disiplin server-side yang
// sebenarnya ada di KONSUMEN_MAX_QTY_PER_ITEM, src/lib/validations.ts).
// Duplikasi angka ini di frontend SENGAJA -- kalau nanti angkanya diubah
// di validations.ts, pengecekan di sini juga harus diubah manual (belum
// ada mekanisme sharing constant antara client/server di skrip ini untuk
// menghindari bundling kode server ke client tanpa perlu).
const MAX_QTY_PER_ITEM = 50;

// Radius default sengaja hardcode "Indramayu" -- sesuai keputusan
// blueprint §6 (B2C dibatasi intra-kabupaten). Kalau nanti platform
// diekspansi ke kabupaten lain, ini harus jadi konfigurasi, bukan hardcode.
const DEFAULT_REGION = "Indramayu";

export default function KonsumenDashboardPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [pickupPoints, setPickupPoints] = useState<PickupPoint[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [cart, setCart] = useState<Record<string, number>>({});
  const [selectedPickupPoint, setSelectedPickupPoint] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const [productsRes, pickupRes, ordersRes] = await Promise.all([
      fetch(`/api/products?region=${encodeURIComponent(DEFAULT_REGION)}`),
      fetch("/api/pickup-points"),
      fetch("/api/orders"),
    ]);
    setProducts(await productsRes.json());
    setPickupPoints(await pickupRes.json());
    setOrders(await ordersRes.json());
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  function setQty(productId: string, qty: number, maxStock: number) {
    const capped = Math.min(qty, MAX_QTY_PER_ITEM, maxStock);
    setCart((prev) => ({ ...prev, [productId]: capped }));
  }

  async function handleCheckout() {
    setMessage("");

    if (!selectedPickupPoint) {
      setMessage("Pilih titik kumpul untuk pengambilan pesanan sebelum checkout.");
      return;
    }

    const items = Object.entries(cart)
      .filter(([, qty]) => qty > 0)
      .map(([productId, quantity]) => ({ productId, quantity }));

    if (items.length === 0) {
      setMessage("Pilih kuantitas minimal satu produk sebelum checkout.");
      return;
    }

    // SIMULASI CHECKOUT: tidak ada payment gateway sungguhan di MVP ini.
    // Order langsung dibuat dengan status PENDING, seperti transaksi
    // "bayar di tempat saat pengambilan" -- ini didokumentasikan jujur,
    // bukan diklaim sebagai pembayaran online yang sudah terintegrasi.
    const res = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items, pickupPointId: selectedPickupPoint }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setMessage(data?.error?.formErrors?.[0] || data?.error || "Gagal membuat pesanan.");
      return;
    }

    setMessage("Pesanan berhasil dibuat. Ambil di titik kumpul yang dipilih setelah dikonfirmasi.");
    setCart({});
    setSelectedPickupPoint("");
    fetchAll();
  }

  async function cancelOrder(orderId: string) {
    const res = await fetch(`/api/orders/${orderId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "CANCELLED" }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(data?.error || "Gagal membatalkan pesanan.");
      return;
    }
    setMessage("Pesanan dibatalkan.");
    fetchAll();
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <h2 className="mb-1 font-semibold text-gray-800">
          Beli Langsung dari Petani — Wilayah {DEFAULT_REGION}
        </h2>
        <p className="mb-3 text-xs text-gray-500">
          Kuantitas per produk dibatasi maksimal {MAX_QTY_PER_ITEM} untuk kebutuhan rumah tangga. Untuk
          pembelian volume besar, gunakan akun Distributor.
        </p>
        {loading ? (
          <p className="text-sm text-gray-500">Memuat...</p>
        ) : products.length === 0 ? (
          <p className="text-sm text-gray-500">
            Belum ada produk dari petani di wilayah {DEFAULT_REGION} saat ini.
          </p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b text-gray-500">
                <th className="py-2">Nama</th>
                <th>Harga</th>
                <th>Tersedia</th>
                <th>Petani</th>
                <th>Qty Beli</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id} className="border-b last:border-0">
                  <td className="py-2">{p.name}</td>
                  <td>Rp{p.price.toLocaleString("id-ID")}</td>
                  <td>
                    {p.quantity} {p.unit}
                  </td>
                  <td>
                    {p.petani.name}
                    {p.petani.location ? ` (${p.petani.location})` : ""}
                  </td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      max={Math.min(MAX_QTY_PER_ITEM, p.quantity)}
                      className="w-20 rounded border px-2 py-1"
                      value={cart[p.id] || ""}
                      onChange={(e) => setQty(p.id, Number(e.target.value), p.quantity)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="mt-4 flex flex-col gap-1">
          <label className="text-sm font-medium text-gray-700">Titik Kumpul Pengambilan</label>
          <select
            value={selectedPickupPoint}
            onChange={(e) => setSelectedPickupPoint(e.target.value)}
            className="rounded border border-gray-300 px-2 py-2 text-sm"
          >
            <option value="">-- Pilih titik kumpul --</option>
            {pickupPoints.map((pp) => (
              <option key={pp.id} value={pp.id}>
                {pp.name} — {pp.kecamatan}
              </option>
            ))}
          </select>
          {pickupPoints.length === 0 && (
            <p className="text-xs text-amber-600">
              Belum ada titik kumpul terdaftar. Checkout tidak bisa dilakukan sampai tim menambahkan
              minimal satu titik kumpul lewat endpoint admin.
            </p>
          )}
        </div>

        {message && <p className="mt-3 text-sm text-gray-700">{message}</p>}
        <Button className="mt-4" onClick={handleCheckout}>
          Checkout (Simulasi — Bayar di Titik Kumpul)
        </Button>
      </Card>

      <Card>
        <h2 className="mb-3 font-semibold text-gray-800">Pesanan Saya</h2>
        {orders.length === 0 ? (
          <p className="text-sm text-gray-500">Belum ada pesanan.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {orders.map((o) => (
              <div key={o.id} className="rounded border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">
                    Pesanan #{o.id.slice(0, 8)} — {STATUS_LABEL[o.status] ?? o.status}
                  </span>
                  <span className="text-sm text-gray-600">
                    Total: Rp{o.totalAggregated.toLocaleString("id-ID")}
                  </span>
                </div>
                {o.pickupPoint && (
                  <p className="mb-2 text-xs text-gray-600">
                    Ambil di: {o.pickupPoint.name} — {o.pickupPoint.address}
                  </p>
                )}
                <ul className="mb-2 text-xs text-gray-600">
                  {o.items.map((i) => (
                    <li key={i.id}>
                      {i.product.name} — {i.quantity} {i.product.unit} @ Rp
                      {i.priceAtOrder.toLocaleString("id-ID")}
                    </li>
                  ))}
                </ul>
                {(o.status === "PENDING" || o.status === "CONFIRMED") && (
                  <Button
                    variant="danger"
                    className="px-2 py-1 text-xs"
                    onClick={() => cancelOrder(o.id)}
                  >
                    Batalkan Pesanan
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
