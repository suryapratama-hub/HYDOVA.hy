document.addEventListener('DOMContentLoaded', async () => {
    const province = document.getElementById('province');
    if (!province) return;

    const area = document.getElementById('area');
    const status = document.getElementById('gis-status');
    const run = document.getElementById('run-gis');
    const start = document.getElementById('rain-start');
    const end = document.getElementById('rain-end');

    let map = null;
    let last = null;

    // =====================================
    // NAMA PROVINSI (Inggris -> Indonesia)
    // Nilai option tetap nama asli agar cocok dengan backend.
    // =====================================

    const PROVINSI_ID = {
        'Aceh': 'Aceh',
        'North Sumatra': 'Sumatera Utara',
        'West Sumatra': 'Sumatera Barat',
        'South Sumatra': 'Sumatera Selatan',
        'Riau': 'Riau',
        'Jambi': 'Jambi',
        'Bengkulu': 'Bengkulu',
        'Lampung': 'Lampung',
        'Bangka Belitung Islands': 'Kepulauan Bangka Belitung',
        'Riau Islands': 'Kepulauan Riau',
        'Jakarta': 'DKI Jakarta',
        'Jakarta Raya': 'DKI Jakarta',
        'West Java': 'Jawa Barat',
        'Central Java': 'Jawa Tengah',
        'East Java': 'Jawa Timur',
        'Banten': 'Banten',
        'Yogyakarta': 'DI Yogyakarta',
        'Special Region of Yogyakarta': 'DI Yogyakarta',
        'Bali': 'Bali',
        'West Nusa Tenggara': 'Nusa Tenggara Barat',
        'East Nusa Tenggara': 'Nusa Tenggara Timur',
        'West Kalimantan': 'Kalimantan Barat',
        'Central Kalimantan': 'Kalimantan Tengah',
        'South Kalimantan': 'Kalimantan Selatan',
        'East Kalimantan': 'Kalimantan Timur',
        'North Kalimantan': 'Kalimantan Utara',
        'North Sulawesi': 'Sulawesi Utara',
        'Central Sulawesi': 'Sulawesi Tengah',
        'South Sulawesi': 'Sulawesi Selatan',
        'Southeast Sulawesi': 'Sulawesi Tenggara',
        'West Sulawesi': 'Sulawesi Barat',
        'Gorontalo': 'Gorontalo',
        'Maluku': 'Maluku',
        'North Maluku': 'Maluku Utara',
        'Papua': 'Papua',
        'West Papua': 'Papua Barat',
        'South Papua': 'Papua Selatan',
        'Central Papua': 'Papua Tengah',
        'Highland Papua': 'Papua Pegunungan',
        'Southwest Papua': 'Papua Barat Daya'
    };

    const namaProvinsi = name => PROVINSI_ID[name] || name;

    // =====================================
    // FUNGSI BANTU
    // =====================================

    function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, char => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        })[char]);
    }

    async function fetchJson(url, options = {}) {
        const response = await fetch(url, options);

        let data;

        try {
            data = await response.json();
        } catch {
            throw new Error('Server memberikan respons yang tidak valid.');
        }

        if (!response.ok) {
            throw new Error(data.error || 'Permintaan gagal.');
        }

        return data;
    }

    function setStatus(message) {
        status.textContent = message;
    }

    function iso(date) {
        return date.toISOString().slice(0, 10);
    }

    function tableHtml(headers, rows) {
        return `
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr>
                            ${headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody>
                        ${rows.map(row => `
                            <tr>
                                ${row.map(value => `
                                    <td>${escapeHtml(value)}</td>
                                `).join('')}
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            </div>
        `;
    }

    // =====================================
    // TANGGAL HUJAN
    // =====================================

    const now = new Date();

    end.value = iso(new Date(
        now.getTime() - 7 * 86400000
    ));

    start.value = iso(new Date(
        now.getTime() - 36 * 86400000
    ));

    // =====================================
    // MEMUAT PROVINSI
    // =====================================

    async function loadProvinces() {
        province.disabled = true;
        province.innerHTML = '<option value="">Memuat provinsi...</option>';

        setStatus('Mengambil daftar provinsi Indonesia...');

        try {
            const names = await fetchJson('/api/regions?level=ADM1');

            if (!Array.isArray(names) || names.length === 0) {
                throw new Error('Daftar provinsi kosong dari server.');
            }

            province.innerHTML =
                '<option value="">Pilih provinsi</option>' +
                names
                    .slice()
                    .sort((a, b) => namaProvinsi(a).localeCompare(namaProvinsi(b), 'id'))
                    .map(name =>
                        `<option value="${escapeHtml(name)}">${escapeHtml(namaProvinsi(name))}</option>`
                    ).join('');

            province.disabled = false;

            setStatus(`${names.length} provinsi berhasil dimuat. Silakan pilih provinsi.`);

        } catch (error) {
            province.innerHTML =
                '<option value="">Gagal memuat provinsi</option>';

            setStatus('Gagal mengambil provinsi: ' + error.message);

            console.error('Province loading error:', error);
        }
    }

    // =====================================
    // MEMUAT KABUPATEN / KOTA
    // =====================================

    province.addEventListener('change', async () => {
        const selectedProvince = province.value;

        area.innerHTML =
            '<option value="Seluruh provinsi">Seluruh provinsi</option>';

        area.disabled = true;

        if (!selectedProvince) {
            area.disabled = false;
            setStatus('Silakan pilih provinsi.');
            return;
        }

        setStatus('Mengambil daftar kabupaten/kota...');

        try {
            const url =
                '/api/regions?level=ADM2&province=' +
                encodeURIComponent(selectedProvince);

            const names = await fetchJson(url);

            if (!Array.isArray(names)) {
                throw new Error('Format data kabupaten/kota tidak valid.');
            }

            area.innerHTML =
                '<option value="Seluruh provinsi">Seluruh provinsi</option>' +
                names.map(name =>
                    `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`
                ).join('');

            area.disabled = false;

            setStatus(
                `${names.length} kabupaten/kota berhasil dimuat untuk ${namaProvinsi(selectedProvince)}.`
            );

        } catch (error) {
            area.disabled = false;

            setStatus(
                'Gagal mengambil kabupaten/kota: ' + error.message
            );

            console.error('Area loading error:', error);
        }
    });

    // =====================================
    // MEMBUAT PETA GIS
    // =====================================

    run.addEventListener('click', async () => {

        if (!province.value) {
            setStatus('Pilih provinsi terlebih dahulu.');
            return;
        }

        if (!start.value || !end.value || start.value >= end.value) {
            setStatus('Periksa tanggal awal dan akhir data hujan.');
            return;
        }

        run.disabled = true;
        run.textContent = 'Memproses...';

        setStatus(
            'Mengunduh batas wilayah, data elevasi, dan hujan historis. Mohon tunggu...'
        );

        try {
            const data = await fetchJson('/api/gis-analysis', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    province: province.value,
                    area: area.value,
                    start: start.value,
                    end: end.value
                })
            });

            last = data;

            // =================================
            // MENAMPILKAN HASIL
            // =================================

            document.getElementById('gis-output')
                .classList.remove('hidden');

            document.getElementById('gis-area-title')
                .textContent = 'Hasil: ' +
                    data.area.replace(data.province, namaProvinsi(data.province));

            const samples = data.samples || [];

            const high = samples.filter(
                x => x['Kelas relatif'] === 'Tinggi'
            ).length;

            const mean = samples.length
                ? samples.reduce(
                    (total, item) => total + Number(item['Elevasi (m)']),
                    0
                ) / samples.length
                : 0;

            const metrics = [
                ['Sampel pembentuk area', samples.length],
                ['Hujan akumulasi', Number(data.rain_total).toFixed(1) + ' mm'],
                ['Elevasi rata-rata', mean.toFixed(1) + ' m'],
                ['Titik kelas tinggi', high]
            ];

            document.getElementById('gis-metrics').innerHTML =
                metrics.map(([label, value]) => `
                    <div class="metric">
                        <span>${escapeHtml(label)}</span>
                        <strong>${escapeHtml(value)}</strong>
                    </div>
                `).join('');

            // =================================
            // MEMBUAT PETA LEAFLET
            // =================================

            if (map) {
                map.remove();
                map = null;
            }

            if (!data.center || !data.boundary || !data.geojson) {
                throw new Error('Data geometri peta tidak lengkap.');
            }

            map = L.map('gis-map').setView(data.center, 10);

            L.tileLayer(
                'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                {
                    maxZoom: 19,
                    attribution: '&copy; OpenStreetMap contributors'
                }
            ).addTo(map);

            const colors = {
                Rendah: '#2d8a57',
                Sedang: '#e0a52b',
                Tinggi: '#c83e2b'
            };

            const garisWilayah = '#3a3a3a';

            // Poligon per wilayah dengan garis pemisah (bukan kotak grid).
            L.geoJSON(data.geojson, {
                style: feature => ({
                    color: garisWilayah,
                    weight: 1.5,
                    fillColor: colors[feature.properties.kelas] || '#888',
                    fillOpacity: 0.5
                }),

                onEachFeature: (feature, layer) => {
                    const p = feature.properties;

                    layer.bindTooltip(
                        `<strong>${escapeHtml(p.wilayah || '-')}</strong>` +
                        `<br>Kelas: ${escapeHtml(p.kelas)}` +
                        (p.indeks != null
                            ? `<br>Indeks: ${escapeHtml(p.indeks)}`
                            : '')
                    );

                    layer.on({
                        mouseover: e => e.target.setStyle({
                            weight: 3,
                            color: '#000'
                        }),
                        mouseout: e => e.target.setStyle({
                            weight: 1.5,
                            color: garisWilayah
                        })
                    });
                }
            }).addTo(map);

            const boundary = L.geoJSON(data.boundary, {
                style: {
                    color: '#c83e2b',
                    weight: 3,
                    fillOpacity: 0
                },
                interactive: false
            }).addTo(map);

            if (boundary.getBounds().isValid()) {
                map.fitBounds(boundary.getBounds(), {
                    padding: [20, 20]
                });
            }

            setTimeout(() => map.invalidateSize(), 200);

            // =================================
            // TABEL LUAS AREA
            // =================================

            document.getElementById('gis-area-table').innerHTML =
                tableHtml(
                    ['Kelas', 'Luas area (km²)'],
                    Object.entries(data.area_summary).map(
                        ([key, value]) => [
                            key,
                            Number(value).toFixed(2)
                        ]
                    )
                );

            // =================================
            // TABEL HUJAN
            // =================================

            document.getElementById('gis-rain-table').innerHTML =
                tableHtml(
                    ['Tanggal', 'Hujan (mm)'],
                    data.rain.map(item => [
                        item.Tanggal,
                        Number(item['Hujan (mm)']).toFixed(2)
                    ])
                );

            // =================================
            // SUMBER DATA
            // =================================

            document.getElementById('gis-sources').textContent =
                `Batas: geoBoundaries (${data.boundary_year});
                sumber: ${data.boundary_source};
                lisensi: ${data.boundary_license}.
                Elevasi: Copernicus DEM GLO-90 melalui Open-Meteo.
                Hujan: reanalisis Open-Meteo pada titik representatif wilayah.`;

            setStatus('Analisis GIS berhasil dibuat.');

        } catch (error) {
            setStatus('Pembuatan peta gagal: ' + error.message);

            console.error('GIS analysis error:', error);

        } finally {
            run.disabled = false;
            run.textContent = 'Buat peta otomatis';
        }
    });

    // =====================================
    // DOWNLOAD CSV
    // =====================================

    function downloadCsv(filename, rows) {
        if (!rows || rows.length === 0) {
            setStatus('Tidak ada data untuk diunduh.');
            return;
        }

        const keys = Object.keys(rows[0]);

        const csv = [
            keys.join(','),
            ...rows.map(row =>
                keys.map(key =>
                    '"' + String(row[key] ?? '').replaceAll('"', '""') + '"'
                ).join(',')
            )
        ].join('\n');

        const blob = new Blob(
            ['\ufeff' + csv],
            { type: 'text/csv;charset=utf-8;' }
        );

        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');

        link.href = url;
        link.download = filename;
        link.click();

        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    document.getElementById('download-samples')
        ?.addEventListener('click', () => {
            downloadCsv(
                'HYDOVA_titik_analisis.csv',
                last?.samples || []
            );
        });

    document.getElementById('download-rain')
        ?.addEventListener('click', () => {
            downloadCsv(
                'HYDOVA_hujan_historis.csv',
                last?.rain || []
            );
        });

    // =====================================
    // JALANKAN PEMUATAN PROVINSI
    // =====================================

    await loadProvinces();

});