"use client";
/* eslint-disable react-hooks/set-state-in-effect --
   Rule eksperimental terbaru mendorong data-fetching lewat SWR/React Query
   alih-alih fetch-on-mount di useEffect. Untuk MVP Sprint 1 ini pola
   fetch-on-mount tetap dipertahankan karena paling sederhana dan valid
   secara fungsional; migrasi ke SWR direkomendasikan untuk versi lanjutan,
   BUKAN diabaikan tanpa alasan. */

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
}

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Menunggu Konfirmasi",
  CONFIRMED: "Dikonfirmasi",
  COMPLETED: "Selesai",
  CANCELLED: "Dibatalkan",
};

// Transisi status yang diizinkan dari tiap status saat ini. COMPLETED dan
// CANCELLED adalah terminal state -- tidak ada opsi lanjutan (harus cocok
// dengan guard yang sama persis di server, src/app/api/orders/[id]/route.ts).
const NEXT_STATUS_OPTIONS: Record<string, string[]> = {
  PENDING: ["CONFIRMED", "CANCELLED"],
  CONFIRMED: ["COMPLETED", "CANCELLED"],
  COMPLETED: [],
  CANCELLED: [],
};

export default function DistributorDashboardPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [allCategories, setAllCategories] = useState<string[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [cart, setCart] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const productsUrl = categoryFilter
      ? `/api/products?category=${encodeURIComponent(categoryFilter)}`
      : "/api/products";

    // Fetch daftar kategori dari listing TIDAK terfilter secara terpisah --
    // kalau dropdown kategori ikut memakai hasil yang sudah difilter, opsi
    // kategori lain akan hilang begitu satu kategori dipilih (bug yang
    // sempat saya tulis sendiri sebelum sempat dites, dikoreksi di sini).
    const [productsRes, allProductsRes, ordersRes] = await Promise.all([
      fetch(productsUrl),
      fetch("/api/products"),
      fetch("/api/orders"),
    ]);
    setProducts(await productsRes.json());
    const allProducts: Product[] = await allProductsRes.json();
    setAllCategories(Array.from(new Set(allProducts.map((p) => p.category))).sort());
    setOrders(await ordersRes.json());
    setLoading(false);
  }, [categoryFilter]);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  function setQty(productId: string, qty: number) {
    setCart((prev) => ({ ...prev, [productId]: qty }));
  }

  async function handleAggregateOrder() {
    setMessage("");
    const items = Object.entries(cart)
      .filter(([, qty]) => qty > 0)
      .map(([productId, quantity]) => ({ productId, quantity }));

    if (items.length === 0) {
      setMessage("Pilih kuantitas minimal satu produk sebelum membuat order.");
      return;
    }

    const res = await fetch("/api/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setMessage(data?.error?.formErrors?.[0] || data?.error || "Gagal membuat order.");
      return;
    }

    setMessage("Order agregasi berhasil dibuat.");
    setCart({});
    fetchAll();
  }

  async function updateStatus(orderId: string, status: string) {
    const res = await fetch(`/api/orders/${orderId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setMessage(data?.error || "Gagal memperbarui status order.");
      return;
    }
    setMessage(`Status order berhasil diubah menjadi ${STATUS_LABEL[status] ?? status}.`);
    fetchAll();
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">
            Agregasi Order — Pilih Produk dari Beberapa Petani
          </h2>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1 text-sm"
          >
            <option value="">Semua Kategori</option>
            {allCategories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>
        {loading ? (
          <p className="text-sm text-gray-500">Memuat...</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b text-gray-500">
                <th className="py-2">Nama</th>
                <th>Kategori</th>
                <th>Harga</th>
                <th>Tersedia</th>
                <th>Petani</th>
                <th>Qty Pesan</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id} className="border-b last:border-0">
                  <td className="py-2">{p.name}</td>
                  <td>{p.category}</td>
                  <td>Rp{p.price.toLocaleString("id-ID")}</td>
                  <td>
                    {p.quantity} {p.unit}
                  </td>
                  <td>{p.petani.name}</td>
                  <td>
                    <input
                      type="number"
                      min={0}
                      max={p.quantity}
                      className="w-20 rounded border px-2 py-1"
                      value={cart[p.id] || ""}
                      onChange={(e) => setQty(p.id, Number(e.target.value))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {message && <p className="mt-3 text-sm text-gray-700">{message}</p>}
        <Button className="mt-4" onClick={handleAggregateOrder}>
          Buat Order Agregasi
        </Button>
      </Card>

      <Card>
        <h2 className="mb-3 font-semibold text-gray-800">Riwayat Order</h2>
        {orders.length === 0 ? (
          <p className="text-sm text-gray-500">Belum ada order.</p>
        ) : (
          <div className="flex flex-col gap-4">
            {orders.map((o) => (
              <div key={o.id} className="rounded border p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium">
                    Order #{o.id.slice(0, 8)} — {STATUS_LABEL[o.status] ?? o.status}
                  </span>
                  <span className="text-sm text-gray-600">
                    Total: Rp{o.totalAggregated.toLocaleString("id-ID")}
                  </span>
                </div>
                <ul className="mb-2 text-xs text-gray-600">
                  {o.items.map((i) => (
                    <li key={i.id}>
                      {i.product.name} — {i.quantity} {i.product.unit} @ Rp
                      {i.priceAtOrder.toLocaleString("id-ID")}
                    </li>
                  ))}
                </ul>
                {NEXT_STATUS_OPTIONS[o.status]?.length > 0 && (
                  <div className="flex gap-2">
                    {NEXT_STATUS_OPTIONS[o.status].map((next) => (
                      <Button
                        key={next}
                        variant={next === "CANCELLED" ? "danger" : "primary"}
                        className="px-2 py-1 text-xs"
                        onClick={() => updateStatus(o.id, next)}
                      >
                        {STATUS_LABEL[next]}
                      </Button>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
