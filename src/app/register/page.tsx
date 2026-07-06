import RegisterForm from "@/components/forms/RegisterForm";
import Link from "next/link";

export default function RegisterPage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
      <h1 className="text-2xl font-bold text-green-800">Daftar AgriLoop</h1>
      <RegisterForm />
      <p className="text-sm text-gray-600">
        Sudah punya akun?{" "}
        <Link href="/login" className="text-green-700 underline">
          Masuk di sini
        </Link>
      </p>
    </main>
  );
}
