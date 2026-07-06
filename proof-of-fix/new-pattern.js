const { Client } = require("pg");

async function simulateNewPattern(delayMs) {
  const client = new Client({
    connectionString: "postgresql://postgres:testpass@localhost:5432/agriloop_test",
  });
  await client.connect();

  await client.query("BEGIN");
  const res = await client.query("SELECT quantity FROM product WHERE id = 1");
  const currentQty = Number(res.rows[0].quantity);

  const requested = 8;

  // Simulasikan delay yang sama seperti test pola lama, supaya perbandingannya adil.
  await new Promise((r) => setTimeout(r, delayMs));

  // INI PERBAIKANNYA: validasi dan decrement digabung jadi SATU statement SQL
  // atomic. Kondisi "quantity >= requested" dicek OLEH DATABASE tepat pada
  // saat UPDATE dieksekusi, bukan berdasarkan hasil baca sebelumnya yang
  // sudah basi (stale read). Ini setara dengan pola Prisma:
  //   tx.product.updateMany({ where: { id, quantity: { gte: requested } }, ... })
  const updateResult = await client.query(
    "UPDATE product SET quantity = quantity - $1 WHERE id = 1 AND quantity >= $1 RETURNING quantity",
    [requested]
  );

  const succeeded = (updateResult.rowCount ?? 0) > 0;

  await client.query("COMMIT");
  await client.end();

  return { succeeded, currentQtyAtCheck: currentQty };
}

async function main() {
  console.log("=== SIMULASI POLA BARU (conditional UPDATE ... WHERE quantity >= X) ===");
  console.log("Stok awal: 10. Dua transaksi bersamaan, masing-masing minta 8.\n");

  const [r1, r2] = await Promise.all([
    simulateNewPattern(50),
    simulateNewPattern(50),
  ]);

  console.log("Transaksi A - berhasil decrement?", r1.succeeded, "| stok terlihat saat baca awal:", r1.currentQtyAtCheck);
  console.log("Transaksi B - berhasil decrement?", r2.succeeded, "| stok terlihat saat baca awal:", r2.currentQtyAtCheck);

  const client = new Client({
    connectionString: "postgresql://postgres:testpass@localhost:5432/agriloop_test",
  });
  await client.connect();
  const final = await client.query("SELECT quantity FROM product WHERE id = 1");
  const finalQty = Number(final.rows[0].quantity);
  console.log("\nSTOK AKHIR DI DATABASE:", finalQty);

  if (finalQty < 0) {
    console.log("!! MASIH BUG: stok negatif walau pakai conditional update. Perlu investigasi ulang.\n");
  } else if (r1.succeeded && r2.succeeded) {
    console.log("Kedua transaksi lolos (kemungkinan tidak benar-benar konkuren / stok cukup untuk keduanya secara berurutan).\n");
  } else {
    console.log(
      "BENAR: hanya SATU transaksi yang berhasil decrement, transaksi lainnya ditolak database\n" +
      "(rowCount 0) karena stok sudah tidak mencukupi SAAT eksekusi UPDATE. Stok tidak pernah negatif.\n"
    );
  }
  await client.end();
}

main().catch(console.error);
