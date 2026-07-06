"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Input from "@/components/ui/Input";
import Button from "@/components/ui/Button";

export default function RegisterForm() {
  const router = useRouter();
  const [form, setForm] = useState({
    name: "",
    email: "",
    password: "",
    role: "PETANI",
    location: "",
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  function update(field: string, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    const res = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });

    setLoading(false);

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      setError(data?.error?.formErrors?.[0] || data?.error || "Registrasi gagal.");
      return;
    }

    router.push("/login");
  }

  return (
    <form onSubmit={handleSubmit} className="flex w-full max-w-sm flex-col gap-4">
      <Input label="Nama" value={form.name} onChange={(e) => update("name", e.target.value)} required />
      <Input
        label="Email"
        type="email"
        value={form.email}
        onChange={(e) => update("email", e.target.value)}
        required
      />
      <Input
        label="Password"
        type="password"
        value={form.password}
        onChange={(e) => update("password", e.target.value)}
        required
        minLength={8}
      />
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-gray-700">Peran</label>
        <select
          value={form.role}
          onChange={(e) => update("role", e.target.value)}
          className="rounded-md border border-gray-300 px-3 py-2 text-sm"
        >
          <option value="PETANI">Petani / Peternak</option>
          <option value="DISTRIBUTOR">Distributor (Pembeli B2B)</option>
          <option value="KONSUMEN">Konsumen (Pembeli B2C)</option>
        </select>
      </div>
      <Input
        label="Lokasi (opsional)"
        value={form.location}
        onChange={(e) => update("location", e.target.value)}
      />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <Button type="submit" disabled={loading}>
        {loading ? "Memproses..." : "Daftar"}
      </Button>
    </form>
  );
}
