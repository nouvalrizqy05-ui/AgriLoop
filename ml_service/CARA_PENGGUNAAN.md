# 📖 Cara Penggunaan — Online Learning System PanenCerdas

---

## 🧠 Konsep Singkat

Model PanenCerdas **belajar dari hasil panen nyata petani**.
Semakin banyak petani melaporkan hasilnya → model makin akurat.

```
Petani input kondisi lahan
         ↓
    POST /predict
         ↓
  Simpan ke database ──→ prediction_log (prediction_log_id dikembalikan)
         ↓
   Petani panen (tunggu beberapa hari/minggu)
         ↓
  Petani lapor hasil nyata
         ↓
    POST /feedback (pakai prediction_log_id tadi)
         ↓
  Terkumpul 10 feedback baru → retrain otomatis
         ↓
  Model baru dievaluasi → lebih baik? ganti | lebih buruk? rollback
```

---

## 🪜 Step-by-Step Cara Pakai

---

### STEP 1 — Setup & Jalankan Server

```bash
cd panencerdas/ml-service

# Install dependencies
pip install -r requirements.txt

# (Opsional tapi direkomendasikan) Seed data historis NASA POWER
# Estimasi waktu: 2–5 menit, hanya perlu dijalankan sekali
python scripts/fetch_historical.py

# Latih model pertama kali
python train.py

# Jalankan server
python main.py
```

Server jalan di `http://localhost:8000`
Swagger UI di `http://localhost:8000/docs`

---

### STEP 2 — Petani Minta Prediksi

Ada dua cara mengirim request:

**Cara A — Input nilai iklim manual:**

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "ndvi": 0.72,
    "rainfall_mm": 160,
    "temperature_c": 28,
    "solar_radiation": 210,
    "land_area_ha": 2.0,
    "crop_type": "padi"
  }'
```

**Cara B — Kirim koordinat, data iklim diambil otomatis dari NASA POWER:**

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "ndvi": 0.72,
    "rainfall_mm": 0,
    "temperature_c": 0,
    "solar_radiation": 0,
    "land_area_ha": 2.0,
    "crop_type": "padi",
    "lat": -7.25,
    "lon": 112.75
  }'
```

> Jika `lat` dan `lon` diisi, nilai `rainfall_mm`, `temperature_c`, dan `solar_radiation`
> di-_override_ otomatis dengan data real 30 hari terakhir dari NASA POWER
> (hasil di-cache selama 6 jam untuk menghindari fetch berulang).

**Response — simpan `prediction_log_id`-nya!**

```json
{
  "prediction_log_id": 42,
  "harvest_days": 88,
  "yield_ton_per_ha": 5.04,
  "total_yield_ton": 10.08,
  "risk_level": "low",
  "risk_score": 0.15,
  "recommendations": ["✅ Kondisi optimal — pertahankan manajemen saat ini"],
  "model_source": "ml_model",
  "confidence": 0.98,
  "climate_source": "nasa_power"
}
```

---

### STEP 3 — Petani Selesai Panen, Lapor Hasil Nyata

Setelah panen selesai, petani mengisi hasil nyatanya.
Field `prediction_log_id` opsional — boleh tidak diisi jika petani langsung input manual.

```bash
curl -X POST http://localhost:8000/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "prediction_log_id": 42,
    "ndvi": 0.72,
    "rainfall_mm": 160,
    "temperature_c": 28,
    "solar_radiation": 210,
    "land_area_ha": 2.0,
    "crop_type": "padi",
    "actual_harvest_days": 91,
    "actual_yield_ton_per_ha": 4.8,
    "actual_risk_level": "low",
    "petani_id": "P001",
    "catatan": "Ada sedikit hama wereng di minggu ke-3, sudah ditangani"
  }'
```

**Response:**

```json
{
  "success": true,
  "message": "Terima kasih! Data panen Anda akan membantu meningkatkan akurasi prediksi",
  "feedback_id": 7,
  "total_feedback_terkumpul": 7,
  "estimasi_retrain": "Perlu 3 feedback lagi untuk trigger retrain otomatis"
}
```

---

### STEP 4 — Retrain Otomatis Terjadi

Setelah **10 feedback baru** terkumpul, retrain berjalan otomatis di background.

Yang terjadi di balik layar:

```
10 feedback baru masuk
       ↓
Gabungkan: ~2000 data synthetic + data nyata (dikalikan 3 untuk bobot lebih besar)
       ↓
Train model baru (Random Forest, 150 estimators)
       ↓
Evaluasi: MAE harvest, MAE yield, Accuracy risk
       ↓
Lebih baik dari model lama?
  ├─ YA  → ganti model aktif → versi naik (v1 → v2)
  └─ TIDAK → rollback, tetap pakai model lama
```

**Cek status model setelah retrain:**

```bash
curl http://localhost:8000/model/info
```

```json
{
  "model_loaded": true,
  "active_version": 2,
  "trained_at": "2025-05-12T03:00:00",
  "metrics": {
    "mae_harvest_days": 6.8,
    "mae_yield": 0.29,
    "risk_accuracy": 0.945
  },
  "training_data": {
    "n_synthetic": 500,
    "n_real": 10
  },
  "feedback_pool": {
    "total": 10,
    "unused": 0,
    "used": 10
  },
  "climate_cache": {
    "total": 25,
    "active": 18,
    "expired": 7
  },
  "next_retrain": "Perlu 10 feedback lagi untuk auto-retrain"
}
```

---

### STEP 5 — (Opsional) Trigger Retrain Manual

Untuk admin/developer yang ingin paksa retrain:

```bash
# Retrain hanya jika ada ≥10 feedback baru
curl -X POST http://localhost:8000/retrain

# Paksa retrain meski belum cukup data
curl -X POST "http://localhost:8000/retrain?force=true"
```

---

## 📊 Cek Statistik Feedback

```bash
curl http://localhost:8000/feedback/stats
```

```json
{
  "feedback": {
    "total": 47,
    "unused": 3,
    "used": 44
  },
  "predictions": {
    "total_predictions": 120,
    "with_feedback": 47,
    "feedback_rate": 39.2
  },
  "pesan": "Terkumpul 47 feedback — model sudah mulai belajar dari data nyata"
}
```

**Lihat riwayat feedback per petani:**

```bash
curl "http://localhost:8000/feedback/history?petani_id=P001&limit=10"
```

---

## 🌍 Cek Status Health & Cache

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "model_loaded": true,
  "service": "PanenCerdas ML Service",
  "version": "v2",
  "feedback_stats": { "total": 47, "unused": 3, "used": 44 },
  "cache_stats": { "total": 25, "active": 18, "expired": 7 }
}
```

**Bersihkan cache expired:**

```bash
curl -X DELETE http://localhost:8000/cache/expired
```

---

## 🔄 Jadwal Retrain Otomatis

| Trigger   | Kapan                         | Keterangan                            |
| --------- | ----------------------------- | ------------------------------------- |
| Threshold | Setiap 10 feedback baru masuk | Otomatis langsung                     |
| Scheduled | Tiap Minggu pukul 02.00       | Force retrain meski belum 10 feedback |
| Manual    | POST /retrain                 | Dipanggil developer                   |

---

## ⚠️ Sistem Rollback

Model lama tidak langsung dihapus. Jika model baru lebih buruk:

```
Model baru dievaluasi
       ↓
MAE harvest baru < MAE lama × 1.05  → lebih baik (toleransi 5%)
  ATAU
Accuracy risk baru > accuracy lama × 0.95

       ├─ Kondisi terpenuhi → ganti model aktif
       └─ Tidak terpenuhi   → rollback, simpan model lama
```

---

## 📁 File Database

| Tabel               | Isi                                     |
| ------------------- | --------------------------------------- |
| `prediction_log`    | Semua history request `/predict`        |
| `training_feedback` | Hasil panen nyata dari petani           |
| `model_version`     | Riwayat versi model (untuk rollback)    |
| `climate_cache`     | Cache data iklim NASA POWER (TTL 6 jam) |

Development menggunakan **SQLite** (auto-dibuat, zero-config):

```
panencerdas_ml.db
```

Untuk production, ganti di `.env`:

```
DATABASE_URL=postgresql://user:password@localhost:5432/panencerdas
```

---

## 🌱 Kapan Model Jadi Benar-benar Akurat?

| Jumlah Feedback | Kondisi Model                               |
| --------------- | ------------------------------------------- |
| 0 – 9           | Synthetic only, belum belajar dari lapangan |
| 10 – 49         | Mulai belajar, ada improvement kecil        |
| 50 – 199        | Signifikan belajar dari data nyata          |
| 200+            | Model optimal, dominasi data lapangan nyata |

Makin banyak petani yang pakai dan lapor → model makin pintar untuk semua petani. 🌾
