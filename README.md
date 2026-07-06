# AgriLoop — Sprint 1 (Revisi 3, "Selesai" untuk Scope Tier 1)

**Klaim yang TIDAK saya buat**: proyek ini bukan "bebas kelemahan" dan
bukan "tervalidasi kebutuhan pasar". Tidak ada software yang bebas
kelemahan, dan validasi pasar sungguhan butuh riset pengguna nyata dengan
petani/distributor UMKM yang belum pernah dilakukan di proses ini. Yang
bisa diklaim: gap fungsional struktural yang ditemukan lewat audit kode
langsung sudah ditutup, dua bug race condition (satu lama, satu yang
sempat saya perkenalkan sendiri di revisi ini) sudah diperbaiki dan
dibuktikan empiris, dan setiap keputusan desain didokumentasikan jujur.

---

## 1. PERINGATAN KEAMANAN DARI REVISI SEBELUMNYA

ZIP yang diaudit di revisi ini sempat membawa `.env` berisi kredensial
Supabase asli. **Person yang mengelola proyek ini sudah mengonfirmasi
kredensial telah dirotasi.** Kalau kamu meneruskan proyek ini ke orang
lain: JANGAN PERNAH sertakan `.env` asli dalam ZIP/commit apa pun --
hanya `.env.example`.

---

## 2. Gap Fungsional yang Ditutup di Revisi Ini

Ditemukan lewat audit langsung ke kode (`grep`), bukan asumsi:

| Gap | Bukti Sebelum Diperbaiki | Perbaikan |
|---|---|---|
| Petani tidak bisa lihat siapa yang order produknya | `GET /api/orders` hardcoded hanya untuk role DISTRIBUTOR | Ditambah cabang logika untuk PETANI, menampilkan HANYA `OrderItem` miliknya sendiri (bukan seluruh `Order`, untuk menjaga privasi lintas-petani dalam satu agregasi order) |
| Tidak ada CRUD individual produk | `grep "PUT\|DELETE" src/app/api/products/` = nihil | `PUT`/`DELETE /api/products/[id]`, dengan ownership check dan soft-delete (`status: "DELISTED"`) kalau produk sudah punya riwayat order (mencegah kerusakan data transaksi lama) |
| `OrderStatus` enum tidak pernah dipakai | `grep "OrderStatus\|CONFIRMED\|COMPLETED" src/` = nihil | `PATCH /api/orders/[id]` untuk transisi status, dengan stock restoration otomatis saat `CANCELLED` |
| Tidak ada filter kategori di listing distributor | UI cuma tabel tanpa filter | Dropdown filter kategori ditambahkan |

## 3. Bug yang Diperbaiki (Dikonfirmasi Lewat Kode, Sebagian Dibuktikan Empiris)

### 3.1 Serialisasi `Decimal` di `POST /api/orders` -- DIKONFIRMASI & DIPERBAIKI
`order` sebelumnya dikembalikan mentah dari `tx.order.create()`, membuat
`totalAggregated`/`priceAtOrder`/`quantity` ter-serialize sebagai STRING
(lewat `toJSON()` bawaan decimal.js), bukan number -- tidak konsisten
dengan `GET` yang sudah benar. **Dampak nyata saat ini nihil** (frontend
tidak membaca body respons sukses), tapi inkonsistensi kontrak API ini
akan jadi bug nyata begitu ada fitur yang membaca field ini langsung.
Diperbaiki dengan konversi eksplisit `Number()` sebelum serialisasi.

### 3.2 Race Condition Stok Saat Order Dibuat -- DIBUKTIKAN EMPIRIS (revisi sebelumnya)
Lihat `proof-of-fix/old-pattern.js` dan `new-pattern.js`. Pola lama
menghasilkan stok **-6** (bug), pola baru (conditional `updateMany`)
menghasilkan stok **2**, konsisten di pengulangan.

### 3.3 Race Condition BARU yang Saya Perkenalkan Sendiri di Endpoint Status Update -- DITEMUKAN & DIPERBAIKI SEBELUM SEMPAT JADI BUG PRODUKSI

Saat menambah `PATCH /api/orders/[id]`, guard "sudah terminal state?"
awalnya dicek SEBELUM transaksi dimulai (`if (existing.status ===
"COMPLETED" ...)`) -- pola TOCTOU yang PERSIS SAMA dengan bug 3.2 yang
sudah saya perbaiki sebelumnya di file lain. Dua request `PATCH`
pembatalan bersamaan ke order yang sama bisa lolos guard itu bersamaan,
mengakibatkan stok ter-restore DUA KALI untuk satu pembatalan.

**Ditemukan lewat self-review, bukan lewat testing eksternal** -- saya
mengecek kode baru saya sendiri terhadap standar yang sama yang sudah
ditegakkan sebelumnya, sebelum menyerahkannya sebagai "selesai".

**Perbaikan**: guard terminal-state dipindah ke dalam `$transaction`
sebagai `updateMany` kondisional (`WHERE status NOT IN
('COMPLETED','CANCELLED')`), analog persis dengan fix 3.2.

**Dibuktikan empiris** lewat `proof-of-fix/cancel-race.js`: dua permintaan
pembatalan bersamaan terhadap order senilai stok tersisa 2 (mewakili 10
awal - 8 dipesan) -- hasil: stok akhir tepat **10** (bukan 18 yang berarti
restore ganda), hanya satu dari dua request yang berhasil.

**Implikasi yang perlu diakui jujur**: kalau bug ini bisa lolos dari
proses saya sendiri yang SUDAH tahu persis pola bug ini dari pengalaman
sebelumnya, ini sinyal kuat bahwa **setiap endpoint baru yang mengubah
state bersama (stok, status) perlu di-review eksplisit untuk pola TOCTOU
yang sama** sebelum dianggap selesai -- bukan diasumsikan aman karena
"sudah pernah diperbaiki di tempat lain".

## 4. Keterbatasan yang Masih Terbuka -- Jujur, Bukan Ditutupi

1. **Prisma Client belum pernah ter-generate di sandbox pembuatan proyek
   ini** (blokir jaringan ke `binaries.prisma.sh`). 13 error TypeScript
   yang muncul di `tsc --noEmit` SEMUA berasal dari akar penyebab ini
   (diverifikasi satu per satu, termasuk error `Prisma.Decimal` yang
   sempat saya curigai sebagai bug terpisah -- ternyata API yang sama
   sekali valid, dikonfirmasi lewat dokumentasi resmi Prisma, cuma tidak
   ter-resolve karena client belum di-generate).
   **WAJIB**: jalankan `npx prisma generate && npx tsc --noEmit` di
   mesinmu sendiri, JANGAN percaya klaim "0 errors" dari laporan mana pun
   (termasuk dokumen ini) tanpa melihat output asli sendiri.
2. **Endpoint baru (`orders/[id]`, `products/[id]`) belum diuji
   end-to-end dengan Prisma Client sungguhan** -- logika SQL intinya sudah
   diuji terpisah lewat `proof-of-fix/`, tapi belum lewat jalur asli
   `fetch()` ke endpoint yang jalan di atas Prisma Client nyata.
3. **State machine status order disederhanakan** -- tidak memvalidasi
   urutan penuh (mis. tidak memaksa PENDING harus lewat CONFIRMED dulu
   sebelum COMPLETED), hanya mencegah perubahan dari state terminal.
   Cukup untuk MVP, bukan business rule lengkap.
4. **Filter kategori memicu 2x fetch ke `/api/products`** (satu untuk
   daftar kategori tak terfilter, satu untuk tabel terfilter) -- trade-off
   sengaja untuk kesederhanaan, bukan dioptimalkan jadi satu endpoint
   gabungan.
5. **Tidak ada pagination** di listing produk/order -- untuk skala demo
   kompetisi ini bukan masalah, tapi bukan solusi skala produksi nyata.
6. **Perubahan harga produk (`PUT`) tidak menulis entri baru ke
   `PriceHistory`** -- dashboard transparansi harga hanya merefleksikan
   harga saat produk pertama dibuat, bukan histori perubahan harga
   berikutnya. Keputusan sengaja untuk MVP, didokumentasikan sebagai
   keterbatasan yang diketahui, bukan diabaikan tanpa sadar.
7. Semua keterbatasan dari revisi sebelumnya yang belum tersentuh: tidak
   ada automated test, rate limiter in-memory (tidak cocok
   multi-instance), belum pernah dideploy ke Vercel/Railway, belum ada
   latihan sesi Q&A.

## 5. Setup

```bash
npm install
cp .env.example .env   # isi DATABASE_URL, DIRECT_URL, NEXTAUTH_SECRET
npx prisma generate
npx prisma migrate dev --name init
npx tsc --noEmit        # WAJIB 0 error sebelum lanjut, jangan asumsikan
npm run build           # WAJIB sukses sebelum anggap "selesai"
npm run dev
```

## 6. Struktur Endpoint (Lengkap per Revisi Ini)

```
POST   /api/register
GET    /api/auth/[...nextauth]  (NextAuth)
GET    /api/products            (?category=, ?mine=true)
POST   /api/products
PUT    /api/products/[id]       (baru — petani, ownership-checked)
DELETE /api/products/[id]       (baru — soft-delete kalau ada riwayat order)
GET    /api/orders              (bercabang: PETANI lihat item masuk, DISTRIBUTOR lihat order sendiri)
POST   /api/orders              (dengan proteksi race condition stok)
PATCH  /api/orders/[id]         (baru — transisi status, dengan stock restoration & proteksi race condition ganda)
GET    /api/price-history
```

## 7. Bukti Empiris (`proof-of-fix/`)

Tiga script berdiri sendiri (raw SQL via `pg`, tidak butuh Prisma Client)
membuktikan tiga klaim teknis dengan PostgreSQL sungguhan, bukan
penjelasan teoretis:

- `old-pattern.js` / `new-pattern.js`: race condition stok saat order dibuat
- `cancel-race.js`: race condition ganda saat pembatalan order (bug yang
  sempat saya perkenalkan sendiri, lihat §3.3)

Jalankan `npm install pg` di masing-masing untuk mengulang sendiri.
