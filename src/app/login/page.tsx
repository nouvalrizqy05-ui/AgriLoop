import LoginForm from "@/components/forms/LoginForm";
import Link from "next/link";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-2xl font-bold text-green-800">Masuk ke AgriLoop</h1>
      <LoginForm />
      <p className="text-sm text-gray-600">
        Belum punya akun?{" "}
        <Link href="/register" className="text-green-700 underline">
          Daftar di sini
        </Link>
      </p>
    </main>
  );
}
