"use client";

import { signOut } from "next-auth/react";
import Link from "next/link";

export default function PetaniLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <nav className="flex items-center justify-between border-b bg-white px-6 py-4">
        <Link href="/petani/dashboard" className="font-bold text-green-800">
          AgriLoop · Petani
        </Link>
        <button
          onClick={() => signOut({ callbackUrl: "/" })}
          className="text-sm text-gray-600 hover:text-red-600"
        >
          Keluar
        </button>
      </nav>
      <main className="p-6">{children}</main>
    </div>
  );
}
