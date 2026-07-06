import type { NextAuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";
import { compare } from "bcryptjs";
import { prisma } from "@/lib/db";
import { checkRateLimit } from "@/lib/rate-limit";

// PENTING: ditulis untuk NextAuth v4.24.14 (versi yang benar-benar ter-install,
// diverifikasi lewat `npm list next-auth`). Jangan pakai pola v5/Auth.js
// (mis. `export const { handlers, auth } = NextAuth(...)`) dengan versi ini —
// akan error karena API-nya berbeda.

export const authOptions: NextAuthOptions = {
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  secret: process.env.NEXTAUTH_SECRET,
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) return null;

        // Rate limit berbasis email (bukan IP) -- mencegah brute-force
        // terhadap satu akun spesifik terlepas dari berapa banyak IP yang
        // dipakai penyerang. Ini pelengkap, BUKAN pengganti, kebijakan
        // password yang kuat (sudah ada minimal 8 karakter di validations.ts).
        const rl = await checkRateLimit(`login:${credentials.email.toLowerCase()}`, {
          limit: 5,
          windowMs: 15 * 60_000,
        });
        if (!rl.allowed) {
          // NextAuth v4 tidak punya cara elegan mengembalikan status 429
          // dari authorize() -- return null (yang berarti "kredensial
          // salah") adalah satu-satunya opsi tanpa modifikasi lebih dalam.
          // Ini KETERBATASAN YANG DIKETAHUI: pesan error ke user akan sama
          // seperti password salah biasa, bukan pesan "coba lagi nanti"
          // yang lebih jelas. Trade-off yang diterima untuk MVP.
          return null;
        }

        const user = await prisma.user.findUnique({
          where: { email: credentials.email },
        });
        if (!user) return null;

        const valid = await compare(credentials.password, user.passwordHash);
        if (!valid) return null;

        return {
          id: user.id,
          name: user.name,
          email: user.email,
          role: user.role,
        };
      },
    }),
  ],
  callbacks: {
    async jwt({ token, user }) {
      if (user) {
        token.role = (user as { role: string }).role;
        token.id = user.id as string;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.id = token.id as string;
        session.user.role = token.role as string;
      }
      return session;
    },
  },
};
