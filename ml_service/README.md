# 🌾 PanenCerdas — ML Service v2.5

FastAPI service untuk prediksi panen berbasis Machine Learning dengan:
- Data iklim real dari **NASA POWER**
- NDVI real dari **NASA APPEEARS/MODIS** (MOD13Q1 16-hari 250m)
- 9 komoditas: **padi, jagung, kedelai, ubi_jalar, ubi_kayu, cabe_besar, cabe_rawit, bawang_merah, bawang_putih**
- Fitur **hama** + **varietas** sudah masuk schema & training
- Cakupan: **514 kabupaten/kota** (drill-down dari **37 provinsi nasional**, centroid level)
- Online learning dari feedback petani — retrain otomatis tiap 10 feedback
- Tiga model RandomForest scikit-learn: `harvest_days`, `yield`, `risk`

---

## 📁 Struktur File

```
ml_service/
├── main.py                       ← FastAPI app (entry point, all /api/* routes)
├── model.py                      ← Training & prediksi (v2.5 — 9 komoditas + hama + varietas)
├── train.py                      ← CLI training script
├── schemas.py                    ← Pydantic input/output models
├── fallback_rules.py             ← Rule-based fallback (saat model tidak ada)
│
├── data_fetcher.py               ← Fetch iklim dari NASA POWER
├── data_cache.py                 ← Cache fetch (NASA POWER + APPEEARS) di SQLite
├── ndvi_fetcher.py               ← Fetch NDVI dari NASA APPEEARS (single + time-series)
│
├── database.py                   ← ORM + CRUD (SQLAlchemy + SQLite)
├── kementan_data.py                   ← Reader CSV Kementan produksi 2021-2025
├── provinces_data.py             ← Lookup 37 provinsi (kode Kementan + centroid + alias)
│
├── feedback_router.py            ← /api/feedback{,/stats,/history}
├── predictions_router.py         ← /api/predictions{,/{id},/history}  ← multi-provinsi
├── dashboard_router.py           ← /api/dashboard/{summary,trend}     ← real Kementan + DB
├── regions_router.py             ← /api/regions/{geojson,provinces}
├── lahan_router.py               ← /api/lahan (derive dari prediction_log)
│
├── retrain_scheduler.py          ← Auto-retrain tiap 10 feedback / Minggu 02.00
│
├── requirements.txt
├── .env                          ← Konfigurasi (DB + APPEEARS creds)
│
├── scripts/
│   ├── fetch_historical.py       ← Seed iklim historis NASA POWER (sekali jalan)
│   ├── prewarm_ndvi_cache.py     ← Pre-warm NDVI cache untuk 7 DIY + 36 provinsi
│   └── test_appeears_login.py    ← Cek credentials APPEEARS (login saja, ringan)
│
├── data/
│   ├── nasa_power_cache.csv      ← ✅ Iklim historis NASA POWER
│   ├── kementan_produksi.csv          ← ✅ Kementan 9 komoditas × 37 provinsi × 2020-2025
│   ├── kementan_template.csv          ← ⚠️ Template lama (dummy 3 baris)
│   ├── pest_data.csv             ← 🔶 Referensi dummy untuk synthetic
│   ├── variety_data.csv          ← 🔶 Referensi dummy untuk synthetic
│   └── kabupaten_indonesia.geojson ← polygon 514 kabupaten/kota (GADM L2, kode 4-digit)
│
├── Data_Raw/                     ← CSV mentah Kementan per komoditas (sebelum convert)
│   ├── produksi_*.csv
│   ├── lahan_*.csv
│   ├── temp_*.csv
│   └── convert_kementan_to_training.py
│
└── saved_models/                 ← Auto-dibuat saat train
    ├── harvest_days_model.joblib   ← RandomForestRegressor
    ├── yield_model.joblib          ← RandomForestRegressor (target = yield_ratio)
    ├── risk_model.joblib           ← RandomForestClassifier (balanced)
    ├── crop_encoder.joblib
    ├── crop_group_encoder.joblib
    └── feature_meta.joblib         ← Metadata fitur (hama/varietas/normalisasi)
```

---

## ⚡ Cara Menjalankan (Step-by-Step)

### Step 1 — Masuk ke folder

```bash
cd ml_service
```

### Step 2 — Buat virtual environment (Python 3.12)

```bash
python -m venv ../.venv

# Linux/Mac:
source ../.venv/bin/activate

# Windows PowerShell:
..\.venv\Scripts\Activate.ps1
```

Atau pakai script setup di root: `..\setup-backend.ps1` (Windows).

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — Konfigurasi `.env`

File `.env` di folder `ml_service/` (sudah ada), cek bagian ini:

```env
# Database — default SQLite (zero-config)
DATABASE_URL=sqlite:///./panencerdas_ml.db
# Production: ganti ke postgresql://user:pass@host:5432/dbname

# Server
HOST=0.0.0.0
PORT=8000

# Retrain otomatis
RETRAIN_FEEDBACK_THRESHOLD=10
RETRAIN_CRON_HOUR=2
RETRAIN_CRON_DAY=sunday

# NASA APPEEARS (untuk NDVI MODIS real)
# Daftar gratis: https://appeears.earthdatacloud.nasa.gov/
# Kosongkan -> NDVI fallback ke estimasi musiman
APPEEARS_USER=your_username
APPEEARS_PASS=your_password
```

> ⚠️ Catatan: nama variable **`APPEEARS`** (3 huruf E), bukan `APPEARS`. Sering typo.

Verifikasi credentials:

```bash
python scripts/test_appeears_login.py
```

### Step 5 — Konfigurasi fitur hama & varietas (opsional)

Buka `model.py`, baris konfigurasi:

```python
USE_PEST    = True   # ← False kalau skip hama sama sekali
USE_VARIETY = True   # ← False kalau skip varietas sama sekali
```

Default `True` untuk keduanya. Schema `PredictInput` (`schemas.py`) sudah punya field `pest_pressure` dan `variety` — tidak perlu edit manual.

### Step 6 — Latih model

```bash
# Pakai data yang ada (Kementan + NASA cache + synthetic):
python train.py

# + sertakan feedback petani dari database:
python train.py --with-db
```

Output yang diharapkan:

```
🌱 Menyiapkan data training...
   Komoditas      : ['bawang_merah', 'bawang_putih', 'cabe_besar', 'cabe_rawit',
                     'jagung', 'kedelai', 'padi', 'ubi_jalar', 'ubi_kayu']
   Fitur hama     : ✅
   Fitur varietas : ✅
   Total data     : ~2000 baris (real Kementan + synthetic)
🤖 Training harvest_days model (RandomForest)...
   MAE harvest_days : ±6.8 hari
🌾 Training yield model (RandomForest, normalized ratio)...
   MAE yield        : ±0.30 ton/ha
⚠️  Training risk classifier (RandomForest)...
   Accuracy risk    : ~94%
✅ Semua model tersimpan di saved_models/
```

### Step 7 — Pre-warm cache NDVI (sekali, sebelum demo)

NDVI MODIS via APPEEARS lambat (5-15 menit per koordinat). Pre-warm cache untuk koordinat penting:

```bash
# Single-point cache untuk /api/predict (7 DIY + 36 provinsi, ~30-40 menit):
python scripts/prewarm_ndvi_cache.py

# + time-series 2018-2025 untuk grafik di /api/predictions/{id} (~50 menit):
python scripts/prewarm_ndvi_cache.py --with-series

# Hanya DIY pilot (lebih cepat ~10 menit):
python scripts/prewarm_ndvi_cache.py --diy-only

# Force refresh (bypass cache yang sudah ada):
python scripts/prewarm_ndvi_cache.py --force
```

Cache TTL: single-point 24 jam, time-series 7 hari.

### Step 8 — Jalankan server

```bash
python main.py
# atau:
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Server: **http://localhost:8000**
Swagger UI: **http://localhost:8000/docs**

---

## 🔗 Endpoint Lengkap

Semua endpoint pakai prefix `/api/`. Diakses lewat Express gateway (`:4200/api/*`) atau langsung ke FastAPI (`:8000/api/*`).

### Petani
| Method | URL | Keterangan |
|--------|-----|------------|
| POST | `/api/predict` | **Prediksi panen** (terima query `petani_id`, `lahan_id`) |
| POST | `/api/feedback` | Lapor hasil panen aktual (post-panen) |
| GET  | `/api/feedback/stats` | Statistik feedback (total, threshold retrain) |
| GET  | `/api/feedback/history?petani_id=` | Riwayat feedback per petani |
| GET  | `/api/lahan?petani_id=` | Daftar lahan petani (derive dari prediction_log) |
| GET  | `/api/predictions/history?petani_id=&lahan_id=` | Riwayat prediksi |
| GET  | `/api/varieties?crop_type=` | Katalog varietas per komoditas |
| GET  | `/api/weather/recent?lat=&lon=&days=7` | Rangkuman cuaca 7 hari terakhir NASA POWER |

### Pemerintah
| Method | URL | Keterangan |
|--------|-----|------------|
| GET | `/api/dashboard/summary?province=&commodity=` | 4 tile KPI (Kementan terbaru + DB activity) |
| GET | `/api/dashboard/trend?province=&commodity=` | Tren produksi 2020-2025 (real Kementan) |
| GET | `/api/predictions?province=&commodity=` | Prediksi per region (3 mode — lihat below) |
| GET | `/api/predictions/{region_id}?commodity=` | Detail region (NDVI series + backtest real Kementan) |
| GET | `/api/regions/geojson?province=` | Polygon DIY ATAU Point centroid 37 provinsi |
| GET | `/api/regions/provinces` | Daftar 37 provinsi (untuk dropdown) |

### Admin
| Method | URL | Keterangan |
|--------|-----|------------|
| GET    | `/api/health` | Status model, feedback, cache |
| GET    | `/api/model/info` | Versi model + metrik (MAE, akurasi, n_data) |
| POST   | `/api/retrain?force=true` | Trigger retrain manual |
| DELETE | `/api/cache/expired` | Hapus cache expired |

### Mode `/api/predictions?province=`:
- `province=DI Yogyakarta` (atau nama provinsi lain) → **semua kabupaten/kota** provinsi itu (drill-down, kabupaten-level)
- `province=ALL` (alias: `Indonesia`, `Nasional`) → **37 provinsi** sekaligus (provincial-level)

### Mode `/api/predictions/{region_id}`:
- `region_id="KAB_3471"` → kabupaten/kota (lookup tabel Supabase `kabupaten`, kode 4-digit Kemendagri)
- `region_id="PROV_32"` → provinsi (lookup Kementan code)

---

## 📥 Contoh Request — POST /api/predict

### Minimal (default iklim Indonesia):

```bash
curl -X POST http://localhost:8000/api/predict \
  -H "Content-Type: application/json" \
  -d '{
    "crop_type": "padi",
    "land_area_ha": 1.5
  }'
```

### Dengan GPS (NASA POWER + NDVI MODIS otomatis):

```bash
curl -X POST "http://localhost:8000/api/predict?petani_id=petani_abc&lahan_id=Petak Utara" \
  -H "Content-Type: application/json" \
  -d '{
    "crop_type": "padi",
    "land_area_ha": 1.5,
    "lat": -7.77,
    "lon": 110.49,
    "pest_pressure": 0.3,
    "variety": "Ciherang"
  }'
```

### Contoh response:

```json
{
  "prediction_log_id": 42,
  "harvest_days": 105,
  "yield_ton_per_ha": 5.3,
  "total_yield_ton": 7.95,
  "risk_level": "medium",
  "risk_score": 0.5,
  "recommendations": [
    "💧 Tingkatkan irigasi — curah hujan di bawah optimal",
    "🐛 Waspadai serangan hama sedang — pantau lahan tiap 3 hari"
  ],
  "model_source": "ml_model",
  "confidence": 0.87,
  "climate_source": "nasa_power",
  "ndvi_source": "modis_appeears"
}
```

---

## 📊 Status Data & Kontribusi ke Model

| Sumber | Status | Lokasi | Kontribusi |
|--------|--------|--------|------------|
| Kementan produksi 2020-2025 | ✅ Real | `data/kementan_produksi.csv` | 37 provinsi × 9 komoditas, ~1.500 baris training |
| NASA POWER cache | ✅ Real | `data/nasa_power_cache.csv` | Iklim 20+ lokasi Indonesia |
| Feedback petani | 🔄 Akumulasi | DB (`training_feedback`) | Bertambah seiring pemakaian |
| MODIS NDVI APPEEARS | ✅ Real (kalau prewarm) | DB cache | Per-koordinat, TTL 24 jam |
| Synthetic generator | 🤖 Auto | `model._generate_synthetic_data()` | Pelengkap sampai 2000 baris |
| `pest_data.csv` | 🔶 Dummy | `data/` | Hanya seed distribusi synthetic |
| `variety_data.csv` | 🔶 Dummy | `data/` | Hanya seed distribusi synthetic |

---

## 🔄 Alur Data di Model (v2.5)

```
Data Real:
  kementan_produksi.csv      → 37 provinsi × 9 crop × 5 tahun        [~1.500 baris]
  nasa_power_cache.csv  → iklim 20+ lokasi                       [~20 baris]
  Feedback petani (DB)  → hasil panen aktual                     [akumulasi]
  APPEEARS NDVI cache   → NDVI MODIS per-koordinat               [on-demand]

         ↓ jika total < 2000 baris
  Synthetic data        → auto-generate, klimatologis 9 komoditas

         ↓
  Fitur per model:
    harvest_days_model : ndvi, rainfall_mm, temperature_c, solar_radiation,
                         land_area_ha, crop_encoded, pest_pressure, variety_encoded
    yield_model        : ndvi, climate, land_area_ha, yield_ratio,
                         pest_pressure, variety_encoded
                         (target = yield_ratio = yield / baseline_per_crop)
    risk_model         : climate, land_area_ha, yield_ratio, crop_group_encoded,
                         pest_pressure, variety_encoded

         ↓
  3 RandomForest scikit-learn:
    harvest_days_model.joblib  → RandomForestRegressor  → hari panen
    yield_model.joblib         → RandomForestRegressor  → yield_ratio × baseline
    risk_model.joblib          → RandomForestClassifier → low/medium/high
```

---

## 🌾 Komoditas yang Didukung

9 komoditas, semua punya: baseline yield + baseline harvest_days + profil iklim optimal + katalog varietas.

| Komoditas (`crop_type`) | Base yield (ton/ha) | Base hari panen | Jumlah varietas |
|-------------------------|---------------------|------------------|-----------------|
| `padi`                  | 5.2 | 110 | 5 (IR64, Ciherang, Inpari32, Memberamo, Lokal) |
| `jagung`                | 5.8 | 100 | 4 (NK7328, Pioneer36, Bisi18, Lokal) |
| `kedelai`               | 1.5 | 85  | 4 (Anjasmoro, Dena1, Grobogan, Lokal) |
| `ubi_jalar`             | 15.0 | 120 | 4 (Cilembu, Papua Solossa, Sukuh, Lokal) |
| `ubi_kayu` (alias `singkong`) | 20.0 | 270 | 4 (UJ5, Adira1, Malang6, Lokal) |
| `cabe_besar`            | 8.0 | 90 | 4 (Lado, Tit Super, Gada, Lokal) |
| `cabe_rawit`            | 6.0 | 75 | 4 (Pelita, Dewata, Ori, Lokal) |
| `bawang_merah`          | 9.5 | 65 | 4 (Bima Brebes, Tajuk, Katumi, Lokal) |
| `bawang_putih`          | 7.0 | 100 | 4 (Lumbu Hijau, Tawangmangu, Kesuma, Lokal) |

Edit `model.VARIETY_CATALOG` untuk tambah varietas baru.

---

## 🦗 Pest Pressure Map

Mapping kategori UI ke nilai `pest_pressure` (0.0–1.0):

| Kategori | Nilai |
|----------|-------|
| `tidak_ada` | 0.0 |
| `ringan` | 0.3 |
| `sedang` | 0.6 |
| `berat` | 0.9 |
| **Padi** | |
| `wereng_coklat` | 0.7 |
| `blast` | 0.8 |
| `penggerek_batang` | 0.5 |
| **Jagung** | |
| `ulat_grayak` | 0.6 |
| `bulai` | 0.75 |
| **Kedelai** | |
| `karat_daun` | 0.55 |
| `ulat_penggulung` | 0.5 |
| **Hortikultura** (cabe/bawang) | |
| `antraknosa` | 0.7 |
| `busuk_buah` | 0.65 |
| `thrips` | 0.5 |
| `fusarium` | 0.75 |
| `busuk_batang` | 0.75 |
| `kutu_daun` | 0.4 |

Lengkapnya di `model.PEST_PRESSURE_MAP`.

---

## 🖥️ Integrasi dengan Express Gateway

ML service dipanggil **lewat Express** di `localhost:4200`, bukan langsung. Express handle CORS + fallback:

```javascript
// frontend/src/lib/api.ts
const BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:4200";

// Predict — terima query opsional petani_id + lahan_id
api.ml.predict(body, { petani_id: "petani_abc", lahan_id: "Petak Utara" });

// Lahan list (read-only, derive dari prediction_log)
api.lahan.list("petani_abc");

// Predictions dengan mode
api.predictions.list("DI Yogyakarta", "padi");   // kabupaten/kota DIY (drill-down)
api.predictions.list("ALL", "padi");              // 37 provinsi
api.predictions.list("Jawa Barat", "jagung");    // kabupaten/kota Jawa Barat

// Detail
api.predictions.detail("KAB_3471");     // kabupaten/kota (Kota Yogyakarta)
api.predictions.detail("PROV_32");      // Jawa Barat provincial

// Regions (geojson)
api.regions.geojson("DI Yogyakarta");   // polygon kabupaten/kota
api.regions.geojson("ALL");             // 37 Point centroid
api.regions.provinces();                // daftar 37 provinsi
```

Express config (`backend-express/index.js`):

```javascript
app.use("/api/predict",     predictRoute);     // dengan fallback ML-down
app.use("/api/feedback",    feedbackRoute);
app.use("/api/health",      healthRoute);
app.use("/api/dashboard",   passthroughRoute);
app.use("/api/predictions", passthroughRoute);
app.use("/api/regions",     passthroughRoute);
app.use("/api/weather",     passthroughRoute);
app.use("/api/lahan",       passthroughRoute);
app.use("/api/varieties",   passthroughRoute);
app.use("/api/model",       passthroughRoute);
```

Env backend:
```env
ML_SERVICE_URL=http://localhost:8000     # development
PORT=4200
FRONTEND_URL=http://localhost:3000
```

---

## 📱 Panduan Frontend

### Komponen yang sudah jadi

| Halaman | Endpoint dipakai |
|---------|-----------------|
| `/petani/prediksi` | `POST /api/predict` (dengan `petani_id` + `lahan_id` query) |
| `/petani/lahan` | `GET /api/lahan?petani_id=` |
| `/petani/cuaca` | `GET /api/weather/recent` |
| `/petani/dashboard` | (mock, optional rewire ke `/api/predictions/history`) |
| `/pemerintah/dashboard` | `GET /api/dashboard/{summary,trend}` |
| `/pemerintah/produksi` | `GET /api/predictions` + `GET /api/regions/geojson` |
| `/pemerintah/analisis` | `GET /api/predictions/{id}` |
| `/pemerintah/alert` | `GET /api/predictions` (filter status defisit) |

### Form prediksi — field

**Selalu tampilkan:**
- Dropdown `crop_type`: 9 komoditas
- Input `land_area_ha`
- Input opsional `lahan_name` (auto-suggest dari localStorage history)

**Pilihan input iklim:**
- Mode `default` → backend pakai Indonesia defaults
- Mode `gps` → kirim `lat`, `lon` → backend fetch NASA POWER + APPEEARS NDVI
- Mode `manual` → kirim `rainfall_mm`, `temperature_c`, `solar_radiation`, `ndvi`

**Opsional v2.5:**
- Dropdown `variety`: dinamis berdasarkan `crop_type`
- Dropdown `pest_level`: Tidak Ada/Ringan/Sedang/Berat → konversi ke `pest_pressure`

### Identitas petani (MVP, tanpa login)

```typescript
// lib/auth.ts
getPetaniId()       // stable per-browser, auto-generate kalau kosong
getLahanNames()     // history nama lahan yang pernah dipakai
addLahanName(name)  // simpan nama lahan baru ke localStorage
```

Saat submit `/api/predict`, kirim `petani_id` (dari `getPetaniId()`) + `lahan_id` (input form) sebagai query param. Backend auto-isi ke `prediction_log`. Kemudian `/api/lahan?petani_id=` bisa derive daftar lahan otomatis.

### Menampilkan hasil prediksi

| Field response | Cara tampil |
|---------------|-------------|
| `harvest_days` | "Perkiraan panen dalam **N hari**" |
| `yield_ton_per_ha` | "Estimasi hasil: **X.X ton/ha**" |
| `total_yield_ton` | "Total: **~Y ton**" |
| `risk_level` | Badge: `low`=hijau, `medium`=kuning, `high`=merah |
| `confidence` | "Keyakinan: **NN%**" |
| `recommendations` | List card dengan icon |
| `climate_source` | Chip: `nasa_power` / `user_input` / `fallback` |
| `ndvi_source` | Chip: `modis_appeears` / `seasonal_estimate` / `user_input` |

---

## ❓ FAQ

**Q: Kenapa NDVI saya selalu `seasonal_estimate`, bukan `modis_appeears`?**

3 kemungkinan: (1) APPEEARS credentials di `.env` belum diisi → `python scripts/test_appeears_login.py` untuk verify. (2) Cache koordinat belum diisi — pertama kali request ke koordinat baru, Express timeout 5 detik sebelum APPEEARS selesai (5-15 menit). Solusinya pre-warm: `python scripts/prewarm_ndvi_cache.py`. (3) APPEEARS sedang sibuk → task antri >20 menit → fallback. Coba lagi nanti.

**Q: Bagaimana drill-down per kabupaten/kota bekerja?**

Setiap provinsi (termasuk non-DIY) di-drill-down ke semua kabupaten/kota-nya: row dibaca dari tabel Supabase `kabupaten` (kode 4-digit Kemendagri, centroid lat/lon dari GADM L2). Yield diprediksi per kabupaten dengan baseline Kementan provinsi induk; luas/produksi memakai data BPS per-kabupaten bila tersedia (`kabupaten_produksi`), selain itu 0 (jujur) sampai data masuk.

**Q: Backtest 5 tahun di `/api/predictions/{id}` aktualnya dari mana?**

Real dari `kementan_produksi.csv` (yield = produksi/luas_panen per provinsi per tahun). Untuk kabupaten/kota, backtest pakai aktual `kabupaten_produksi` (BPS) bila ada; bila belum, dipakai data provinsi induk sebagai proxy.

**Q: Apakah bisa pakai model tanpa hama/varietas?**

Ya. Set `USE_PEST = False` dan/atau `USE_VARIETY = False` di `model.py`, lalu retrain. Schema tetap accept field tersebut (opsional) — backend abaikan saat predict.

**Q: Model retrain otomatis kapan?**

Dua trigger: (1) tiap **10 feedback baru** dari petani, (2) **Minggu pukul 02.00**. Trigger manual via `POST /api/retrain?force=true`. Konfigurasi: `RETRAIN_FEEDBACK_THRESHOLD`, `RETRAIN_CRON_HOUR`, `RETRAIN_CRON_DAY` di `.env`.

**Q: Kalau dapat data hama/varietas real, gimana cara masukin?**

Taruh CSV di `data/pest_data.csv` / `data/variety_data.csv` (format lihat section di bawah). Pastikan `USE_PEST=True` / `USE_VARIETY=True`, jalankan `python train.py`. Saat ini file dummy hanya dipakai sebagai referensi distribusi untuk synthetic generator — belum dijoin ke baris training real.

**Q: Saat task APPEEARS gagal terus, apa yang harus dilakukan?**

Cek satu-per-satu: (a) Server APPEEARS down? — `curl https://appeears.earthdatacloud.nasa.gov/api/login -u user:pass`. (b) Koneksi internet stabil? — Errno 11001 = DNS gagal. (c) Naikin `POLL_MAX_ATTEMPTS` di `ndvi_fetcher.py` (default 80 = 20 menit). (d) Kalau memang server lagi sibuk, fallback `seasonal_estimate` masih kasih hasil prediksi yang masuk akal.

---

## 📄 Format Data Hama Real (`pest_data.csv`)

Jika dapat data dari Dinas Pertanian / BBPOPT / Kementan:

```csv
provinsi,crop_type,tahun,musim,pest_type,pest_pressure,luas_terserang_ha,keterangan
Jawa Timur,padi,2024,MH,wereng_coklat,0.7,120,Serangan berat di Jember
```

Kolom wajib: `provinsi`, `crop_type`, `tahun`, `pest_pressure`. Hitung dari luas:

```
pest_pressure = clip(luas_terserang_ha / luas_tanam_total_ha, 0.0, 1.0)
```

Atau kategorik:
- `0.0` = tidak ada · `0.3` = ringan (<10%) · `0.6` = sedang (10–30%) · `0.9` = berat (>30%)

---

## 📄 Format Data Varietas Real (`variety_data.csv`)

```csv
provinsi,crop_type,tahun,variety,luas_tanam_ha,keterangan
Jawa Timur,padi,2024,Ciherang,850,Dominan di sawah irigasi
```

Kolom wajib: `provinsi`, `crop_type`, `tahun`, `variety`. Untuk varietas baru, tambah ke `VARIETY_CATALOG` di `model.py` (tuple `(nama, yield_modifier, days_modifier)`).
