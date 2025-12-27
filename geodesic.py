#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import json
import math
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium
import pulp as lp
from geopy.distance import geodesic  # Kuş uçuşu hesaplama kütüphanesi

# =========================================================================
# SABİTLER VE AYARLAR
# =========================================================================
OPTIMAL_STATUS_CODE = 1

st.set_page_config(page_title="Arıza Optimizasyonu", layout="wide")
st.title("Arıza Müdahalesi İçin Atama/Rota Optimizasyonu")

# =========================================================================
# EKİP KONUMLARI
# =========================================================================
ekip_verileri = {
    "Beyoglu": (41.042942843441594, 28.98187509471993),
    "Beyazit": (41.01255990927693, 28.962134641262114),
    "Bayrampasa": (41.046302182999646, 28.910872668799808),
    "Bakirkoy": (40.98605787570794, 28.89211399154593),
    "Basaksehir": (41.09662872610036, 28.789892375665104),
    "Besyol": (41.02375414632992, 28.790824905276498),
    "Caglayan": (41.07210166553191, 28.982043223356346),
    "Gungoren": (41.02151226059597, 28.887805521157343),
    "Sefakoy": (40.99755768113406, 28.829276198411225),
    "Sariyer": (41.032477772499725, 28.904751568799814)
}
ekip_listesi = list(ekip_verileri.keys())

# =========================================================================
# GEOJSON OKUMA
# =========================================================================
def load_trafos_from_geojson(path: str):
    with open(path, "r", encoding="utf-8") as f:
        geojson_data = json.load(f)

    tum_trafo_konumlari_standart = {}
    trafo_sayaci = 1

    for feature in geojson_data["features"]:
        geometry = feature.get("geometry", {})
        props = feature.get("properties", {})

        # Sadece substation olanları al
        if "power" in props and props["power"] == "substation":
            if geometry.get("type") == "Point":
                lon, lat = geometry["coordinates"]
            elif geometry.get("type") == "MultiPolygon":
                lon, lat = geometry["coordinates"][0][0][0]
            elif geometry.get("type") == "Polygon":
                lon, lat = geometry["coordinates"][0][0]
            else:
                continue

            tum_trafo_konumlari_standart[f"Trafo_{trafo_sayaci}"] = (lat, lon)
            trafo_sayaci += 1

    return tum_trafo_konumlari_standart

# =========================================================================
# KUŞ UÇUŞU (GEODESIC) MESAFE HESABI
# =========================================================================
def compute_C_ij_geodesic(ekip_verileri, trafo_konumlari):
    C_ij = {}
    for i, (i_lat, i_lon) in ekip_verileri.items():
        C_ij[i] = {}
        ekip_loc = (i_lat, i_lon)
        
        for j, (j_lat, j_lon) in trafo_konumlari.items():
            trafo_loc = (j_lat, j_lon)
            # geopy geodesic fonksiyonu (lat, lon) ister, sonuç km
            mesafe = geodesic(ekip_loc, trafo_loc).km
            C_ij[i][j] = round(mesafe, 2)
    return C_ij

# =========================================================================
# GAP ÇÖZ (PUlP)
# =========================================================================
def solve_gap(C_ij, ekip_listesi, trafo_listesi, cap_dict):
    prob = lp.LpProblem("Kus_Ucusu_GAP_UI", lp.LpMinimize)
    X_ij = lp.LpVariable.dicts(
        "Atama",
        [(i, j) for i in ekip_listesi for j in trafo_listesi],
        cat=lp.LpBinary
    )

    # Amaç Fonksiyonu: Toplam Mesafeyi Minimize Et
    prob += lp.lpSum(C_ij[i][j] * X_ij[(i, j)] for i in ekip_listesi for j in trafo_listesi)

    # Kısıt 1: Her arızaya tam olarak 1 ekip gitmeli
    for j in trafo_listesi:
        prob += lp.lpSum(X_ij[(i, j)] for i in ekip_listesi) == 1

    # Kısıt 2: Ekip kapasiteleri aşılmamalı
    for i in ekip_listesi:
        prob += lp.lpSum(X_ij[(i, j)] for j in trafo_listesi) <= int(cap_dict[i])

    prob.solve()
    return prob, X_ij

# =========================================================================
# SOL PANEL (GİRDİLER)
# =========================================================================
with st.sidebar:
    st.header("Girdiler")

    geojson_path = st.text_input("GeoJSON dosya adı/yolu", value="export.geojson")

    try:
        tum_trafo_konumlari_standart = load_trafos_from_geojson(geojson_path)
        st.success(f"Trafo bulundu: {len(tum_trafo_konumlari_standart)}")
        geo_ok = True
    except:
        st.error("GeoJSON okunamadı veya bulunamadı.")
        geo_ok = False
        tum_trafo_konumlari_standart = {}

    st.subheader("Arızalı trafolar")
    trafo_options = list(tum_trafo_konumlari_standart.keys())
    selected_faults = st.multiselect(
        "Arızalı trafoları seç",
        options=trafo_options,
        default=trafo_options[:10] if len(trafo_options) >= 10 else trafo_options
    )

    st.subheader("Ekip kapasiteleri")
    cap_mode = st.selectbox(
        "Kapasite tipi", 
        ["Tek sayı (hepsine aynı)", "Ekip bazlı (tek tek)", "Optimal (Sistem Önerisi)"]
    )

    cap_dict = {}

    # OPTIMAL HESAPLAMA MANTIĞI
    if cap_mode == "Optimal (Sistem Önerisi)":
        # Notebook'taki mantık: (Toplam Arıza / Toplam Ekip) yukarı yuvarla + 1 güvenlik payı
        # Bu sayede yük dengeli dağılır.
        if len(ekip_listesi) > 0:
            calc_cap = math.ceil(len(selected_faults) / len(ekip_listesi)) + 1
        else:
            calc_cap = len(selected_faults)
            
        cap_dict = {i: int(calc_cap) for i in ekip_listesi}
        st.info(f"Sistem, iş yükünü dengelemek için her ekibe {calc_cap} kapasite atadı.")

    elif cap_mode == "Tek sayı (hepsine aynı)":
        default_cap = max(1, math.ceil(max(len(selected_faults), 1) / max(len(ekip_listesi), 1)) + 1)
        max_cap = st.number_input("Kapasite (max_cap)", min_value=1, value=int(default_cap), step=1)
        cap_dict = {i: int(max_cap) for i in ekip_listesi}
    else:
        for i in ekip_listesi:
            cap_dict[i] = int(st.number_input(f"{i} kapasite", min_value=0, value=3, step=1))

    run_btn = st.button("ÇÖZ / HARİTAYI GÜNCELLE")

if not geo_ok:
    st.stop()

# Simülasyon verilerini hazırla
girilen_arizalar = [a for a in selected_faults if a in tum_trafo_konumlari_standart]
trafo_konumlari = {j: tum_trafo_konumlari_standart[j] for j in girilen_arizalar}
trafo_listesi = list(trafo_konumlari.keys())

# =========================================================================
# ANA EKRAN DÜZENİ (Map & Table)
# =========================================================================
col_map, col_right = st.columns([1.2, 0.8], gap="large")

# Butona basıldıysa veya ilk kez açılıyorsa (session state kontrolü)
if run_btn or ("last_solution" not in st.session_state):
    if len(trafo_listesi) == 0:
        st.warning("Arızalı trafo seçmedin.")
        st.stop()

    # ARTIK OSMnx YOK, HIZLICA GEODESIC HESAPLANIYOR
    with st.spinner("Kuş uçuşu mesafeler hesaplanıyor..."):
        C_ij = compute_C_ij_geodesic(ekip_verileri, trafo_konumlari)

    with st.spinner("Optimizasyon modeli çözülüyor..."):
        prob, X_ij = solve_gap(C_ij, ekip_listesi, trafo_listesi, cap_dict)

    st.session_state.last_solution = {
        "C_ij": C_ij,
        "status": prob.status,
        "status_text": lp.LpStatus[prob.status],
        "objective": float(lp.value(prob.objective)) if prob.status == OPTIMAL_STATUS_CODE else None,
        "X": {(i, j): float(lp.value(X_ij[(i, j)])) for i in ekip_listesi for j in trafo_listesi},
        "trafo_konumlari": trafo_konumlari,
        "cap_dict": cap_dict,
    }

# Sonuçları State'den çek
sol = st.session_state.last_solution
C_ij = sol["C_ij"]
status_text = sol["status_text"]
objective = sol["objective"]
Xvals = sol["X"]
trafo_konumlari = sol["trafo_konumlari"]
cap_dict = sol["cap_dict"]

# =========================================================================
# SAĞ: SONUÇ TABLOLARI
# =========================================================================
with col_right:
    st.subheader("Özet")
    st.write(f"Durum: **{status_text}**")

    if sol["status"] != OPTIMAL_STATUS_CODE:
        st.error("Optimal çözüm bulunamadı. Kapasiteleri artırmayı deneyin.")
    else:
        st.success(f"TOPLAM MİNİMUM MESAFE (Kuş Uçuşu): **{objective:.2f} km**")

        # Atama Verilerini Hazırla
        rows = []
        atama_sonuclari = {}

        for j in trafo_listesi:
            assigned_i = None
            for i in ekip_listesi:
                if Xvals.get((i, j), 0) == 1.0:
                    assigned_i = i
                    break
            
            if assigned_i is not None:
                mesafe = C_ij[assigned_i][j]
                rows.append({
                    "Arıza (Trafo)": j,
                    "Atanan Ekip": assigned_i,
                    "Mesafe (km)": mesafe
                })
                
                if assigned_i not in atama_sonuclari:
                    atama_sonuclari[assigned_i] = {"Arıza Sayısı": 0, "Toplam Mesafe (km)": 0.0}
                atama_sonuclari[assigned_i]["Arıza Sayısı"] += 1
                atama_sonuclari[assigned_i]["Toplam Mesafe (km)"] += float(mesafe)

        st.subheader("Atama Listesi")
        df_assign = pd.DataFrame(rows).sort_values(["Atanan Ekip", "Arıza (Trafo)"], ignore_index=True)
        st.dataframe(df_assign, use_container_width=True, height=360)

        st.subheader("Ekip Bazlı İş Yükü")
        if atama_sonuclari:
            df_atama = pd.DataFrame(atama_sonuclari).T
            df_atama["Arıza Sayısı"] = df_atama["Arıza Sayısı"].astype(int)
            df_atama["Toplam Mesafe (km)"] = df_atama["Toplam Mesafe (km)"].round(2)
            df_atama["Kapasite"] = pd.Series(cap_dict)
            st.dataframe(df_atama[["Arıza Sayısı", "Kapasite", "Toplam Mesafe (km)"]], use_container_width=True, height=280)
        else:
            st.write("Atama yapılmadı.")

# =========================================================================
# ORTA: HARİTA (DÜZ ÇİZGİLER)
# =========================================================================
with col_map:
    st.subheader("Harita (Kuş Uçuşu Atamalar)")

    lat_mean = sum(v[0] for v in ekip_verileri.values()) / len(ekip_verileri)
    lon_mean = sum(v[1] for v in ekip_verileri.values()) / len(ekip_verileri)

    m = folium.Map(location=(lat_mean, lon_mean), zoom_start=11, control_scale=True)

    palette = [
        "red", "blue", "green", "purple", "orange",
        "darkred", "cadetblue", "darkgreen", "darkblue", "pink"
    ]
    ekip_color = {ekip_listesi[idx]: palette[idx % len(palette)] for idx in range(len(ekip_listesi))}

    # Ekipleri Haritaya Ekle
    for i, (ilat, ilon) in ekip_verileri.items():
        folium.Marker(
            location=(ilat, ilon),
            tooltip=f"Ekip: {i} | Kapasite: {cap_dict.get(i, 0)}",
            icon=folium.Icon(color=ekip_color[i], icon="users", prefix="fa")
        ).add_to(m)

    # Trafoları ve Çizgileri Ekle
    for j, (jlat, jlon) in trafo_konumlari.items():
        assigned_i = None
        for i in ekip_listesi:
            if Xvals.get((i, j), 0) == 1.0:
                assigned_i = i
                break
        
        # Eğer atama yapılmadıysa varsayılan renk (gri) olsun
        if assigned_i is None:
            color = "gray"
            assigned_i_text = "Atanmadı"
        else:
            color = ekip_color[assigned_i]
            assigned_i_text = assigned_i

        folium.Marker(
            location=(jlat, jlon),
            tooltip=f"Arıza: {j} | Atanan: {assigned_i_text}",
            icon=folium.Icon(color=color, icon="bolt", prefix="fa")
        ).add_to(m)

        if assigned_i is not None:
            ilat, ilon = ekip_verileri[assigned_i]
            # Düz çizgi (Kuş uçuşu temsili)
            folium.PolyLine(
                locations=[(ilat, ilon), (jlat, jlon)],
                color=color,
                weight=2.5,
                opacity=0.8,
                dash_array='5, 5'  # Kesikli çizgi kuş uçuşunu daha iyi ifade eder
            ).add_to(m)

    st_folium(m, width=None, height=720)

