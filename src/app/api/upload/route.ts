import { NextResponse } from "next/server";
import { getServerSession } from "next-auth";
import { writeFile, mkdir } from "fs/promises";
import path from "path";
import { randomUUID } from "crypto";
import { authOptions } from "@/lib/auth";

// PERINGATAN ARSITEKTUR -- BACA SEBELUM DEPLOY KE VERCEL:
// Endpoint ini menyimpan file ke filesystem lokal (public/uploads).
// Ini BEKERJA untuk development lokal dan hosting single-instance
// (Railway/Render/VPS) yang punya disk persisten. Ini TIDAK BEKERJA
// di Vercel atau hosting serverless lain -- filesystem di sana bersifat
// read-only/ephemeral per-invocation, file yang ditulis akan HILANG atau
// gagal ditulis sama sekali di production.
//
// Ini bukan asumsi, ini keterbatasan yang harus divalidasi manual (lihat
// dokumen VERIFIKASI-MANUAL.md poin upload) sebelum dianggap "selesai".
// Untuk production sungguhan, ganti implementasi ini dengan object
// storage (Supabase Storage / Cloudinary / S3) -- effort tambahan yang
// SENGAJA tidak dikerjakan di sini karena butuh kredensial pihak ketiga
// yang tidak tersedia saat kode ini ditulis.
const MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024; // 5MB
const ALLOWED_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"];

export async function POST(req: Request) {
  const session = await getServerSession(authOptions);
  if (!session || session.user.role !== "PETANI") {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const formData = await req.formData().catch(() => null);
  const file = formData?.get("file");

  if (!file || !(file instanceof Blob)) {
    return NextResponse.json({ error: "File tidak ditemukan pada request" }, { status: 400 });
  }

  if (!ALLOWED_MIME_TYPES.includes(file.type)) {
    return NextResponse.json(
      { error: `Tipe file tidak didukung. Gunakan: ${ALLOWED_MIME_TYPES.join(", ")}` },
      { status: 400 }
    );
  }

  if (file.size > MAX_FILE_SIZE_BYTES) {
    return NextResponse.json({ error: "Ukuran file maksimal 5MB" }, { status: 400 });
  }

  const extension = file.type.split("/")[1];
  const filename = `${randomUUID()}.${extension}`;
  const uploadDir = path.join(process.cwd(), "public", "uploads");

  try {
    await mkdir(uploadDir, { recursive: true });
    const buffer = Buffer.from(await file.arrayBuffer());
    await writeFile(path.join(uploadDir, filename), buffer);
  } catch {
    return NextResponse.json(
      { error: "Gagal menyimpan file di server (kemungkinan filesystem read-only, cek target hosting)" },
      { status: 500 }
    );
  }

  return NextResponse.json({ photoUrl: `/uploads/${filename}` }, { status: 201 });
}
