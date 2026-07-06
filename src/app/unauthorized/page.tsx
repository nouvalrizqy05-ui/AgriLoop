import Link from "next/link";

export default function UnauthorizedPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8 text-center">
      <h1 className="text-2xl font-bold text-red-700">Akses Ditolak</h1>
      <p className="text-gray-600">Kamu tidak memiliki izin untuk mengakses halaman ini.</p>
      <Link href="/" className="text-green-700 underline">
        Kembali ke Beranda
      </Link>
    </main>
  );
}
