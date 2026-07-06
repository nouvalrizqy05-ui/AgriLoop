import { z } from "zod";

export const registerSchema = z.object({
  name: z.string().min(2, "Nama minimal 2 karakter"),
  email: z.string().email("Email tidak valid"),
  password: z.string().min(8, "Password minimal 8 karakter"),
  role: z.enum(["PETANI", "DISTRIBUTOR", "KONSUMEN"]),
  location: z.string().optional(),
  phone: z.string().optional(),
});

// photoUrl sengaja TIDAK divalidasi sebagai .url() lagi -- endpoint upload
// lokal (/api/upload) mengembalikan path relatif ("/uploads/xxx.jpg"),
// bukan URL absolut. Ganti balik ke .url() kalau nanti pindah ke object
// storage (S3/Supabase Storage/Cloudinary) yang mengembalikan URL penuh.
// CATATAN JUJUR: upload lokal ini TIDAK bertahan di hosting serverless
// (Vercel) karena filesystem-nya ephemeral -- lihat komentar di
// api/upload/route.ts. Ini disengaja untuk MVP demo, BUKAN solusi final.
const photoUrlSchema = z.string().min(1).max(500);

export const productSchema = z.object({
  name: z.string().min(2),
  category: z.string().min(2),
  price: z.number().positive(),
  quantity: z.number().positive(),
  unit: z.string().min(1),
  photoUrl: photoUrlSchema.optional(),
});

export const productUpdateSchema = z
  .object({
    name: z.string().min(2).optional(),
    category: z.string().min(2).optional(),
    price: z.number().positive().optional(),
    quantity: z.number().min(0).optional(), // min(0), bukan positive() -- 0 = habis, bukan invalid
    unit: z.string().min(1).optional(),
    photoUrl: photoUrlSchema.optional(),
  })
  .refine((data) => Object.keys(data).length > 0, {
    message: "Minimal satu field harus diisi untuk update",
  });

export const orderCreateSchema = z.object({
  items: z
    .array(
      z.object({
        productId: z.string(),
        quantity: z.number().positive(),
      })
    )
    .min(1, "Minimal satu item dalam order"),
  // Wajib diisi kalau pemesan berperan KONSUMEN (dicek di route, bukan di
  // sini, karena Zod tidak tahu role sesi saat ini). DISTRIBUTOR abaikan
  // field ini -- order B2B tidak pakai titik kumpul.
  pickupPointId: z.string().optional(),
});

export const orderStatusUpdateSchema = z.object({
  status: z.enum(["CONFIRMED", "COMPLETED", "CANCELLED"]),
});

export const pickupPointSchema = z.object({
  name: z.string().min(2),
  kecamatan: z.string().min(2),
  desa: z.string().optional(),
  address: z.string().min(5),
  contactPhone: z.string().optional(),
});

// Batas kuantitas eceran untuk KONSUMEN (B2C). Angka 50 (kg/unit) adalah
// ASUMSI KERJA, belum dikalibrasi per komoditas -- untuk padi/gabah ini
// perlu divalidasi ulang terhadap kebutuhan rumah tangga riil sebelum
// dipakai di proposal sebagai angka final. Tujuannya cuma mencegah
// KONSUMEN memesan dalam volume yang sebenarnya B2B menyamar jadi B2C.
export const KONSUMEN_MAX_QTY_PER_ITEM = 50;

export type RegisterInput = z.infer<typeof registerSchema>;
export type ProductInput = z.infer<typeof productSchema>;
export type OrderCreateInput = z.infer<typeof orderCreateSchema>;
