import Link from "next/link";

export default function HomePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <h1 className="text-3xl font-bold text-green-800">AgriLoop</h1>
      <p className="max-w-md text-gray-600">
        Memutus rantai tengkulak, menyambung petani ke pasar. Platform commerce
        dan traceability untuk rantai pasok pertanian pedesaan.
      </p>
      <div className="flex gap-3">
        <Link
          href="/login"
          className="rounded-md bg-green-700 px-5 py-2 text-white hover:bg-green-800"
        >
          Masuk
        </Link>
        <Link
          href="/register"
          className="rounded-md border border-green-700 px-5 py-2 text-green-700 hover:bg-green-50"
        >
          Daftar
        </Link>
      </div>
    </main>
  );
}
