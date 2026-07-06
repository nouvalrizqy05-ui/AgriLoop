"use client";

import { useState } from "react";
import Input from "@/components/ui/Input";
import Button from "@/components/ui/Button";

export default function FormTambahProduk({ onCreated }: { onCreated?: () => void }) {
  const [form, setForm] = useState({
    name: "",
    category: "",
    price: "",
    quantity: "",
    unit: "kg",
  });
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState("");

  function update(field: string, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    // UPLOAD FOTO SEBELUM SIMPAN PRODUK -- menggantikan field URL teks
    // yang sebelumnya mengharuskan petani punya link foto sendiri (tidak
    // realistis untuk target pengguna literasi digital rendah). Sekarang
    // ambil langsung dari galeri/kamera HP.
    //
    // CATATAN JUJUR yang HARUS dicek manual sebelum deploy ke Vercel:
    // endpoint /api/upload menulis ke filesystem lokal server -- ini
    // TIDAK bertahan di hosting serverless. Lihat komentar lengkap di
    // src/app/api/upload/route.ts dan VERIFIKASI-MANUAL.md.
    let photoUrl: string | undefined;
    if (photoFile) {
      setUploadStatus("Mengunggah foto...");
      const fd = new FormData();
      fd.append("file", photoFile);
      const uploadRes = await fetch("/api/upload", { method: "POST", body: fd });
      const uploadData = await uploadRes.json().catch(() => ({}));
      if (!uploadRes.ok) {
        setLoading(false);
        setUploadStatus("");
        setError(uploadData?.error || "Gagal mengunggah foto.");
        return;
      }
      photoUrl = uploadData.photoUrl;
      setUploadStatus("");
    }

    const res = await fetch("/api/products", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: form.name,
        category: form.category,
        price: Number(form.price),
        quantity: Number(form.quantity),
        unit: form.unit,
        photoUrl,
      }),
    });

    setLoading(false);

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setError(data?.error?.formErrors?.[0] || data?.error || "Gagal menambah produk.");
      return;
    }

    setForm({ name: "", category: "", price: "", quantity: "", unit: "kg" });
    setPhotoFile(null);
    onCreated?.();
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <Input label="Nama Produk" value={form.name} onChange={(e) => update("name", e.target.value)} required />
      <Input
        label="Kategori"
        value={form.category}
        onChange={(e) => update("category", e.target.value)}
        placeholder="mis. Gabah, Cabai, Ayam Potong"
        required
      />
      <div className="grid grid-cols-2 gap-3">
        <Input
          label="Harga (Rp)"
          type="number"
          value={form.price}
          onChange={(e) => update("price", e.target.value)}
          required
        />
        <Input
          label="Kuantitas"
          type="number"
          value={form.quantity}
          onChange={(e) => update("quantity", e.target.value)}
          required
        />
      </div>
      <Input label="Satuan" value={form.unit} onChange={(e) => update("unit", e.target.value)} required />
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-gray-700">Foto Produk (opsional, maks 5MB)</label>
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          onChange={(e) => setPhotoFile(e.target.files?.[0] ?? null)}
          className="text-sm"
        />
      </div>
      {uploadStatus && <p className="text-sm text-gray-500">{uploadStatus}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Button type="submit" disabled={loading}>
        {loading ? "Menyimpan..." : "Tambah Produk"}
      </Button>
    </form>
  );
}
