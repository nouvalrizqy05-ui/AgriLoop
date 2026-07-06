# Bukti Empiris: Perbaikan Race Condition Stok

Dua script berdiri sendiri ini (BUKAN bagian dari aplikasi Next.js, tidak
butuh Prisma) dipakai untuk membuktikan secara empiris -- dengan PostgreSQL
sungguhan, bukan cuma penjelasan teoretis -- bahwa:

1. **`old-pattern.js`**: mereplikasi pola LAMA (baca stok, validasi di
   aplikasi, baru decrement) yang sebelumnya dipakai di `orders/route.ts`.
2. **`new-pattern.js`**: mereplikasi pola BARU (conditional
   `UPDATE ... WHERE quantity >= X`) yang sekarang dipakai di
   `orders/route.ts` lewat `tx.product.updateMany()`.

## Hasil Pengujian Nyata (dijalankan di sandbox pembuatan proyek ini)

Setup: PostgreSQL 16 lokal, tabel `product` dengan stok awal 10, dua
transaksi dijalankan BERSAMAAN (`Promise.all`), masing-masing meminta 8 unit.

| Pola | Stok Akhir | Status |
|---|---|---|
| Lama (baca-lalu-tulis) | **-6** | BUG: stok negatif, kedua transaksi "lolos validasi" |
| Baru (conditional UPDATE) | **2** (konsisten di 3x percobaan berulang) | BENAR: hanya 1 transaksi berhasil, satunya ditolak database |

## Cara Menjalankan Ulang Sendiri

Butuh PostgreSQL lokal berjalan dan package `pg`:

```bash
cd proof-of-fix
npm init -y && npm install pg
# Sesuaikan connection string di dalam file .js dengan PostgreSQL-mu,
# buat tabel: CREATE TABLE product (id SERIAL PRIMARY KEY, name TEXT, quantity NUMERIC);
# lalu isi stok awal 10 sebelum tiap run.
node old-pattern.js
node new-pattern.js
```

**Catatan jujur**: ini menguji pola SQL-nya secara langsung lewat `pg`,
BUKAN lewat Prisma Client yang sesungguhnya dipakai aplikasi (karena Prisma
Client belum bisa di-generate di sandbox pembuatan proyek ini -- lihat
README utama §3). Pola SQL yang dihasilkan Prisma untuk
`updateMany({ where: { quantity: { gte: X } }, data: { quantity: { decrement: X } } })`
secara semantik setara dengan `UPDATE ... WHERE quantity >= X SET quantity = quantity - X`
yang diuji di sini, tapi kesetaraan ini **belum diverifikasi langsung**
lewat Prisma Client sungguhan. Rekomendasi: jalankan ulang tes serupa
setelah `prisma generate` berhasil di mesinmu, langsung lewat endpoint
`/api/orders`, untuk konfirmasi penuh end-to-end.
