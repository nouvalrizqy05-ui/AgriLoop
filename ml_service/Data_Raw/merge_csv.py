import pandas as pd

files = [
    "temp_padi.csv",
    "temp_jagung.csv",
    "temp_kedelai.csv",
    "temp_ubi_kayu.csv",
    "temp_ubi_jalar.csv",
    "temp_bawang_putih.csv",
    "temp_bawang_merah.csv",
    "temp_cabe_rawit.csv",
    "temp_cabe_besar.csv"
]

dfs = []
for f in files:
    print(f"Loading {f}...")
    dfs.append(pd.read_csv(f))

combined = pd.concat(dfs, ignore_index=True)

print("Total rows:", len(combined))

combined.to_csv("kementan_produksi.csv", index=False)
print("✅ File saved: kementan_produksi.csv")