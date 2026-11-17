# HYDOVA — Flask Edition

Aplikasi HYDOVA dikonversi dari Streamlit menjadi **Flask + HTML + CSS + JavaScript**.

## Menjalankan di Windows

1. Ekstrak ZIP.
2. Buka folder `HYDOVA_Flask` di VS Code.
3. Buka terminal pada folder tersebut.
4. Buat virtual environment:

   ```bash
   python -m venv venv
   ```

5. Aktifkan environment:

   ```powershell
   .\venv\Scripts\Activate.ps1
   ```

   Jika PowerShell memblokir aktivasi, jalankan:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\venv\Scripts\Activate.ps1
   ```

6. Pasang dependensi:

   ```bash
   python -m pip install -r requirements.txt
   ```

   Catatan: paket GIS seperti GeoPandas dan Rasterio dapat memerlukan wheel yang sesuai dengan versi Python/Windows. Gunakan Python 3.11 atau 3.12 jika instalasi GIS bermasalah.

7. Jalankan:

   ```bash
   python app.py
   ```

8. Buka browser ke `http://127.0.0.1:5000`.

## Fitur

- Dashboard ringkasan data hujan dan grafik.
- Input manual, impor CSV/Excel, edit, hapus, reset, dan ekspor CSV.
- Analisis frekuensi Normal, Log Normal, Gumbel, dan Log Pearson III.
- Estimasi debit banjir Metode Rasional.
- GIS otomatis: batas geoBoundaries, elevasi Copernicus DEM melalui Open-Meteo, hujan historis Open-Meteo, peta Leaflet, ringkasan luas kelas, dan unduh GeoTIFF.
- Panduan penggunaan.

## Catatan GIS

Analisis GIS memerlukan internet dan dependensi GIS. Indeks yang dihasilkan adalah **screening potensi relatif**, bukan kedalaman genangan atau peta bahaya resmi. Hujan historis bersumber dari reanalisis/model Open-Meteo, bukan observasi langsung BMKG.

Database `hydova.db` dibuat otomatis saat aplikasi pertama dijalankan. Jangan hapus file tersebut jika ingin mempertahankan data observasi.
