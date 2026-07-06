"""
scripts/parse_kementan.py
--------------------
Parse data produksi + luas panen PADI Kementan per provinsi,
gabungkan dengan data iklim NASA POWER,
output ke data/kementan_produksi.csv siap pakai oleh model.py

Catatan: File ini khusus untuk komoditas PADI karena data Kementan yang
tersedia adalah produksi padi. Untuk komoditas lain (jagung, kedelai,
ubi jalar, ubi kayu, cabe, bawang), data historis di-generate oleh
fetch_historical.py berbasis lokasi sentra produksi nyata.

Cara pakai:
    cd ml-service
    python scripts/parse_kementan.py

Output:
    data/kementan_produksi.csv   (khusus padi, 38 provinsi × 5 tahun)
"""

import asyncio
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from io import StringIO

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_fetcher import fetch_climate_data, estimate_ndvi_from_season


# ── DATA Kementan: PRODUKSI PADI (ton GKG) ─────────────────
KEMENTAN_PRODUKSI_HTML = """
<table>
<thead><tr>
  <td>No</td><td>Provinsi</td>
  <td>2021</td><td>2022</td><td>2023</td><td>2024</td><td>2025</td><td>Pertumbuhan</td>
</tr></thead>
<tbody>
<tr><td>1</td><td>Aceh</td><td>1,634,639.60</td><td>1,509,456.46</td><td>1,404,234.82</td><td>1,659,966.28</td><td>1,615,200.08</td><td>-2.7%</td></tr>
<tr><td>2</td><td>Sumatera Utara</td><td>2,004,142.51</td><td>2,088,583.81</td><td>2,087,474.15</td><td>2,204,875.51</td><td>2,752,655.35</td><td>24.84%</td></tr>
<tr><td>3</td><td>Sumatera Barat</td><td>1,317,209.38</td><td>1,373,532.19</td><td>1,482,468.79</td><td>1,356,467.93</td><td>1,382,696.68</td><td>1.93%</td></tr>
<tr><td>4</td><td>Riau</td><td>217,458.87</td><td>213,557.23</td><td>205,972.55</td><td>222,055.71</td><td>232,071.27</td><td>4.51%</td></tr>
<tr><td>5</td><td>Jambi</td><td>298,149.25</td><td>277,743.83</td><td>275,941.45</td><td>281,022.05</td><td>367,791.00</td><td>30.88%</td></tr>
<tr><td>6</td><td>Sumatera Selatan</td><td>2,552,443.19</td><td>2,775,069.26</td><td>2,832,773.92</td><td>2,909,411.67</td><td>3,627,022.14</td><td>24.67%</td></tr>
<tr><td>7</td><td>Bengkulu</td><td>271,117.19</td><td>281,610.09</td><td>286,684.43</td><td>272,848.55</td><td>266,567.39</td><td>-2.3%</td></tr>
<tr><td>8</td><td>Lampung</td><td>2,485,452.78</td><td>2,688,159.74</td><td>2,757,898.19</td><td>2,791,347.53</td><td>3,252,627.27</td><td>16.53%</td></tr>
<tr><td>9</td><td>Kepulauan Bangka Belitung</td><td>70,496.25</td><td>61,425.07</td><td>66,468.89</td><td>77,489.79</td><td>64,303.50</td><td>-17.02%</td></tr>
<tr><td>10</td><td>Kepulauan Riau</td><td>855.01</td><td>506.91</td><td>324.01</td><td>305.09</td><td>688.29</td><td>125.6%</td></tr>
<tr><td>11</td><td>Daerah Khusus Ibukota Jakarta</td><td>3,249.47</td><td>2,337.77</td><td>2,674.28</td><td>2,306.54</td><td>1,487.45</td><td>-35.51%</td></tr>
<tr><td>12</td><td>Jawa Barat</td><td>9,113,574.00</td><td>9,433,723.00</td><td>9,140,039.00</td><td>8,626,879.91</td><td>10,226,653.75</td><td>18.54%</td></tr>
<tr><td>13</td><td>Jawa Tengah</td><td>9,618,656.81</td><td>9,356,445.49</td><td>9,084,107.53</td><td>8,891,297.05</td><td>9,304,062.84</td><td>4.64%</td></tr>
<tr><td>14</td><td>Daerah Istimewa Yogyakarta</td><td>556,531.03</td><td>561,699.53</td><td>534,113.69</td><td>452,831.77</td><td>547,510.10</td><td>20.91%</td></tr>
<tr><td>15</td><td>Jawa Timur</td><td>9,789,587.67</td><td>9,526,515.67</td><td>9,710,661.33</td><td>9,270,435.29</td><td>10,438,360.58</td><td>12.6%</td></tr>
<tr><td>16</td><td>Banten</td><td>1,603,247.00</td><td>1,788,582.60</td><td>1,686,483.29</td><td>1,550,623.46</td><td>1,774,016.87</td><td>14.41%</td></tr>
<tr><td>17</td><td>Bali</td><td>618,910.81</td><td>680,601.60</td><td>673,580.65</td><td>635,473.35</td><td>587,866.28</td><td>-7.49%</td></tr>
<tr><td>18</td><td>Nusa Tenggara Barat</td><td>1,419,559.84</td><td>1,452,945.47</td><td>1,538,536.92</td><td>1,453,408.37</td><td>1,708,618.85</td><td>17.56%</td></tr>
<tr><td>19</td><td>Nusa Tenggara Timur</td><td>731,877.74</td><td>756,049.91</td><td>766,810.46</td><td>707,792.54</td><td>968,324.36</td><td>36.81%</td></tr>
<tr><td>20</td><td>Kalimantan Barat</td><td>711,897.00</td><td>731,226.00</td><td>700,291.00</td><td>764,784.15</td><td>757,829.16</td><td>-0.91%</td></tr>
<tr><td>21</td><td>Kalimantan Tengah</td><td>381,189.55</td><td>343,918.75</td><td>330,781.05</td><td>366,146.82</td><td>333,428.63</td><td>-8.94%</td></tr>
<tr><td>22</td><td>Kalimantan Selatan</td><td>1,016,314.00</td><td>819,419.00</td><td>875,545.73</td><td>1,029,567.93</td><td>1,179,338.37</td><td>14.55%</td></tr>
<tr><td>23</td><td>Kalimantan Timur</td><td>244,677.96</td><td>239,425.34</td><td>226,972.07</td><td>249,643.19</td><td>270,866.92</td><td>8.5%</td></tr>
<tr><td>24</td><td>Kalimantan Utara</td><td>29,967.31</td><td>30,533.59</td><td>23,602.11</td><td>30,079.77</td><td>38,038.08</td><td>26.46%</td></tr>
<tr><td>25</td><td>Sulawesi Utara</td><td>232,885.00</td><td>243,730.00</td><td>238,193.41</td><td>273,134.94</td><td>265,111.82</td><td>-2.94%</td></tr>
<tr><td>26</td><td>Sulawesi Tengah</td><td>867,012.77</td><td>744,408.70</td><td>821,367.41</td><td>761,936.39</td><td>920,086.08</td><td>20.76%</td></tr>
<tr><td>27</td><td>Sulawesi Selatan</td><td>5,090,637.23</td><td>5,360,169.37</td><td>4,876,386.11</td><td>4,818,429.39</td><td>5,466,592.36</td><td>13.45%</td></tr>
<tr><td>28</td><td>Sulawesi Tenggara</td><td>530,029.08</td><td>478,958.03</td><td>479,407.25</td><td>555,836.08</td><td>680,820.20</td><td>22.49%</td></tr>
<tr><td>29</td><td>Gorontalo</td><td>234,392.86</td><td>240,134.53</td><td>251,431.76</td><td>234,862.88</td><td>281,171.26</td><td>19.72%</td></tr>
<tr><td>30</td><td>Sulawesi Barat</td><td>311,072.46</td><td>353,513.29</td><td>291,458.59</td><td>318,876.59</td><td>386,084.41</td><td>21.08%</td></tr>
<tr><td>31</td><td>Maluku</td><td>116,803.67</td><td>92,601.06</td><td>79,958.34</td><td>91,125.35</td><td>103,818.57</td><td>13.93%</td></tr>
<tr><td>32</td><td>Maluku Utara</td><td>28,052.00</td><td>24,486.00</td><td>26,663.00</td><td>31,232.95</td><td>22,641.62</td><td>-27.51%</td></tr>
<tr><td>33</td><td>Papua Barat</td><td>26,926.93</td><td>23,963.92</td><td>22,566.81</td><td>20,729.15</td><td>13,365.79</td><td>-35.52%</td></tr>
<tr><td>34</td><td>Papua Barat Daya</td><td>0.00</td><td>0.00</td><td>2,397.00</td><td>988.64</td><td>830.38</td><td>-16.01%</td></tr>
<tr><td>35</td><td>Papua</td><td>286,279.80</td><td>193,943.47</td><td>3,760.45</td><td>4,609.95</td><td>1,859.06</td><td>-59.67%</td></tr>
<tr><td>36</td><td>Papua Selatan</td><td>0.00</td><td>0.00</td><td>183,628.00</td><td>217,789.62</td><td>362,542.11</td><td>66.46%</td></tr>
<tr><td>37</td><td>Papua Tengah</td><td>0.00</td><td>0.00</td><td>9,273.00</td><td>6,072.38</td><td>3,805.44</td><td>-37.33%</td></tr>
<tr><td>38</td><td>Papua Pegunungan</td><td>0.00</td><td>0.00</td><td>62.00</td><td>42.38</td><td>89.42</td><td>111%</td></tr>
</tbody>
</table>
"""

# ── DATA Kementan: LUAS PANEN PADI (ha) ────────────────────
# Sumber: Kementan — data nyata per provinsi per tahun
KEMENTAN_LUAS_HTML = """
<table>
<thead><tr>
  <td>No</td><td>Provinsi</td>
  <td>2021</td><td>2022</td><td>2023</td><td>2024</td><td>2025</td><td>Pertumbuhan</td>
</tr></thead>
<tbody>
<tr><td>1</td><td>Aceh</td><td>297,058.38</td><td>271,750.16</td><td>254,287.38</td><td>301,196.35</td><td>283,182.01</td><td>-5.98%</td></tr>
<tr><td>2</td><td>Sumatera Utara</td><td>385,405.00</td><td>411,462.10</td><td>406,109.49</td><td>419,463.48</td><td>535,009.20</td><td>27.55%</td></tr>
<tr><td>3</td><td>Sumatera Barat</td><td>272,391.95</td><td>271,883.11</td><td>300,564.77</td><td>295,278.98</td><td>284,075.59</td><td>-3.79%</td></tr>
<tr><td>4</td><td>Riau</td><td>53,062.35</td><td>51,054.04</td><td>51,914.14</td><td>56,421.96</td><td>59,502.09</td><td>5.46%</td></tr>
<tr><td>5</td><td>Jambi</td><td>64,412.26</td><td>60,539.59</td><td>61,236.64</td><td>61,625.68</td><td>80,373.17</td><td>30.42%</td></tr>
<tr><td>6</td><td>Sumatera Selatan</td><td>496,241.65</td><td>513,378.20</td><td>504,143.03</td><td>521,092.21</td><td>636,315.64</td><td>22.11%</td></tr>
<tr><td>7</td><td>Bengkulu</td><td>55,704.69</td><td>57,151.84</td><td>57,877.18</td><td>55,775.09</td><td>51,653.87</td><td>-7.39%</td></tr>
<tr><td>8</td><td>Lampung</td><td>489,573.23</td><td>518,256.06</td><td>530,108.09</td><td>531,715.12</td><td>596,017.07</td><td>12.09%</td></tr>
<tr><td>9</td><td>Kepulauan Bangka Belitung</td><td>18,278.27</td><td>15,107.80</td><td>15,284.56</td><td>18,202.56</td><td>16,286.54</td><td>-10.53%</td></tr>
<tr><td>10</td><td>Kepulauan Riau</td><td>270.16</td><td>179.48</td><td>115.27</td><td>113.33</td><td>216.07</td><td>90.65%</td></tr>
<tr><td>11</td><td>Daerah Khusus Ibukota Jakarta</td><td>559.97</td><td>477.25</td><td>542.93</td><td>498.31</td><td>273.54</td><td>-45.11%</td></tr>
<tr><td>12</td><td>Jawa Barat</td><td>1,604,109.00</td><td>1,662,403.00</td><td>1,583,656.00</td><td>1,475,362.09</td><td>1,755,300.24</td><td>18.97%</td></tr>
<tr><td>13</td><td>Jawa Tengah</td><td>1,696,712.36</td><td>1,688,669.65</td><td>1,642,761.23</td><td>1,554,777.14</td><td>1,674,994.11</td><td>7.73%</td></tr>
<tr><td>14</td><td>Daerah Istimewa Yogyakarta</td><td>107,506.16</td><td>110,927.24</td><td>105,693.66</td><td>96,976.13</td><td>107,224.23</td><td>10.57%</td></tr>
<tr><td>15</td><td>Jawa Timur</td><td>1,747,481.20</td><td>1,693,210.70</td><td>1,698,083.31</td><td>1,616,985.05</td><td>1,841,346.29</td><td>13.88%</td></tr>
<tr><td>16</td><td>Banten</td><td>318,248.46</td><td>337,240.74</td><td>311,200.00</td><td>299,090.79</td><td>345,420.54</td><td>15.49%</td></tr>
<tr><td>17</td><td>Bali</td><td>105,201.31</td><td>112,320.62</td><td>108,514.06</td><td>103,803.93</td><td>96,444.30</td><td>-7.09%</td></tr>
<tr><td>18</td><td>Nusa Tenggara Barat</td><td>276,211.88</td><td>270,092.88</td><td>287,512.14</td><td>281,717.84</td><td>322,895.73</td><td>14.62%</td></tr>
<tr><td>19</td><td>Nusa Tenggara Timur</td><td>174,900.07</td><td>183,091.99</td><td>184,698.89</td><td>168,727.24</td><td>212,089.62</td><td>25.7%</td></tr>
<tr><td>20</td><td>Kalimantan Barat</td><td>223,166.00</td><td>241,479.00</td><td>224,069.00</td><td>247,207.72</td><td>263,551.49</td><td>6.61%</td></tr>
<tr><td>21</td><td>Kalimantan Tengah</td><td>125,870.05</td><td>108,226.75</td><td>101,580.30</td><td>111,016.13</td><td>97,151.49</td><td>-12.49%</td></tr>
<tr><td>22</td><td>Kalimantan Selatan</td><td>254,263.59</td><td>214,908.91</td><td>214,283.82</td><td>246,112.42</td><td>290,332.45</td><td>17.97%</td></tr>
<tr><td>23</td><td>Kalimantan Timur</td><td>66,269.46</td><td>64,970.01</td><td>57,082.00</td><td>63,041.76</td><td>66,517.91</td><td>5.51%</td></tr>
<tr><td>24</td><td>Kalimantan Utara</td><td>8,880.83</td><td>8,604.19</td><td>6,500.00</td><td>8,282.06</td><td>10,209.52</td><td>23.27%</td></tr>
<tr><td>25</td><td>Sulawesi Utara</td><td>59,182.00</td><td>58,196.00</td><td>54,562.95</td><td>59,121.96</td><td>59,912.14</td><td>1.34%</td></tr>
<tr><td>26</td><td>Sulawesi Tengah</td><td>182,186.62</td><td>168,993.18</td><td>177,699.03</td><td>172,606.22</td><td>198,289.64</td><td>14.88%</td></tr>
<tr><td>27</td><td>Sulawesi Selatan</td><td>985,158.23</td><td>1,038,084.29</td><td>967,790.21</td><td>951,308.60</td><td>1,041,700.68</td><td>9.5%</td></tr>
<tr><td>28</td><td>Sulawesi Tenggara</td><td>127,517.29</td><td>118,259.00</td><td>113,930.26</td><td>129,999.61</td><td>151,810.91</td><td>16.78%</td></tr>
<tr><td>29</td><td>Gorontalo</td><td>48,713.50</td><td>46,823.47</td><td>49,610.47</td><td>46,952.15</td><td>53,896.54</td><td>14.79%</td></tr>
<tr><td>30</td><td>Sulawesi Barat</td><td>59,763.18</td><td>69,323.95</td><td>58,606.67</td><td>63,181.59</td><td>74,097.88</td><td>17.28%</td></tr>
<tr><td>31</td><td>Maluku</td><td>28,319.75</td><td>23,987.82</td><td>22,636.68</td><td>23,947.35</td><td>24,322.69</td><td>1.57%</td></tr>
<tr><td>32</td><td>Maluku Utara</td><td>7,782.00</td><td>6,416.00</td><td>7,709.00</td><td>9,366.71</td><td>6,655.16</td><td>-28.95%</td></tr>
<tr><td>33</td><td>Papua Barat</td><td>6,414.94</td><td>5,461.00</td><td>5,006.27</td><td>5,121.13</td><td>2,663.07</td><td>-48%</td></tr>
<tr><td>34</td><td>Papua Barat Daya</td><td>0.00</td><td>0.00</td><td>580.00</td><td>363.87</td><td>310.47</td><td>-14.68%</td></tr>
<tr><td>35</td><td>Papua</td><td>64,984.90</td><td>49,741.91</td><td>840.18</td><td>1,068.57</td><td>432.47</td><td>-59.53%</td></tr>
<tr><td>36</td><td>Papua Selatan</td><td>0.00</td><td>0.00</td><td>44,808.00</td><td>47,168.57</td><td>79,433.92</td><td>68.4%</td></tr>
<tr><td>37</td><td>Papua Tengah</td><td>0.00</td><td>0.00</td><td>2,094.00</td><td>1,436.12</td><td>999.22</td><td>-30.42%</td></tr>
<tr><td>38</td><td>Papua Pegunungan</td><td>0.00</td><td>0.00</td><td>14.00</td><td>9.66</td><td>78.73</td><td>715.01%</td></tr>
</tbody>
</table>
"""

# ── KOORDINAT PER PROVINSI ─────────────────────────────
PROVINSI_COORDS = {
    "Aceh":                           {"lat":  4.69,  "lon":  96.75},
    "Sumatera Utara":                 {"lat":  2.10,  "lon":  99.54},
    "Sumatera Barat":                 {"lat": -0.74,  "lon": 100.48},
    "Riau":                           {"lat":  0.29,  "lon": 101.70},
    "Jambi":                          {"lat": -1.48,  "lon": 102.44},
    "Sumatera Selatan":               {"lat": -3.32,  "lon": 104.91},
    "Bengkulu":                       {"lat": -3.79,  "lon": 102.26},
    "Lampung":                        {"lat": -4.56,  "lon": 105.41},
    "Kepulauan Bangka Belitung":      {"lat": -2.74,  "lon": 106.44},
    "Kepulauan Riau":                 {"lat":  3.95,  "lon": 108.14},
    "Daerah Khusus Ibukota Jakarta":  {"lat": -6.20,  "lon": 106.82},
    "Jawa Barat":                     {"lat": -6.90,  "lon": 107.60},
    "Jawa Tengah":                    {"lat": -7.15,  "lon": 110.14},
    "Daerah Istimewa Yogyakarta":     {"lat": -7.80,  "lon": 110.37},
    "Jawa Timur":                     {"lat": -7.54,  "lon": 112.24},
    "Banten":                         {"lat": -6.40,  "lon": 106.48},
    "Bali":                           {"lat": -8.34,  "lon": 115.09},
    "Nusa Tenggara Barat":            {"lat": -8.65,  "lon": 117.36},
    "Nusa Tenggara Timur":            {"lat": -8.66,  "lon": 121.08},
    "Kalimantan Barat":               {"lat":  0.02,  "lon": 109.34},
    "Kalimantan Tengah":              {"lat": -1.68,  "lon": 113.38},
    "Kalimantan Selatan":             {"lat": -3.09,  "lon": 115.28},
    "Kalimantan Timur":               {"lat":  0.54,  "lon": 116.33},
    "Kalimantan Utara":               {"lat":  3.07,  "lon": 116.04},
    "Sulawesi Utara":                 {"lat":  0.63,  "lon": 123.97},
    "Sulawesi Tengah":                {"lat": -1.43,  "lon": 121.45},
    "Sulawesi Selatan":               {"lat": -3.66,  "lon": 119.97},
    "Sulawesi Tenggara":              {"lat": -3.97,  "lon": 122.51},
    "Gorontalo":                      {"lat":  0.54,  "lon": 123.06},
    "Sulawesi Barat":                 {"lat": -2.84,  "lon": 119.23},
    "Maluku":                         {"lat": -3.24,  "lon": 130.14},
    "Maluku Utara":                   {"lat":  1.57,  "lon": 127.81},
    "Papua Barat":                    {"lat": -1.33,  "lon": 133.17},
    "Papua Barat Daya":               {"lat": -1.50,  "lon": 132.00},
    "Papua":                          {"lat": -4.27,  "lon": 138.08},
    "Papua Selatan":                  {"lat": -7.00,  "lon": 139.00},
    "Papua Tengah":                   {"lat": -3.50,  "lon": 136.50},
    "Papua Pegunungan":               {"lat": -4.00,  "lon": 138.50},
}

# Tahun El Niño & La Niña untuk variasi iklim antar tahun
EL_NINO_YEARS = {2023}
LA_NINA_YEARS = {2021, 2022}

# Baseline yield nasional Kementan (untuk hitung risk_level)
NASIONAL_YIELD_BASELINE = 5.2


# ── PARSE HTML ─────────────────────────────────────────
def parse_table(html: str, col_prefix: str) -> pd.DataFrame:
    """Parse HTML table Kementan menjadi DataFrame long format."""
    df = pd.read_html(StringIO(html))[0]
    df.columns = ["no", "provinsi",
                  f"{col_prefix}_2021", f"{col_prefix}_2022",
                  f"{col_prefix}_2023", f"{col_prefix}_2024",
                  f"{col_prefix}_2025", "pertumbuhan"]

    # Hapus baris total & header duplikat
    df = df[~df["provinsi"].astype(str).str.contains("INDONESIA|Provinsi", na=True)]
    df = df[df["provinsi"].notna()].copy()

    for col in [c for c in df.columns if col_prefix in c]:
        df[col] = pd.to_numeric(
            df[col].astype(str).str.replace(",", "").str.strip(),
            errors="coerce"
        ).fillna(0)

    return df[["provinsi"] + [c for c in df.columns if col_prefix in c]].reset_index(drop=True)


def build_training_rows(df_prod: pd.DataFrame, df_luas: pd.DataFrame,
                        climate_map: dict, rng: np.random.Generator) -> list[dict]:
    """
    Gabungkan produksi + luas panen nyata + iklim NASA POWER
    menjadi baris training yang realistis.

    yield_ton_per_ha = produksi_ton / luas_panen_ha (data nyata Kementan)
    → Hasilnya realistis ~3.5–6.5 ton/ha sesuai kondisi lapangan
    """
    tahun_list = [2021, 2022, 2023, 2024, 2025]
    rows = []

    # Gabung produksi & luas panen per provinsi
    merged = df_prod.merge(df_luas, on="provinsi", suffixes=("_prod", "_luas"))

    for _, row in merged.iterrows():
        provinsi = row["provinsi"]
        coords   = PROVINSI_COORDS.get(provinsi)
        climate  = climate_map.get(provinsi)
        if not coords or not climate:
            print(f"   ⚠️  Skip {provinsi} — tidak ada koordinat/iklim")
            continue

        base_temp = climate["temperature_c"]
        base_rain = climate["rainfall_mm"]
        base_rad  = climate["solar_radiation"]

        for tahun in tahun_list:
            produksi  = row.get(f"prod_{tahun}", 0)
            luas_panen = row.get(f"luas_{tahun}", 0)

            # Skip jika salah satu nol
            if produksi <= 0 or luas_panen <= 0:
                continue

            # ── YIELD: langsung dari data Kementan (bukan estimasi) ───
            yield_ton_per_ha = round(produksi / luas_panen, 2)
            # Clamp ke range realistis padi Indonesia: 2.5–7.5 ton/ha
            yield_ton_per_ha = round(max(2.5, min(7.5, yield_ton_per_ha)), 2)

            # ── HARVEST DAYS: variasi per tahun (El Niño / La Niña)
            if tahun in EL_NINO_YEARS:
                hd_base = 115   # El Niño → panen lebih lambat
            elif tahun in LA_NINA_YEARS:
                hd_base = 105   # La Niña → panen lebih cepat
            else:
                hd_base = 110
            harvest_days = int(rng.normal(hd_base, 8))
            harvest_days = max(90, min(130, harvest_days))

            # ── LAND AREA: skala petani (0.3–2.5 ha) ────────────
            # Rata-rata lahan petani Indonesia: ~0.3–1.5 ha
            land_area_ha = round(float(rng.uniform(0.3, 2.5)), 1)

            # ── IKLIM: variasi antar tahun (El Niño / La Niña) ──
            if tahun in EL_NINO_YEARS:
                temp_adj = rng.uniform(0.8,  1.8)    # lebih panas
                rain_adj = rng.uniform(0.70, 0.85)   # lebih kering
                rad_adj  = rng.uniform(1.05, 1.15)   # lebih cerah
            elif tahun in LA_NINA_YEARS:
                temp_adj = rng.uniform(-0.5, 0.3)
                rain_adj = rng.uniform(1.10, 1.25)   # lebih basah
                rad_adj  = rng.uniform(0.90, 0.98)
            else:
                temp_adj = rng.uniform(-0.3, 0.8)
                rain_adj = rng.uniform(0.92, 1.08)
                rad_adj  = rng.uniform(0.95, 1.05)

            temperature_c   = round(max(20.0, min(35.0, base_temp + temp_adj)), 1)
            rainfall_mm     = round(max(30.0, base_rain * rain_adj), 1)
            solar_radiation = round(max(100.0, base_rad * rad_adj), 1)

            # ── NDVI: estimasi dari musim + pengaruh El Niño ─────
            ndvi_base = estimate_ndvi_from_season(coords["lat"], coords["lon"], "padi")
            if tahun in EL_NINO_YEARS:
                ndvi = round(ndvi_base - rng.uniform(0.05, 0.12), 3)
            elif tahun in LA_NINA_YEARS:
                ndvi = round(ndvi_base + rng.uniform(0.01, 0.05), 3)
            else:
                ndvi = round(ndvi_base + rng.uniform(-0.03, 0.04), 3)
            ndvi = round(max(0.30, min(0.90, ndvi)), 3)

            # ── RISK: dari yield vs baseline nasional ────────────
            ratio = yield_ton_per_ha / NASIONAL_YIELD_BASELINE
            if ratio >= 0.90:
                risk_level = "low"
            elif ratio >= 0.70:
                risk_level = "medium"
            else:
                risk_level = "high"

            rows.append({
                "ndvi":              ndvi,
                "rainfall_mm":       rainfall_mm,
                "temperature_c":     temperature_c,
                "solar_radiation":   solar_radiation,
                "land_area_ha":      land_area_ha,
                "crop_type":         "padi",
                "harvest_days":      harvest_days,
                "yield_ton_per_ha":  yield_ton_per_ha,
                "risk_level":        risk_level,
                "provinsi":          provinsi,
                "tahun":             tahun,
                "produksi_ton":      produksi,
                "luas_panen_ha":     luas_panen,   # data nyata Kementan
                "data_source":       "kementan",
            })

    return rows


async def main():
    print("=" * 62)
    print("  🌾 PanenCerdas — Parse Kementan Produksi + Luas Panen Padi")
    print("=" * 62)

    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    # Step 1: Parse kedua tabel Kementan
    print("\n📋 Parsing tabel Kementan...")
    df_prod = parse_table(KEMENTAN_PRODUKSI_HTML, "prod")
    df_luas = parse_table(KEMENTAN_LUAS_HTML,     "luas")
    print(f"   Produksi : {len(df_prod)} provinsi")
    print(f"   Luas panen: {len(df_luas)} provinsi")

    # Cek kecocokan provinsi
    prod_set = set(df_prod["provinsi"])
    luas_set = set(df_luas["provinsi"])
    mismatch = prod_set.symmetric_difference(luas_set)
    if mismatch:
        print(f"   ⚠️  Provinsi tidak cocok: {mismatch}")

    # Step 2: Fetch iklim NASA POWER
    print(f"\n🌍 Fetching NASA POWER ({len(PROVINSI_COORDS)} provinsi)...")
    print("   (mungkin 3–5 menit)\n")

    tasks   = {
        prov: fetch_climate_data(coords["lat"], coords["lon"], days_back=90)
        for prov, coords in PROVINSI_COORDS.items()
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    climate_map = {}
    n_ok = n_fallback = 0
    for prov, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            climate_map[prov] = {
                "temperature_c": 27.0, "rainfall_mm": 150.0,
                "solar_radiation": 185.0, "data_source": "default_fallback",
            }
            n_fallback += 1
            print(f"   ⚠️  {prov}: fallback default")
        else:
            climate_map[prov] = result
            n_ok += 1
            print(f"   ✓  {prov}: {result['temperature_c']}°C "
                  f"| {result['rainfall_mm']}mm "
                  f"| {result['solar_radiation']} MJ/m² "
                  f"[{result.get('data_source','?')}]")

    print(f"\n   NASA POWER: {n_ok} berhasil, {n_fallback} pakai fallback")

    # Step 3: Konversi ke training rows
    print("\n🔄 Konversi ke format training...")
    rng  = np.random.default_rng(42)
    rows = build_training_rows(df_prod, df_luas, climate_map, rng)

    result_df = pd.DataFrame(rows)

    # Step 4: Validasi
    print("\n🔍 Validasi hasil:")
    print(f"   Total baris        : {len(result_df)}")
    print(f"   Provinsi           : {result_df['provinsi'].nunique()}")
    print(f"   Tahun              : {sorted(result_df['tahun'].unique().tolist())}")
    print(f"   yield min/mean/max : {result_df['yield_ton_per_ha'].min():.2f} / "
          f"{result_df['yield_ton_per_ha'].mean():.2f} / "
          f"{result_df['yield_ton_per_ha'].max():.2f} ton/ha")
    print(f"   harvest_days range : {result_df['harvest_days'].min()}–"
          f"{result_df['harvest_days'].max()} hari")
    print(f"   temperature range  : {result_df['temperature_c'].min():.1f}–"
          f"{result_df['temperature_c'].max():.1f} °C")
    print(f"   rainfall range     : {result_df['rainfall_mm'].min():.0f}–"
          f"{result_df['rainfall_mm'].max():.0f} mm")
    print(f"   land_area range    : {result_df['land_area_ha'].min():.1f}–"
          f"{result_df['land_area_ha'].max():.1f} ha")
    print(f"   risk breakdown     : {result_df['risk_level'].value_counts().to_dict()}")

    over = result_df[result_df["yield_ton_per_ha"] >= 7.5]
    if len(over):
        print(f"\n   ⚠️  {len(over)} baris di-clamp ke 7.5 ton/ha:")
        print(over[["provinsi", "tahun", "yield_ton_per_ha",
                     "produksi_ton", "luas_panen_ha"]].to_string(index=False))
    else:
        print(f"\n   ✅ Semua yield dalam range realistis (2.5–7.5 ton/ha)")

    # Step 5: Simpan
    output_path = data_dir / "kementan_produksi.csv"
    result_df.to_csv(output_path, index=False)
    print(f"\n✅ Tersimpan di: {output_path}")
    print("\n📋 Langkah selanjutnya:")
    print("   python train.py")


if __name__ == "__main__":
    asyncio.run(main())
