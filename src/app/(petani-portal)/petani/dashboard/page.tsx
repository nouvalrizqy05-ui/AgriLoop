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
import Input from "@/components/ui/Input";
import FormTambahProduk from "@/components/forms/FormTambahProduk";
import HoloMap from "@/components/HoloMap";

interface Product {
  id: string;
  name: string;
  category: string;
  price: number;
  quantity: number;
  unit: string;
  status: string;
  petani: { name: string; location: string | null };
}

interface IncomingOrderItem {
  id: string;
  orderId: string;
  status: string;
  createdAt: string;
  productName: string;
  category: string;
  unit: string;
  quantity: number;
  priceAtOrder: number;
  subtotal: number;
}

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Menunggu Konfirmasi",
  CONFIRMED: "Dikonfirmasi",
  COMPLETED: "Selesai",
  CANCELLED: "Dibatalkan",
};

export default function PetaniDashboardPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [orders, setOrders] = useState<IncomingOrderItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [editQty, setEditQty] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  const fetchAll = useCallback(async () => {
    setLoading(true);
    const [productsRes, ordersRes] = await Promise.all([
      fetch("/api/products?mine=true"),
      fetch("/api/orders"),
    ]);
    try {
      const pText = await productsRes.text();
      const oText = await ordersRes.text();
      
      if (productsRes.ok && pText) {
        setProducts(JSON.parse(pText));
      } else {
        console.error("Products fetch failed or empty:", productsRes.status, pText);
        setProducts([]);
      }
      
      if (ordersRes.ok && oText) {
        setOrders(JSON.parse(oText));
      } else {
        console.error("Orders fetch failed or empty:", ordersRes.status, oText);
        setOrders([]);
      }
    } catch (err) {
      console.error("JSON Parse Error:", err);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  function startEdit(p: Product) {
    setEditingId(p.id);
    setEditPrice(String(p.price));
    setEditQty(String(p.quantity));
    setActionMessage("");
  }

  async function saveEdit(id: string) {
    const res = await fetch(`/api/products/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ price: Number(editPrice), quantity: Number(editQty) }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setActionMessage(data?.error?.formErrors?.[0] || data?.error || "Gagal memperbarui produk.");
      return;
    }
    setEditingId(null);
    fetchAll();
  }

  async function handleDelete(id: string) {
    if (!confirm("Yakin ingin menghapus/menonaktifkan produk ini?")) return;
    const res = await fetch(`/api/products/${id}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      setActionMessage(data?.error || "Gagal menghapus produk.");
      return;
    }
    setActionMessage(data.message || "Produk berhasil diperbarui.");
    fetchAll();
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-3">
        <div className="flex flex-col md:flex-row justify-between items-start md:items-center mb-4">
          <div>
            <h2 className="font-semibold text-emerald-800">Pemantauan Lahan (IoT Sensor)</h2>
            <p className="text-sm text-gray-500">Live feed data kelembapan & curah hujan lahan Desa Rajasinga</p>
          </div>
          <div className="mt-2 md:mt-0 flex gap-4 text-sm font-mono">
            <div className="bg-emerald-50 px-3 py-1 rounded text-emerald-700 border border-emerald-200">
              💧 Kelembapan: <span className="font-bold">68%</span>
            </div>
            <div className="bg-blue-50 px-3 py-1 rounded text-blue-700 border border-blue-200">
              🌧️ Curah Hujan: <span className="font-bold">12 mm</span>
            </div>
          </div>
        </div>
        <HoloMap lat={-6.5256} lng={108.0674} locationName="Lahan Pilot - Desa Rajasinga" />
      </Card>

      <Card className="lg:col-span-1">
        <h2 className="mb-3 font-semibold text-gray-800">Tambah Produk</h2>
        <FormTambahProduk onCreated={fetchAll} />
      </Card>

      <Card className="lg:col-span-2">
        <h2 className="mb-3 font-semibold text-gray-800">Produk Saya</h2>
        {actionMessage && <p className="mb-2 text-sm text-gray-700">{actionMessage}</p>}
        {loading ? (
          <p className="text-sm text-gray-500">Memuat...</p>
        ) : products.length === 0 ? (
          <p className="text-sm text-gray-500">Belum ada produk.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b text-gray-500">
                <th className="py-2">Nama</th>
                <th>Kategori</th>
                <th>Harga</th>
                <th>Kuantitas</th>
                <th>Status</th>
                <th>Aksi</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.id} className="border-b last:border-0">
                  <td className="py-2">{p.name}</td>
                  <td>{p.category}</td>
                  <td>
                    {editingId === p.id ? (
                      <Input
                        type="number"
                        value={editPrice}
                        onChange={(e) => setEditPrice(e.target.value)}
                        className="w-24"
                      />
                    ) : (
                      `Rp${p.price.toLocaleString("id-ID")}`
                    )}
                  </td>
                  <td>
                    {editingId === p.id ? (
                      <Input
                        type="number"
                        value={editQty}
                        onChange={(e) => setEditQty(e.target.value)}
                        className="w-20"
                      />
                    ) : (
                      `${p.quantity} ${p.unit}`
                    )}
                  </td>
                  <td>
                    {p.status === "DELISTED" ? (
                      <span className="text-xs text-red-600">Dinonaktifkan</span>
                    ) : (
                      <span className="text-xs text-green-700">Aktif</span>
                    )}
                  </td>
                  <td className="space-x-2">
                    {editingId === p.id ? (
                      <>
                        <Button variant="primary" onClick={() => saveEdit(p.id)} className="px-2 py-1 text-xs">
                          Simpan
                        </Button>
                        <Button variant="secondary" onClick={() => setEditingId(null)} className="px-2 py-1 text-xs">
                          Batal
                        </Button>
                      </>
                    ) : (
                      <>
                        {p.status !== "DELISTED" && (
                          <Button variant="secondary" onClick={() => startEdit(p)} className="px-2 py-1 text-xs">
                            Edit
                          </Button>
                        )}
                        {p.status !== "DELISTED" && (
                          <Button variant="danger" onClick={() => handleDelete(p.id)} className="px-2 py-1 text-xs">
                            Hapus
                          </Button>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card className="lg:col-span-3">
        <h2 className="mb-3 font-semibold text-gray-800">Pesanan Masuk</h2>
        {loading ? (
          <p className="text-sm text-gray-500">Memuat...</p>
        ) : orders.length === 0 ? (
          <p className="text-sm text-gray-500">Belum ada pesanan masuk untuk produk kamu.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b text-gray-500">
                <th className="py-2">Produk</th>
                <th>Kuantitas</th>
                <th>Harga Saat Order</th>
                <th>Subtotal</th>
                <th>Status</th>
                <th>Tanggal</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <tr key={o.id} className="border-b last:border-0">
                  <td className="py-2">{o.productName}</td>
                  <td>
                    {o.quantity} {o.unit}
                  </td>
                  <td>Rp{o.priceAtOrder.toLocaleString("id-ID")}</td>
                  <td>Rp{o.subtotal.toLocaleString("id-ID")}</td>
                  <td>{STATUS_LABEL[o.status] ?? o.status}</td>
                  <td>{new Date(o.createdAt).toLocaleDateString("id-ID")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
