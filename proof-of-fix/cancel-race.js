const { Client } = require("pg");

async function attemptCancel(label) {
  const client = new Client({
    connectionString: "postgresql://postgres:testpass@localhost:5432/agriloop_test",
  });
  await client.connect();

  await client.query("BEGIN");

  // Ini pola BARU (conditional updateMany): status dan guard terminal-state
  // digabung jadi satu operasi atomic, persis seperti fix di
  // src/app/api/orders/[id]/route.ts
  const result = await client.query(
    `UPDATE orders SET status = 'CANCELLED'
     WHERE id = 1 AND status NOT IN ('COMPLETED', 'CANCELLED')
     RETURNING id`
  );

  const wonRace = (result.rowCount ?? 0) > 0;

  if (wonRace) {
    // Restore stok 8 unit yang dulu dipesan -- HANYA dijalankan kalau
    // request ini yang menang guard di atas.
    await client.query("UPDATE product SET quantity = quantity + 8 WHERE id = 1");
  }

  await client.query("COMMIT");
  await client.end();

  return { label, wonRace };
}

async function main() {
  console.log("=== TEST: Dua PATCH pembatalan bersamaan ke order yang sama ===");
  console.log("Stok sebelum: 2 (mewakili 10 awal - 8 yang sudah dipesan)\n");

  const [r1, r2] = await Promise.all([attemptCancel("Request A"), attemptCancel("Request B")]);

  console.log(r1.label, "- menang race & restore stok?", r1.wonRace);
  console.log(r2.label, "- menang race & restore stok?", r2.wonRace);

  const client = new Client({
    connectionString: "postgresql://postgres:testpass@localhost:5432/agriloop_test",
  });
  await client.connect();
  const finalProduct = await client.query("SELECT quantity FROM product WHERE id = 1");
  const finalOrder = await client.query("SELECT status FROM orders WHERE id = 1");
  console.log("\nStok akhir:", finalProduct.rows[0].quantity, "(harus 10, BUKAN 18 kalau restore ganda)");
  console.log("Status order akhir:", finalOrder.rows[0].status);

  const qty = Number(finalProduct.rows[0].quantity);
  if (qty === 10 && r1.wonRace !== r2.wonRace) {
    console.log("\nBENAR: hanya SATU request yang berhasil restore, stok tepat 10, tidak ganda.");
  } else if (qty > 10) {
    console.log("\n!! BUG: stok ter-restore lebih dari sekali.");
  } else {
    console.log("\nHasil tidak sesuai ekspektasi, perlu investigasi ulang.");
  }
  await client.end();
}

main().catch(console.error);
