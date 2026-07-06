"use client";

import { SessionProvider } from "next-auth/react";
import { ReactNode } from "react";

// Wajib ada: tanpa SessionProvider ini, hook `useSession()` di client component
// manapun akan melempar error "useSession must be wrapped in a SessionProvider".

export default function Providers({ children }: { children: ReactNode }) {
  return <SessionProvider>{children}</SessionProvider>;
}
