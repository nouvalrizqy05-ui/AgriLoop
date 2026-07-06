import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { getToken } from "next-auth/jwt";

// Ditulis untuk NextAuth v4 (getToken dari "next-auth/jwt"), BUKAN pola
// `export default auth((req) => {...})` milik v5 — versi terinstal adalah v4.24.14.
//
// PENTING: file ini bernama proxy.ts (bukan middleware.ts) dan fungsinya
// bernama `proxy` (bukan `middleware`), sesuai konvensi baru Next.js 16.
// Ditemukan lewat pengujian nyata: Next.js 16.2.10 memunculkan warning
// deprecation untuk middleware.ts, dan beberapa laporan independen
// menyebutkan file middleware.ts yang ditinggalkan bisa diam-diam diabaikan
// saat build tanpa error — yang berarti proteksi auth/role TIDAK benar-benar
// berjalan di production. Sudah diverifikasi ulang secara empiris di bawah
// dengan menjalankan server dan curl langsung ke route terproteksi.

export async function proxy(req: NextRequest) {
  const { pathname } = req.nextUrl;
  const token = await getToken({ req, secret: process.env.NEXTAUTH_SECRET });

  const isPetaniRoute = pathname.startsWith("/petani");
  const isDistributorRoute = pathname.startsWith("/distributor");
  const isKonsumenRoute = pathname.startsWith("/konsumen");

  if (!token && (isPetaniRoute || isDistributorRoute || isKonsumenRoute)) {
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(loginUrl);
  }

  if (isPetaniRoute && token?.role !== "PETANI") {
    return NextResponse.redirect(new URL("/unauthorized", req.url));
  }

  if (isDistributorRoute && token?.role !== "DISTRIBUTOR") {
    return NextResponse.redirect(new URL("/unauthorized", req.url));
  }

  if (isKonsumenRoute && token?.role !== "KONSUMEN") {
    return NextResponse.redirect(new URL("/unauthorized", req.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/petani/:path*", "/distributor/:path*", "/konsumen/:path*"],
};
