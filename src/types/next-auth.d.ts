import { DefaultSession, DefaultUser } from "next-auth";
import { DefaultJWT } from "next-auth/jwt";

// Module augmentation untuk NextAuth v4.24.14.
// Tanpa file ini, TypeScript error setiap kali mengakses session.user.role
// atau session.user.id, karena tipe default NextAuth tidak mengenal field ini.

declare module "next-auth" {
  interface User extends DefaultUser {
    role: string;
  }

  interface Session extends DefaultSession {
    user: {
      id: string;
      role: string;
    } & DefaultSession["user"];
  }
}

declare module "next-auth/jwt" {
  interface JWT extends DefaultJWT {
    id: string;
    role: string;
  }
}
