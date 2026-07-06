import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";

// Endpoint ini digunakan untuk menerima data dari sensor IoT (hardware)
// yang dipasang di lahan petani (misal: sensor kelembapan tanah, curah hujan).
// Dalam skenario demo kompetisi, ini adalah "X-Factor" yang menunjukkan 
// bahwa sistem tidak hanya CRUD biasa, tetapi siap mengintegrasikan data 
// lapangan real-time untuk diteruskan ke model ML.

export async function POST(req: Request) {
  // Pengamanan sederhana menggunakan API Key / Secret di headers
  const authHeader = req.headers.get("Authorization");
  if (authHeader !== `Bearer ${process.env.IOT_WEBHOOK_SECRET || "demo-secret-123"}`) {
    return NextResponse.json({ error: "Unauthorized access to IoT Webhook" }, { status: 401 });
  }

  let body: {
    petaniId: string;
    moisture: number;
    rainfall: number;
  };

  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  if (!body.petaniId || typeof body.moisture !== "number" || typeof body.rainfall !== "number") {
    return NextResponse.json({ error: "Missing or invalid payload fields (petaniId, moisture, rainfall required)" }, { status: 400 });
  }

  // Verifikasi Petani exists
  const petani = await prisma.user.findUnique({
    where: { id: body.petaniId, role: "PETANI" }
  });

  if (!petani) {
    return NextResponse.json({ error: "Petani not found" }, { status: 404 });
  }

  // Insert data IoT ke database
  const sensorData = await prisma.sensorData.create({
    data: {
      petaniId: body.petaniId,
      moisture: body.moisture,
      rainfall: body.rainfall,
    }
  });

  // Di dunia nyata, data ini bisa langsung di-pipe ke Redis PubSub atau 
  // antrean (Queue) untuk diproses ML service. Untuk MVP ini, cukup simpan 
  // ke DB agar Dashboard Petani nanti bisa membaca riwayat tanahnya.

  return NextResponse.json({ 
    message: "IoT Data recorded successfully", 
    data: sensorData 
  }, { status: 201 });
}
