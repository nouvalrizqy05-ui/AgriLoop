/**
 * Seed data referensi untuk demo/MVP -- BUKAN data live.
 *
 * PENTING UNTUK DIBACA SEBELUM MENJALANKAN:
 * 1. Harga acuan di bawah adalah angka yang DITEMUKAN lewat riset web
 *    (bukan API resmi -- ketersediaan endpoint terbuka PIHPS/Panel Harga
 *    Badan Pangan Nasional belum diverifikasi, lihat blueprint §12).
 *    Setiap baris punya field `source` yang WAJIB ditunjukkan di UI kalau
 *    harga ini ditampilkan ke pengguna/juri, supaya tidak diklaim sebagai
 *    data real-time yang sebenarnya tidak ada.
 * 2. Titik kumpul di bawah adalah CONTOH STRUKTUR DATA untuk demo, BUKAN
 *    lokasi yang sudah dikonfirmasi/disurvei nyata. Nama desa/kecamatan
 *    perlu diganti dengan lokasi yang benar-benar akan jadi pilot,
 *    dan alamat harus diverifikasi manual (siapa pemilik lokasi, jam
 *    operasional, dst -- lihat VERIFIKASI-MANUAL.md).
 *
 * Jalankan dengan: npm run db:seed-mvp
 */
import { PrismaClient } from "@prisma/client";

const prisma = new PrismaClient();

async function main() {
  // Harga Pembelian Pemerintah (HPP) untuk Gabah Kering Panen, sebagai
  // acuan minimum yang dijamin negara. [High confidence -- angka ini
  // dikonfirmasi berulang di laporan Republika Sept 2025 soal Indramayu]
  await prisma.priceHistory.create({
    data: {
      category: "Padi/Gabah",
      region: "Indramayu",
      price: 6500,
      isReference: true,
      source: "Harga Pembelian Pemerintah (HPP) Gabah Kering Panen -- dikutip Republika, 30 Sept 2025",
    },
  });

  // Contoh harga transaksi tengkulak yang PERNAH terjadi di lapangan --
  // ditampilkan sebagai ILUSTRASI RENTANG HARGA, BUKAN patokan tetap,
  // karena harga tengkulak berfluktuasi tergantung musim/permintaan
  // (lihat catatan kontradiksi data di blueprint §4 -- tengkulak PERNAH
  // membayar di atas HPP, bukan selalu di bawah).
  await prisma.priceHistory.create({
    data: {
      category: "Padi/Gabah",
      region: "Indramayu",
      price: 7500,
      isReference: true,
      source:
        "Contoh harga tengkulak Desa Rajasinga, Kec. Terisi, Sept 2025 (kondisi permintaan tinggi) -- Republika, 30 Sept 2025. BUKAN rata-rata tetap.",
    },
  });

  // ⚠️ GANTI dengan lokasi pilot yang sudah dikonfirmasi tim sebelum demo.
  // Nama kecamatan/desa di bawah ini CONTOH STRUKTUR, belum divalidasi.
  await prisma.pickupPoint.createMany({
    data: [
      {
        name: "Titik Kumpul Contoh -- Kantor Desa (GANTI SEBELUM DEMO)",
        kecamatan: "Ganti-Kecamatan-Pilot",
        desa: "Ganti-Desa-Pilot",
        address: "Alamat belum diverifikasi -- isi alamat riil sebelum demo",
        contactPhone: null,
        isActive: true,
      },
    ],
  });

  console.log("Seed data referensi selesai. INGAT: sebagian data di atas masih placeholder contoh.");
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
