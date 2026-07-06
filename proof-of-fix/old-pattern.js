const { Client } = require("pg");

async function simulateOldPattern(delayMs) {
  const client = new Client({
    connectionString: "postgresql://postgres:testpass@localhost:5432/agriloop_test",
  });
  await client.connect();

  await client.query("BEGIN");
  const res = await client.query("SELECT quantity FROM product WHERE id = 1");
  const currentQty = Number(res.rows[0].quantity);

  const requested = 8;
  // Ini persis logika lama: validasi di aplikasi, terpisah dari waktu decrement.
  const validationPassed = currentQty >= requested;

  // Simulasikan delay pemrosesan (mis. jaringan/komputasi lain) sebelum decrement,
  // supaya window race condition-nya lebih mudah terjadi & terlihat.
  await new Promise((r) => setTimeout(r, delayMs));

  if (validationPassed) {
    await client.query("UPDATE product SET quantity = quantity - $1 WHERE id = 1", [requested]);
  }

  await client.query("COMMIT");
  await client.end();

  return { validationPassed, currentQtyAtCheck: currentQty };
}

async function main() {
  console.log("=== SIMULASI POLA LAMA (baca lalu tulis, tanpa guard) ===");
  console.log("Stok awal: 10. Dua transaksi bersamaan, masing-masing minta 8.\n");

  const [r1, r2] = await Promise.all([
    simulateOldPattern(50),
    simulateOldPattern(50),
  ]);

  console.log("Transaksi A - validasi lolos?", r1.validationPassed, "| stok terlihat saat cek:", r1.currentQtyAtCheck);
  console.log("Transaksi B - validasi lolos?", r2.validationPassed, "| stok terlihat saat cek:", r2.currentQtyAtCheck);

  const client = new Client({
    connectionString: "postgresql://postgres:testpass@localhost:5432/agriloop_test",
  });
  await client.connect();
  const final = await client.query("SELECT quantity FROM product WHERE id = 1");
  console.log("\nSTOK AKHIR DI DATABASE:", final.rows[0].quantity);
  console.log(
    Number(final.rows[0].quantity) < 0
      ? "!! BUG TERKONFIRMASI: stok jadi NEGATIF meski masing-masing validasi individual terlihat 'lolos'.\n"
      : "Stok tidak negatif pada run ini (race condition bersifat probabilistik, coba run ulang).\n"
  );
  await client.end();
}

main().catch(console.error);
