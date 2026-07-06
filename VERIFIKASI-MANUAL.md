# Langkah Verifikasi Manual — WAJIB Dilakukan Sebelum Klaim "Production Ready"

Dokumen ini bukan formalitas. Setiap poin di sini adalah sesuatu yang **belum bisa saya verifikasi dari sandbox saya sendiri**, dan gagal di salah satu poin ini berarti klaim "siap deploy" tidak benar.

## 0. Yang SUDAH saya verifikasi langsung (bukan asumsi)

- ✅ `npm install` berjalan sukses tanpa error fatal (394 package terpasang, cuma warning deprecation biasa).
- ✅ Seluruh file baru/diedit **lolos parsing TypeScript** (`npx tsc --noEmit` mencapai tahap type-checking, bukan gagal di parsing — artinya tidak ada syntax error).

## 1. Yang TIDAK BISA saya verifikasi dari sandbox saya — dan KENAPA

**`npx prisma generate` gagal di sandbox saya** dengan error `403 Forbidden` saat mengambil binary engine dari `binaries.prisma.sh`. Domain ini **tidak ada di allowlist jaringan sandbox saya** — ini keterbatasan environment saya, BUKAN indikasi bug di kode. Prisma CLI publik dan seharusnya bisa diakses normal dari laptop/CI kamu.

**Konsekuensi:** Karena Prisma Client belum ter-generate ulang untuk skema baru (KONSUMEN, PickupPoint, OrderType, dst), `npx tsc --noEmit` menunjukkan 14 error bertipe "implicitly has an 'any' type" dan "Property 'Decimal' does not exist" — **ini SEMUA disebabkan oleh Prisma Client yang belum di-generate ulang**, bukan kesalahan logika kode. Pola error yang sama juga ada di kode original sebelum saya edit (sudah dicek).

**LANGKAH WAJIB PERTAMA yang harus kamu lakukan sendiri:**
```bash
npm install
npx prisma generate
npx tsc --noEmit    # harus 0 error setelah generate berhasil
```
Kalau setelah ini masih ada error TypeScript, itu baru benar-benar bug yang perlu diperbaiki — laporkan errornya kalau butuh bantuan lanjutan.

## 2. Database — belum pernah disentuh database sungguhan

Saya cuma mengedit `schema.prisma`, belum pernah menjalankan migrasi ke database nyata (tidak ada kredensial database di sandbox saya).

```bash
npx prisma migrate dev --name add-konsumen-pickup-point
```
Jalankan ini ke database PostgreSQL sungguhan (Supabase/Railway/lokal). Cek hasil migrasi tidak error, terutama karena ini **migrasi yang mengubah nama kolom** (`distributorId` → `buyerId`) — kalau ada data lama di database (kemungkinan kecil untuk Sprint 1, tapi cek dulu), migrasi rename butuh perhatian ekstra supaya data tidak hilang.

## 3. Seed data referensi — WAJIB diedit dulu, jangan langsung jalankan

`prisma/seed-reference-data.ts` berisi:
- Harga acuan HPP Rp6.500 dan contoh harga tengkulak Rp7.500 — ini angka nyata dari riset saya (sumber Republika, sudah dicatat di field `source`), aman dipakai.
- **Titik kumpul CONTOH dengan nama placeholder "Ganti-Kecamatan-Pilot"** — ini HARUS diganti dengan lokasi pilot riil sebelum demo. Kalau tidak diganti, saat live demo juri akan melihat nama placeholder yang jelas belum siap.

```bash
# EDIT dulu prisma/seed-reference-data.ts, ganti titik kumpul dengan lokasi asli, LALU:
npm run db:seed-mvp
```

## 4. `ML_SERVICE_URL` — layanan ML harus di-deploy dan dites TERPISAH

Folder `ml_service/` disalin apa adanya dari proyek riset internal tim. Saya **tidak mengubah isinya sama sekali** — resiko yang sudah diakui README aslinya (cold-start 5-15 detik, rate limit APPEEARS 1 permintaan/menit, checklist deploy yang belum lengkap) **masih berlaku dan belum saya selesaikan**.

Langkah wajib:
1. Deploy `ml_service/` ke Render/Railway sesuai instruksi di `ml_service/README.md`-nya sendiri.
2. Set environment variable `ML_SERVICE_URL` di project AgriLoop ke URL hasil deploy tersebut.
3. Test langsung endpoint `/api/ml/predict` dari AgriLoop (bukan cuma dari ml_service langsung) — proxy baru ini **belum pernah dites end-to-end** karena saya tidak punya cara menjalankan dua service sekaligus di sandbox saya.
4. **Sebelum demo:** jalankan `ml_service/scripts/prewarm_ndvi_cache.py` dan lakukan satu request "pemanasan" ke `/api/ml/predict` beberapa menit sebelum presentasi untuk menghindari cold-start terlihat di depan juri.

## 5. Upload foto — HARUS dites di platform hosting final, bukan cuma lokal

`/api/upload` menulis ke `public/uploads/` di filesystem. Ini **akan bekerja normal saat `npm run dev` di laptop**, tapi:
- **Kalau target deploy Vercel:** ini TIDAK akan bekerja di production (filesystem serverless bersifat ephemeral). Kamu HARUS ganti ke object storage (Supabase Storage paling mudah karena `ml_service` sudah pakai Supabase) sebelum submit, ATAU pindah target deploy ke Railway/Render (disk persisten).
- **Test manual wajib:** upload satu foto, refresh halaman, cek foto masih muncul. Kalau hosting-nya serverless dan foto hilang setelah beberapa saat, itu bukti masalah ini nyata terjadi.

## 6. Rate limiter — keputusan hosting menentukan apakah ini aman

Rate limiter tetap in-memory (TIDAK saya nonaktifkan, sesuai penjelasan saya sebelumnya soal risiko keamanan). Ini cocok untuk hosting single-instance. **Kalau target deploy Vercel (serverless multi-instance), rate limiter ini tidak benar-benar melindungi** karena setiap instance punya memori terpisah.

**Keputusan yang harus kamu ambil, bukan saya:** pilih Railway/Render (single instance, rate limiter jalan sesuai desain) ATAU pindah ke Vercel dan implementasikan rate limiter berbasis Redis/Upstash (effort tambahan yang belum saya kerjakan).

## 7. Environment variables — checklist lengkap untuk deploy

```
DATABASE_URL=            # dari provider PostgreSQL (Supabase/Railway/dll)
DIRECT_URL=              # sama seperti di atas, dibutuhkan Prisma untuk migrasi
NEXTAUTH_SECRET=         # generate baru dengan: openssl rand -base64 32 -- JANGAN pakai nilai contoh di .env sandbox saya
NEXTAUTH_URL=            # URL production sungguhan, bukan localhost
ML_SERVICE_URL=          # URL hasil deploy ml_service (lihat poin 4)
ADMIN_SETUP_SECRET=      # string rahasia bebas, untuk endpoint POST /api/pickup-points
```

## 8. Uji fungsional manual minimal sebelum submit

- [ ] Registrasi sebagai KONSUMEN berhasil, redirect ke `/konsumen/dashboard`
- [ ] Registrasi sebagai PETANI, tambah produk dengan foto asli dari HP — foto benar-benar tampil
- [ ] Registrasi sebagai DISTRIBUTOR, order agregasi tetap berjalan seperti sebelumnya (regresi — pastikan fitur lama tidak rusak oleh perubahan `buyerId`)
- [ ] Sebagai KONSUMEN: checkout dengan kuantitas di atas 50 → harus ditolak dengan pesan error yang jelas
- [ ] Sebagai KONSUMEN: checkout tanpa pilih titik kumpul → harus ditolak
- [ ] Panggil `/api/ml/predict` dari UI (bukan cuma curl langsung ke ml_service) — pastikan proxy benar-benar meneruskan data
- [ ] Coba akses `/distributor/dashboard` sambil login sebagai KONSUMEN → harus redirect ke `/unauthorized`

Tidak ada satu pun dari delapan poin ini yang bisa saya klaim "sudah pasti jalan" — semuanya butuh kamu jalankan sendiri di device/hosting kamu.
