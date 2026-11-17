from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from pathlib import Path
from datetime import date, timedelta
import io, json, os, sqlite3, tempfile, zipfile
import requests
import numpy as np
import pandas as pd
from scipy import stats
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "ganti-dengan-secret-key-acak-anda")
DB_PATH = Path(__file__).with_name("hydova.db")
RETURN_PERIODS = [2, 5, 10, 25, 50, 100]
GEOB_API = "https://www.geoboundaries.org/api/current/gbOpen/IDN/{adm}/"
OPEN_METEO_ELEVATION = "https://api.open-meteo.com/v1/elevation"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
ALLOWED_DATA = {"csv", "xlsx", "xls"}

# Cache batas wilayah agar tidak diunduh ulang pada setiap analisis.
_GEO_CACHE = {}

# Modul GIS bersifat opsional untuk menjaga dashboard tetap dapat berjalan.
try:
    import geopandas as gpd
    from shapely.geometry import Point, Polygon, box, mapping, shape
    from shapely.ops import unary_union, triangulate
    from scipy.interpolate import griddata
    from pyproj import Geod
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.features import geometry_mask as raster_geometry_mask, shapes as raster_shapes
    GIS_AVAILABLE = True
    GIS_ERROR = None
except Exception as exc:
    GIS_AVAILABLE = False
    GIS_ERROR = str(exc)


# ----------------------------- DATABASE -----------------------------
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS rainfall (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL, station TEXT NOT NULL,
            rainfall_mm REAL NOT NULL CHECK(rainfall_mm >= 0),
            UNIQUE(date, station))""")
        conn.commit()

def read_rainfall():
    with get_connection() as conn:
        df = pd.read_sql_query("SELECT id,date,station,rainfall_mm FROM rainfall ORDER BY date", conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["rainfall_mm"] = pd.to_numeric(df["rainfall_mm"], errors="coerce")
        df = df.dropna(subset=["date", "station", "rainfall_mm"])
    return df

def insert_rainfall(df):
    required = {"date", "station", "rainfall_mm"}
    if not required.issubset(df.columns):
        raise ValueError("Kolom wajib: date, station, rainfall_mm.")
    rows = []
    for _, row in df.iterrows():
        dt = pd.to_datetime(row["date"], errors="coerce")
        station = str(row["station"]).strip()
        rain = pd.to_numeric(row["rainfall_mm"], errors="coerce")
        if pd.isna(dt) or not station or pd.isna(rain) or float(rain) < 0:
            continue
        rows.append((dt.strftime("%Y-%m-%d"), station, float(rain)))
    if not rows:
        raise ValueError("Tidak ada baris data valid.")
    with get_connection() as conn:
        conn.executemany("""INSERT INTO rainfall(date,station,rainfall_mm) VALUES (?,?,?)
        ON CONFLICT(date,station) DO UPDATE SET rainfall_mm=excluded.rainfall_mm""", rows)
        conn.commit()
    return len(rows)

def validate_rainfall(raw):
    aliases = {
        "tanggal":"date", "date":"date", "datetime":"date",
        "stasiun":"station", "nama stasiun":"station", "wilayah":"station",
        "station":"station", "rain station":"station",
        "curah hujan":"rainfall_mm", "curah hujan (mm)":"rainfall_mm",
        "rainfall":"rainfall_mm", "rainfall mm":"rainfall_mm",
        "rainfall (mm)":"rainfall_mm", "hujan (mm)":"rainfall_mm"
    }
    df = raw.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    rename = {}
    for col in df.columns:
        normalized = " ".join(col.replace("_", " ").split())
        if normalized in aliases:
            rename[col] = aliases[normalized]
    df = df.rename(columns=rename)
    missing = {"date","station","rainfall_mm"} - set(df.columns)
    if missing:
        raise ValueError("Kolom tidak ditemukan: " + ", ".join(sorted(missing)) +
                         ". Gunakan tanggal/date, stasiun/station, curah hujan/rainfall_mm.")
    df = df[["date","station","rainfall_mm"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["station"] = df["station"].astype(str).str.strip()
    df["rainfall_mm"] = pd.to_numeric(df["rainfall_mm"], errors="coerce")
    invalid = df["date"].isna() | df["station"].isin(["","nan","None"]) | df["rainfall_mm"].isna() | (df["rainfall_mm"] < 0)
    skipped = int(invalid.sum())
    clean = df.loc[~invalid].copy()
    clean["date"] = clean["date"].dt.strftime("%Y-%m-%d")
    clean = clean.drop_duplicates(["date","station"], keep="last")
    if clean.empty:
        raise ValueError("Tidak ada data valid untuk diimpor.")
    return clean, skipped

def annual_maxima(df, station):
    if df.empty:
        return pd.Series(dtype=float)
    subset = df.copy()
    if station != "Semua stasiun":
        subset = subset[subset["station"] == station]
    if subset.empty:
        return pd.Series(dtype=float)
    subset["year"] = pd.to_datetime(subset["date"]).dt.year
    return subset.groupby("year")["rainfall_mm"].max().sort_index()

# -------------------------- FREKUENSI & DEBIT --------------------------
def calculate_frequency(values, distribution):
    x = np.asarray(values, dtype=float)
    x = x[np.isfinite(x)]
    if len(x) < 5:
        raise ValueError("Minimal 5 tahun data maksimum tahunan diperlukan; lebih baik 10–30 tahun.")
    if np.any(x < 0) or np.std(x, ddof=1) == 0:
        raise ValueError("Data tidak valid atau seluruh maksimum tahunan sama.")
    if distribution == "Normal":
        mu, sd = stats.norm.fit(x); params = {"Rata-rata": float(mu), "Simpangan baku": float(sd)}
        dist = stats.norm(loc=mu, scale=sd)
    elif distribution == "Log Normal":
        if np.any(x <= 0): raise ValueError("Log Normal membutuhkan data lebih besar dari 0.")
        shape_, loc, scale = stats.lognorm.fit(x, floc=0)
        params = {"Bentuk (sigma log)":float(shape_), "Skala":float(scale)}
        dist = stats.lognorm(s=shape_, loc=loc, scale=scale)
    elif distribution == "Gumbel":
        loc, scale = stats.gumbel_r.fit(x)
        params = {"Lokasi":float(loc), "Skala":float(scale)}
        dist = stats.gumbel_r(loc=loc, scale=scale)
    elif distribution == "Log Pearson III":
        if np.any(x <= 0): raise ValueError("Log Pearson III membutuhkan data lebih besar dari 0.")
        lx = np.log10(x); shape_, loc, scale = stats.pearson3.fit(lx)
        params = {"Rata-rata log":float(np.mean(lx)), "Simpangan baku log":float(np.std(lx,ddof=1)),
                  "Koefisien kemencengan log":float(stats.skew(lx,bias=False))}
        log_dist = stats.pearson3(skew=shape_, loc=loc, scale=scale); dist = None
    else: raise ValueError("Distribusi tidak dikenal.")
    rows=[]
    for t in RETURN_PERIODS:
        p=1-1/t
        q=float(10**log_dist.ppf(p)) if distribution=="Log Pearson III" else float(dist.ppf(p))
        rows.append({"Periode ulang (tahun)":t,"Peluang tidak terlampaui":p,"Hujan rencana (mm)":q})
    return params, pd.DataFrame(rows)

def goodness_of_fit(values, distribution):
    x=np.asarray(values,dtype=float); x=x[np.isfinite(x)]
    if len(x)<5: return {"Status":"Minimal 5 data diperlukan"}
    try:
        if distribution=="Normal":
            pars=stats.norm.fit(x); cdf=lambda z:stats.norm.cdf(z,*pars)
        elif distribution=="Log Normal":
            if np.any(x<=0): return {"Status":"Data harus > 0 untuk Log Normal"}
            pars=stats.lognorm.fit(x,floc=0); cdf=lambda z:stats.lognorm.cdf(z,*pars)
        elif distribution=="Gumbel":
            pars=stats.gumbel_r.fit(x); cdf=lambda z:stats.gumbel_r.cdf(z,*pars)
        else:
            if np.any(x<=0): return {"Status":"Data harus > 0 untuk Log Pearson III"}
            pars=stats.pearson3.fit(np.log10(x)); cdf=lambda z:stats.pearson3.cdf(np.log10(z),*pars)
        ks=stats.kstest(x,cdf)
        return {"K-S statistik":float(ks.statistic),"K-S p-value (indikatif)":float(ks.pvalue),
                "Catatan":"P-value K-S standar bersifat indikatif karena parameter distribusi diestimasi dari sampel."}
    except Exception as exc: return {"Status":f"Uji tidak dapat dihitung: {exc}"}

def rational_method(area_km2, runoff_c, intensity_mm_hr):
    return 0.278*float(runoff_c)*float(intensity_mm_hr)*float(area_km2)

def rational_table(area, runoff, frequency_table, duration):
    result=frequency_table.copy()
    result["Intensitas (mm/jam)"]=result["Hujan rencana (mm)"]/float(duration)
    result["Debit (m³/det)"]=result["Intensitas (mm/jam)"].apply(lambda i:rational_method(area,runoff,i))
    return result

# ------------------------------- GIS ---------------------------------
def fetch_geoboundaries(adm_level):
    if not GIS_AVAILABLE: raise RuntimeError("Dependensi GIS belum tersedia: "+str(GIS_ERROR))
    if adm_level not in ("ADM1","ADM2","ADM3"): raise ValueError("Level administrasi tidak didukung.")
    if adm_level in _GEO_CACHE: return _GEO_CACHE[adm_level]
    meta=requests.get(GEOB_API.format(adm=adm_level),timeout=45); meta.raise_for_status(); metadata=meta.json()
    url=metadata.get("simplifiedGeometryGeoJSON") or metadata.get("gjDownloadURL")
    if not url: raise RuntimeError("Tautan GeoJSON tidak tersedia.")
    response=requests.get(url,timeout=180); response.raise_for_status()
    gdf=gpd.read_file(io.BytesIO(response.content))
    if gdf.empty: raise RuntimeError("Data batas wilayah kosong.")
    if gdf.crs is None: gdf=gdf.set_crs("EPSG:4326")
    else: gdf=gdf.to_crs("EPSG:4326")
    name_col=next((c for c in ["shapeName","NAME_1","NAME_2","name","NAME"] if c in gdf.columns),None)
    if name_col is None: raise RuntimeError("Kolom nama wilayah tidak ditemukan.")
    gdf=gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["_hydova_name"]=gdf[name_col].astype(str).str.strip()
    _GEO_CACHE[adm_level]=(gdf,metadata)
    return gdf,metadata

def fetch_elevation_points(latitudes,longitudes):
    values=[]
    for start in range(0,len(latitudes),100):
        response=requests.get(OPEN_METEO_ELEVATION,params={
            "latitude":",".join(f"{v:.5f}" for v in latitudes[start:start+100]),
            "longitude":",".join(f"{v:.5f}" for v in longitudes[start:start+100])},timeout=60)
        response.raise_for_status(); values.extend(response.json().get("elevation",[]))
    if len(values)!=len(latitudes): raise RuntimeError("Jumlah data elevasi tidak sesuai.")
    return [float(v) if v is not None else float("nan") for v in values]

def fetch_historical_rain(lat,lon,start_date,end_date):
    response=requests.get(OPEN_METEO_ARCHIVE,params={"latitude":float(lat),"longitude":float(lon),
        "start_date":str(start_date),"end_date":str(end_date),"daily":"precipitation_sum",
        "timezone":"Asia/Jakarta"},timeout=90)
    response.raise_for_status(); payload=response.json(); daily=payload.get("daily",{})
    dates=daily.get("time",[]); rain=daily.get("precipitation_sum",[])
    if not dates or not rain: raise RuntimeError("API tidak mengembalikan data hujan.")
    frame=pd.DataFrame({"Tanggal":pd.to_datetime(dates),"Hujan (mm)":rain})
    frame["Hujan (mm)"]=pd.to_numeric(frame["Hujan (mm)"],errors="coerce")
    frame=frame.dropna(subset=["Hujan (mm)"])
    return frame,payload.get("timezone","Asia/Jakarta"),payload.get("latitude",lat),payload.get("longitude",lon)

def create_analysis_grid(polygon,rows=12,cols=12,max_points=64):
    minx,miny,maxx,maxy=polygon.bounds
    points=[]
    for r,lat in enumerate(np.linspace(miny,maxy,rows)):
        for c,lon in enumerate(np.linspace(minx,maxx,cols)):
            p=Point(float(lon),float(lat))
            if polygon.covers(p): points.append({"row":r,"col":c,"latitude":float(lat),"longitude":float(lon)})
    if len(points)>max_points:
        points=[points[i] for i in np.linspace(0,len(points)-1,max_points,dtype=int)]
    if len(points)<3:
        candidates=[tri.representative_point() for tri in triangulate(polygon) if polygon.covers(tri.representative_point())]
        candidates.append(polygon.representative_point()); unique=[]; seen=set()
        for p in candidates:
            key=(round(p.x,8),round(p.y,8))
            if key not in seen: seen.add(key); unique.append(p)
        points=[{"row":i,"col":0,"latitude":float(p.y),"longitude":float(p.x)} for i,p in enumerate(unique[:max_points])]
    if len(points)<3: raise ValueError("Wilayah terlalu kecil untuk minimal tiga titik sampling.")
    return points

def calculate_relative_index(points,elevations,rainfall_total):
    frame=pd.DataFrame(points).copy()
    frame["Elevasi (m)"]=pd.to_numeric(pd.Series(elevations),errors="coerce").to_numpy()
    frame=frame.replace([np.inf,-np.inf],np.nan).dropna(subset=["latitude","longitude","Elevasi (m)"]).reset_index(drop=True)
    if len(frame)<3: raise ValueError("Titik elevasi valid kurang dari tiga.")
    z=frame["Elevasi (m)"].to_numpy(float); emin,emax=float(z.min()),float(z.max())
    low=np.full(len(frame),.5) if emax-emin<.01 else 1-np.clip((z-emin)/(emax-emin),0,1)
    lat=frame["latitude"].to_numpy(float); lon=frame["longitude"].to_numpy(float)
    coords=np.column_stack((lon*111320*np.cos(np.radians(lat)),lat*110574))
    relief=np.zeros(len(frame))
    for i in range(len(frame)):
        distances=np.sqrt(((coords-coords[i])**2).sum(axis=1)); distances[i]=np.inf
        nearest=np.argsort(distances)[:min(4,len(frame)-1)]
        nearest=[j for j in nearest if distances[j]>0]
        if nearest: relief[i]=float(np.mean([abs(z[j]-z[i])/max(1,distances[j]) for j in nearest]))
    frame["Relief lokal (m/m)"]=relief
    frame["Hujan periode (mm)"]=float(rainfall_total)
    frame["Indeks potensi relatif"]=(.40*low+.35*(1-np.clip(relief/.15,0,1))+.25*np.clip(float(rainfall_total)/300,0,1))*100
    frame["Kelas relatif"]=pd.cut(frame["Indeks potensi relatif"],[-.001,35,65,100.001],
        labels=["Rendah","Sedang","Tinggi"],include_lowest=True).astype(str)
    return frame

def make_index_geotiff(samples,polygon,width=240,height=240):
    minx,miny,maxx,maxy=polygon.bounds
    transform=from_bounds(minx,miny,maxx,maxy,width,height)
    xs=minx+(np.arange(width)+.5)*((maxx-minx)/width)
    ys=maxy-(np.arange(height)+.5)*((maxy-miny)/height)
    gx,gy=np.meshgrid(xs,ys)
    clean=samples.dropna(subset=["longitude","latitude","Indeks potensi relatif"]).drop_duplicates(["longitude","latitude"])
    coords=clean[["longitude","latitude"]].to_numpy(float); vals=clean["Indeks potensi relatif"].to_numpy(float)
    if len(coords)<1: raise ValueError("Tidak ada titik valid.")
    method="linear" if len(coords)>=3 else "nearest"
    try: grid=griddata(coords,vals,(gx,gy),method=method)
    except Exception: grid=griddata(coords,vals,(gx,gy),method="nearest")
    nearest=griddata(coords,vals,(gx,gy),method="nearest"); grid=np.where(np.isfinite(grid),grid,nearest)
    inside=raster_geometry_mask([mapping(polygon)],out_shape=(height,width),transform=transform,invert=True)
    grid=np.where(inside & np.isfinite(grid),grid,-9999).astype("float32")
    output=io.BytesIO()
    with rasterio.io.MemoryFile() as mem:
        with mem.open(driver="GTiff",height=height,width=width,count=1,dtype="float32",
            crs="EPSG:4326",transform=transform,nodata=-9999,compress="lzw") as dst:
            dst.write(grid,1)
            dst.set_band_description(1,"Interpolated relative susceptibility screening index 0-100")
        output.write(mem.read())
    return output.getvalue()

def _kelas(value):
    return "Rendah" if value<=35 else ("Sedang" if value<=65 else "Tinggi")

def make_index_area_geojson(polygon,samples,rows=28,cols=28):
    """Versi lama berbasis kotak grid. Dipertahankan untuk kompatibilitas."""
    clean=samples.dropna(subset=["longitude","latitude","Indeks potensi relatif"]).drop_duplicates(["longitude","latitude"])
    if len(clean)<3: raise ValueError("Minimal tiga titik valid diperlukan.")
    minx,miny,maxx,maxy=polygon.bounds; dx=(maxx-minx)/cols; dy=(maxy-miny)/rows
    coords=clean[["longitude","latitude"]].to_numpy(float); vals=clean["Indeks potensi relatif"].to_numpy(float)
    xc=minx+(np.arange(cols)+.5)*dx; yc=miny+(np.arange(rows)+.5)*dy
    gx,gy=np.meshgrid(xc,yc)
    try: grid=griddata(coords,vals,(gx,gy),method="linear")
    except Exception: grid=np.full(gx.shape,np.nan)
    nearest=griddata(coords,vals,(gx,gy),method="nearest"); grid=np.clip(np.where(np.isfinite(grid),grid,nearest),0,100)
    features=[]
    for r in range(rows):
        for c in range(cols):
            cell=box(minx+c*dx,miny+r*dy,minx+(c+1)*dx,miny+(r+1)*dy).intersection(polygon)
            if cell.is_empty or cell.area<=0: continue
            value=float(grid[r,c])
            features.append({"type":"Feature","properties":{"indeks":round(value,2),"kelas":_kelas(value)},"geometry":mapping(cell)})
    return {"type":"FeatureCollection","features":features}

def _chaikin_ring(coords,iterations=3):
    """Haluskan cincin poligon tertutup dengan pemotongan sudut Chaikin."""
    pts=np.asarray(coords,dtype=float)
    if len(pts)<4: return pts
    for _ in range(iterations):
        q=0.75*pts[:-1]+0.25*pts[1:]; r=0.25*pts[:-1]+0.75*pts[1:]
        new=np.empty((len(q)*2,2)); new[0::2]=q; new[1::2]=r
        pts=np.vstack([new,new[0]])
    return pts

def _contour_zone_features(polygon,coords,vals,res=300):
    """Zona kelas halus mengikuti kontur indeks. Hanya memakai scipy, rasterio, dan shapely."""
    from scipy.ndimage import gaussian_filter
    minx,miny,maxx,maxy=polygon.bounds
    gx,gy=np.meshgrid(np.linspace(minx,maxx,res),np.linspace(maxy,miny,res))   # baris atas = lintang maks
    near=griddata(coords,vals,(gx,gy),method="nearest")
    lin=griddata(coords,vals,(gx,gy),method="linear")
    try: cub=griddata(coords,vals,(gx,gy),method="cubic")
    except Exception: cub=np.full(gx.shape,np.nan)
    grid=np.where(np.isfinite(cub),cub,np.where(np.isfinite(lin),lin,near))
    grid=np.clip(gaussian_filter(grid,sigma=max(3.0,res/60)),0,100)
    kelas_arr=np.where(grid<=35,0,np.where(grid<=65,1,2)).astype("uint8")
    transform=from_bounds(minx,miny,maxx,maxy,res,res)
    inside=raster_geometry_mask([mapping(polygon)],out_shape=(res,res),transform=transform,invert=True)
    px=max(maxx-minx,maxy-miny)/res
    features=[]
    for k,kelas in enumerate(["Rendah","Sedang","Tinggi"]):
        mask=(kelas_arr==k)&inside
        if not mask.any(): continue
        parts=[]
        for g,_ in raster_shapes(kelas_arr,mask=mask,transform=transform):
            pg=shape(g)
            if pg.area<4*px*px: continue                      # buang serpihan kecil
            ext=_chaikin_ring(pg.exterior.coords)
            holes=[_chaikin_ring(h.coords) for h in pg.interiors if Polygon(h).area>=4*px*px]
            parts.append(Polygon(ext,holes).buffer(0))
        if not parts: continue
        zona=unary_union(parts).buffer(px).intersection(polygon).simplify(px*0.3,preserve_topology=True)
        if zona.is_empty or zona.area<=0: continue
        features.append({"type":"Feature","properties":{"wilayah":"Zona "+kelas,"indeks":None,"kelas":kelas},
            "geometry":mapping(zona)})
    return features

def make_index_region_geojson(polygon,samples,regions=None,rows=60,cols=60):
    """Peta per wilayah administrasi (garis pemisah wilayah), bukan kotak grid.
    regions: GeoDataFrame batas wilayah (kecamatan/kabupaten) atau None untuk zona kelas."""
    clean=samples.dropna(subset=["longitude","latitude","Indeks potensi relatif"]).drop_duplicates(["longitude","latitude"])
    if len(clean)<3: raise ValueError("Minimal tiga titik valid diperlukan.")
    coords=clean[["longitude","latitude"]].to_numpy(float); vals=clean["Indeks potensi relatif"].to_numpy(float)
    minx,miny,maxx,maxy=polygon.bounds; dx=(maxx-minx)/cols; dy=(maxy-miny)/rows
    gx,gy=np.meshgrid(minx+(np.arange(cols)+.5)*dx, miny+(np.arange(rows)+.5)*dy)
    try: grid=griddata(coords,vals,(gx,gy),method="linear")
    except Exception: grid=np.full(gx.shape,np.nan)
    nearest=griddata(coords,vals,(gx,gy),method="nearest")
    grid=np.clip(np.where(np.isfinite(grid),grid,nearest),0,100)
    features=[]

    # Mode 1: poligon per wilayah administrasi, nilai = rata-rata grid di dalamnya.
    if regions is not None and len(regions):
        regs=regions[["_hydova_name","geometry"]].copy()
        regs["geometry"]=regs.geometry.intersection(polygon)
        regs=regs[(~regs.geometry.is_empty)&(regs.geometry.area>1e-8)].reset_index(drop=True)
        if len(regs):
            pts=gpd.GeoDataFrame({"indeks":grid.ravel()},
                geometry=gpd.points_from_xy(gx.ravel(),gy.ravel()),crs="EPSG:4326")
            joined=gpd.sjoin(pts,regs[["geometry"]],predicate="within",how="inner")
            mean=joined.groupby("index_right")["indeks"].mean()
            for i,row in regs.iterrows():
                if i in mean.index: v=float(mean[i])
                else:
                    rp=row.geometry.representative_point()
                    v=float(griddata(coords,vals,[(rp.x,rp.y)],method="nearest")[0])
                features.append({"type":"Feature",
                    "properties":{"wilayah":row["_hydova_name"],"indeks":round(v,2),"kelas":_kelas(v)},
                    "geometry":mapping(row.geometry)})
            return {"type":"FeatureCollection","features":features}

    # Mode 2: zona kelas mengikuti kontur (halus). Jika matplotlib tidak ada, pakai cadangan kotak.
    try:
        feats=_contour_zone_features(polygon,coords,vals)
        if feats: return {"type":"FeatureCollection","features":feats}
    except Exception as exc:
        print("Kontur gagal, memakai cadangan grid:",exc)
    groups={"Rendah":[],"Sedang":[],"Tinggi":[]}
    for r in range(rows):
        for c in range(cols):
            cell=box(minx+c*dx,miny+r*dy,minx+(c+1)*dx,miny+(r+1)*dy).intersection(polygon)
            if not cell.is_empty and cell.area>0: groups[_kelas(float(grid[r,c]))].append(cell)
    for kelas,cells in groups.items():
        if cells:
            zona=unary_union(cells).buffer(0)
            features.append({"type":"Feature","properties":{"wilayah":"Zona "+kelas,"indeks":None,"kelas":kelas},
                "geometry":mapping(zona)})
    return {"type":"FeatureCollection","features":features}

def summarize_geojson_area_km2(geojson):
    geod=Geod(ellps="WGS84"); totals={"Rendah":0.,"Sedang":0.,"Tinggi":0.}
    for feature in geojson["features"]:
        area,_=geod.geometry_area_perimeter(shape(feature["geometry"]))
        totals[feature["properties"]["kelas"]]+=abs(float(area))
    return {k:v/1_000_000 for k,v in totals.items()}

def summarize_index_area_km2(polygon,samples):
    return summarize_geojson_area_km2(make_index_area_geojson(polygon,samples))

def run_auto_analysis(province_name,area_name,start,end):
    if not GIS_AVAILABLE: raise RuntimeError("Dependensi GIS belum terpasang: "+str(GIS_ERROR))
    adm1,meta1=fetch_geoboundaries("ADM1")
    prov=adm1[adm1["_hydova_name"]==province_name]
    if prov.empty: raise ValueError("Provinsi tidak ditemukan.")
    prov_geom=unary_union(list(prov.geometry))
    adm2,meta2=fetch_geoboundaries("ADM2")
    local=adm2[adm2.geometry.intersects(prov_geom)]
    if area_name=="Seluruh provinsi":
        geom=prov_geom; label=province_name
        regions=local                                   # dibagi per kabupaten/kota
    else:
        rows=local[local["_hydova_name"]==area_name]
        if rows.empty: raise ValueError("Kabupaten/kota tidak ditemukan pada provinsi tersebut.")
        geom=unary_union(list(rows.geometry)); label=f"{area_name}, {province_name}"
        try:                                            # dibagi per kecamatan
            adm3,_=fetch_geoboundaries("ADM3")
            regions=adm3[adm3.geometry.intersects(geom)]
        except Exception:
            regions=None                                # cadangan: zona kelas
    points=create_analysis_grid(geom)
    elevations=fetch_elevation_points([p["latitude"] for p in points],[p["longitude"] for p in points])
    valid=[(p,e) for p,e in zip(points,elevations) if e is not None and np.isfinite(float(e))]
    if len(valid)<3: raise ValueError("Data elevasi valid kurang dari tiga titik.")
    points=[x[0] for x in valid]; elevations=[x[1] for x in valid]
    centroid=geom.representative_point()
    rain,_,api_lat,api_lon=fetch_historical_rain(centroid.y,centroid.x,start,end)
    total=float(rain["Hujan (mm)"].sum())
    samples=calculate_relative_index(points,elevations,total)
    geojson=make_index_region_geojson(geom,samples,regions)
    area_summary=summarize_geojson_area_km2(geojson)
    geotiff=make_index_geotiff(samples,geom)
    boundary=json.loads(gpd.GeoSeries([geom],crs="EPSG:4326").to_json())
    return {"province":province_name,"area":label,"samples":samples.to_dict(orient="records"),
        "rain":rain.assign(Tanggal=rain["Tanggal"].dt.strftime("%Y-%m-%d")).to_dict(orient="records"),
        "rain_total":total,"start":start,"end":end,"area_summary":area_summary,
        "geojson":geojson,"boundary":boundary,"center":[centroid.y,centroid.x],
        "geotiff":geotiff,"boundary_year":meta2.get("boundaryYearRepresented","tidak diketahui"),
        "boundary_source":meta2.get("boundarySource","geoBoundaries"),
        "boundary_license":meta2.get("boundaryLicense","lihat metadata geoBoundaries"),
        "rain_lat":api_lat,"rain_lon":api_lon}


# Buat tabel saat dijalankan lewat gunicorn maupun python app.py.
init_db()

# -------------------------------- ROUTES -------------------------------
@app.context_processor
def inject_globals():
    return {"today":date.today().isoformat()}

@app.route("/")
def dashboard():
    df=read_rainfall()
    stations=sorted(df.station.astype(str).unique().tolist()) if not df.empty else []
    stats_data={"count":len(df),"stations":len(stations),
        "max":float(df.rainfall_mm.max()) if len(df) else 0,
        "avg":float(df.rainfall_mm.mean()) if len(df) else 0}
    daily=[]
    if not df.empty:
        grouped=df.groupby(df.date.dt.strftime("%Y-%m-%d"))["rainfall_mm"].mean()
        daily=[{"date":k,"rain":float(v)} for k,v in grouped.items()]
    recent=df.sort_values("date",ascending=False).head(20).copy()
    if not recent.empty: recent["date"]=recent.date.dt.strftime("%Y-%m-%d")
    return render_template("dashboard.html",stats=stats_data,stations=stations,daily=daily,
        recent=recent.to_dict(orient="records"))

@app.post("/rainfall/add")
def add_rainfall():
    try:
        insert_rainfall(pd.DataFrame([{"date":request.form.get("date"),"station":request.form.get("station","").strip(),
            "rainfall_mm":request.form.get("rainfall_mm")}]))
        flash("Observasi berhasil disimpan.","success")
    except Exception as exc: flash(str(exc),"danger")
    return redirect(url_for("dashboard"))

@app.post("/rainfall/import")
def import_rainfall():
    file=request.files.get("file")
    if not file or not file.filename: flash("Pilih file CSV atau Excel.","warning"); return redirect(url_for("dashboard"))
    ext=file.filename.rsplit(".",1)[-1].lower()
    if ext not in ALLOWED_DATA: flash("Format file harus CSV atau Excel.","danger"); return redirect(url_for("dashboard"))
    try:
        raw=pd.read_csv(file) if ext=="csv" else pd.read_excel(file)
        clean,skipped=validate_rainfall(raw); saved=insert_rainfall(clean)
        flash(f"{saved} baris berhasil disimpan/diperbarui. {skipped} baris tidak valid diabaikan.","success")
    except Exception as exc: flash(f"Gagal mengimpor: {exc}","danger")
    return redirect(url_for("dashboard"))

@app.route("/rainfall/manage")
def manage_rainfall():
    df=read_rainfall()
    if not df.empty: df["date"]=df.date.dt.strftime("%Y-%m-%d")
    return render_template("manage.html",records=df.to_dict(orient="records"))

@app.post("/rainfall/<int:record_id>/edit")
def edit_rainfall(record_id):
    try:
        dt=pd.to_datetime(request.form.get("date"),errors="raise")
        station=request.form.get("station","").strip(); rain=float(request.form.get("rainfall_mm"))
        if not station or rain<0: raise ValueError("Nama stasiun wajib diisi dan hujan tidak boleh negatif.")
        with get_connection() as conn:
            conn.execute("UPDATE rainfall SET date=?,station=?,rainfall_mm=? WHERE id=?",
                (dt.strftime("%Y-%m-%d"),station,rain,record_id)); conn.commit()
        flash("Data berhasil diperbarui.","success")
    except Exception as exc: flash(str(exc),"danger")
    return redirect(url_for("manage_rainfall"))

@app.post("/rainfall/<int:record_id>/delete")
def delete_rainfall(record_id):
    with get_connection() as conn:
        conn.execute("DELETE FROM rainfall WHERE id=?",(record_id,)); conn.commit()
    flash("Data berhasil dihapus.","success")
    return redirect(url_for("manage_rainfall"))

@app.post("/rainfall/reset")
def reset_rainfall():
    if request.form.get("confirm")=="yes":
        with get_connection() as conn: conn.execute("DELETE FROM rainfall"); conn.commit()
        flash("Seluruh data hujan telah dihapus.","success")
    return redirect(url_for("manage_rainfall"))

@app.route("/download/rainfall.csv")
def download_csv():
    df=read_rainfall().drop(columns=["id"])
    return send_file(io.BytesIO(df.to_csv(index=False).encode("utf-8-sig")),mimetype="text/csv",
        as_attachment=True,download_name="HYDOVA_data_hujan.csv")

@app.route("/frequency",methods=["GET","POST"])
def frequency():
    df=read_rainfall(); stations=sorted(df.station.astype(str).unique().tolist()) if not df.empty else []
    result=None; annual=[]
    station=request.values.get("station",stations[0] if stations else "")
    distribution=request.values.get("distribution","Gumbel")
    if not df.empty and station in stations:
        series=annual_maxima(df,station); annual=[{"year":int(k),"rain":float(v)} for k,v in series.items()]
        if request.method=="POST":
            try:
                params,table=calculate_frequency(series.values,distribution)
                result={"params":params,"rows":table.to_dict(orient="records"),
                    "fit":goodness_of_fit(series.values,distribution)}
            except Exception as exc: flash(str(exc),"danger")
    return render_template("frequency.html",stations=stations,station=station,
        distribution=distribution,annual=annual,result=result)

@app.route("/flood",methods=["GET","POST"])
def flood():
    df=read_rainfall(); stations=sorted(df.station.astype(str).unique().tolist()) if not df.empty else []
    result=None; station=request.values.get("station",stations[0] if stations else "")
    distribution=request.values.get("distribution","Gumbel")
    area=float(request.values.get("area",10) or 10); runoff=float(request.values.get("runoff",.6) or 0)
    duration=float(request.values.get("duration",1) or 1)
    if request.method=="POST":
        try:
            if not (area>0 and 0<=runoff<=1 and duration>0): raise ValueError("Periksa luas, koefisien limpasan, dan durasi.")
            if station not in stations: raise ValueError("Pilih stasiun hujan yang tersedia.")
            annual=annual_maxima(df,station)
            _,depths=calculate_frequency(annual.values,distribution)
            table=rational_table(area,runoff,depths,duration)
            result={"rows":table.to_dict(orient="records"),"peak":float(table.iloc[-1]["Debit (m³/det)"]),
                "period":int(table.iloc[-1]["Periode ulang (tahun)"])}
        except Exception as exc: flash(str(exc),"danger")
    return render_template("flood.html",stations=stations,station=station,distribution=distribution,
        area=area,runoff=runoff,duration=duration,result=result)

@app.route("/gis")
def gis():
    return render_template("gis.html",gis_available=GIS_AVAILABLE,gis_error=GIS_ERROR)

@app.route("/api/regions")
def regions():
    if not GIS_AVAILABLE: return jsonify({"error":"Dependensi GIS belum tersedia: "+str(GIS_ERROR)}),400
    level=request.args.get("level","ADM1")
    try:
        gdf,_=fetch_geoboundaries(level)
        province=request.args.get("province")
        if level=="ADM2" and province:
            adm1,_=fetch_geoboundaries("ADM1")
            prow=adm1[adm1["_hydova_name"]==province]
            if prow.empty: raise ValueError("Provinsi tidak ditemukan.")
            province_geom=unary_union(list(prow.geometry))
            gdf=gdf[gdf.geometry.intersects(province_geom)]
        names=sorted(gdf["_hydova_name"].dropna().unique().tolist())
        return jsonify(names)
    except Exception as exc: return jsonify({"error":str(exc)}),500

@app.route("/api/gis-analysis",methods=["POST"])
def gis_analysis():
    payload=request.get_json(silent=True) or {}
    try:
        start=payload.get("start"); end=payload.get("end")
        if not start or not end or start>=end: raise ValueError("Tanggal awal harus sebelum tanggal akhir.")
        result=run_auto_analysis(payload["province"],payload["area"],start,end)
        # Data GeoTIFF dikirim sebagai endpoint terpisah melalui session server.
        output_dir=Path(__file__).with_name("generated")
        output_dir.mkdir(exist_ok=True)
        (output_dir/"HYDOVA_indeks_potensi_interpolasi.tif").write_bytes(result["geotiff"])
        return jsonify({k:v for k,v in result.items() if k!="geotiff"})
    except Exception as exc: return jsonify({"error":str(exc)}),500

@app.route("/download/gis.tif")
def download_gis_tif():
    path=Path(__file__).with_name("generated")/"HYDOVA_indeks_potensi_interpolasi.tif"
    if not path.exists(): flash("Buat analisis GIS terlebih dahulu.","warning"); return redirect(url_for("gis"))
    return send_file(path,mimetype="image/tiff",as_attachment=True,
        download_name="HYDOVA_indeks_potensi_interpolasi.tif")

@app.route("/guide")
def guide():
    return render_template("guide.html")

@app.route("/download/sample.csv")
def sample_csv():
    sample="date,station,rainfall_mm\n2020-01-01,Stasiun Magelang,12.5\n2020-01-02,Stasiun Magelang,0\n"
    return send_file(io.BytesIO(sample.encode()),mimetype="text/csv",as_attachment=True,download_name="contoh_data_hujan.csv")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)