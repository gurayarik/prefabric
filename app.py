import sqlite3
import json
import math
import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import List, Dict, Optional

# --- Veritabanı Yöneticisi Sınıfı (Değişmeden Kalabilir) ---
# Bu kısım Flask'tan bağımsız olduğu için büyük ölçüde aynı kalabilir.
# Ancak, veritabanı bağlantılarının yönetimi FastAPI context'ine daha uygun hale getirilebilir.
class VeritabaniYoneticisi:
    def __init__(self, db_adı="malzeme_veritabani.db"):
        self.db_adı = db_adı
        self.conn = None

    def baglan(self):
        try:
            if self.conn is None or not self._is_connection_active():
                self.conn = sqlite3.connect(self.db_adı)
                self.conn.row_factory = sqlite3.Row
            return self.conn
        except sqlite3.Error as e:
            print(f"Veritabanı bağlantı hatası: {e}")
            self.conn = None
            return None

    def _is_connection_active(self):
        if self.conn:
            try:
                self.conn.execute("SELECT 1").fetchone()
                return True
            except sqlite3.Error:
                return False
        return False

    def baglantiyi_kapat(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def tablo_olustur(self):
        conn = self.baglan()
        if not conn:
            print("Tablo oluşturma başarısız: Veritabanı bağlantısı yok.")
            return

        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Malzemeler (
                malzeme_id INTEGER PRIMARY KEY AUTOINCREMENT,
                malzeme_adi TEXT NOT NULL UNIQUE,
                malzeme_kategori TEXT NOT NULL,
                birim_olcu_tipi TEXT NOT NULL,
                varsayilan_fire_orani REAL,
                aciklama TEXT
            );
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Fiyatlar (
                fiyat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                malzeme_id INTEGER NOT NULL,
                birim_fiyat REAL NOT NULL,
                gecerlilik_tarihi TEXT NOT NULL,
                tedarikci TEXT,
                FOREIGN KEY (malzeme_id) REFERENCES Malzemeler(malzeme_id) ON DELETE CASCADE
            );
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS Sarfiyatlar (
                sarfiyat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                malzeme_id INTEGER NOT NULL,
                uygulama_turu TEXT,
                sarfiyat_degeri REAL,
                sarfiyat_birimi TEXT,
                FOREIGN KEY (malzeme_id) REFERENCES Malzemeler(malzeme_id) ON DELETE CASCADE
            );
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS UygulamaDetaylari (
                detay_id INTEGER PRIMARY KEY AUTOINCREMENT,
                oda_tipi TEXT NOT NULL,
                uygulama_alani TEXT NOT NULL,
                varsayilan_malzeme_kategori TEXT,
                varsayilan_malzeme_id INTEGER,
                ek_ozellikler TEXT,
                FOREIGN KEY (varsayilan_malzeme_id) REFERENCES Malzemeler(malzeme_id) ON DELETE SET NULL
            );
        ''')
        conn.commit()

    def veri_ekle(self, tablo_adı, veri_dict):
        conn = self.baglan()
        if not conn: return None
        sutunlar = ', '.join(veri_dict.keys())
        yer_tutucular = ', '.join(['?' for _ in veri_dict.values()])
        sorgu = f"INSERT INTO {tablo_adı} ({sutunlar}) VALUES ({yer_tutucular})"
        try:
            cursor = conn.cursor()
            cursor.execute(sorgu, tuple(veri_dict.values()))
            conn.commit()
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None
        except sqlite3.Error as e:
            print(f"Veri ekleme hatası ({tablo_adı}): {e}")
            return None

    def veri_guncelle(self, tablo_adı, veri_dict, kosullar):
        conn = self.baglan()
        if not conn: return False
        set_clause = ", ".join([f"{k} = ?" for k in veri_dict.keys()])
        where_clause = " AND ".join([f"{k} = ?" for k in kosullar.keys()])
        sorgu = f"UPDATE {tablo_adı} SET {set_clause} WHERE {where_clause}"
        try:
            cursor = conn.cursor()
            cursor.execute(sorgu, tuple(list(veri_dict.values()) + list(kosullar.values())))
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"Veri güncelleme hatası ({tablo_adı}): {e}")
            return False

    def veri_sorgula(self, tablo_adı, kosullar=None):
        conn = self.baglan()
        if not conn: return []
        sorgu = f"SELECT * FROM {tablo_adı}"
        if kosullar:
            where_clause = " AND ".join([f"{k} = ?" for k in kosullar.keys()])
            sorgu += f" WHERE {where_clause}"
        try:
            cursor = conn.cursor()
            cursor.execute(sorgu, tuple(kosullar.values()) if kosullar else ())
            return cursor.fetchall()
        except sqlite3.Error as e:
            print(f"Veri sorgulama hatası ({tablo_adı}): {e}")
            return []

    def malzeme_bilgisi_getir(self, malzeme_adi_veya_kategori):
        conn = self.baglan()
        if not conn: return None
        malzeme_data = self.veri_sorgula("Malzemeler", {"malzeme_adi": malzeme_adi_veya_kategori})
        if not malzeme_data:
            malzeme_data = self.veri_sorgula("Malzemeler", {"malzeme_kategori": malzeme_adi_veya_kategori})
        if not malzeme_data:
            return None
        malzeme = dict(malzeme_data[0])
        malzeme_id = malzeme["malzeme_id"]

        fiyat_data_raw = conn.execute("""
            SELECT * FROM Fiyatlar
            WHERE malzeme_id = ?
            ORDER BY gecerlilik_tarihi DESC, fiyat_id DESC
            LIMIT 1
        """, (malzeme_id,)).fetchall()

        sarfiyat_data = self.veri_sorgula("Sarfiyatlar", {"malzeme_id": malzeme_id})

        return {
            "malzeme": malzeme,
            "fiyat": dict(fiyat_data_raw[0]) if fiyat_data_raw else None,
            "sarfiyat": [dict(s) for s in sarfiyat_data] if sarfiyat_data else []
        }

# --- Ev Hesaplayıcı Sınıfı (Değişmeden Kalabilir) ---
# Bu sınıf da doğrudan Flask'a bağımlı olmadığı için aynı kalabilir.
class EvHesaplayici:
    def __init__(self, db_yoneticisi: VeritabaniYoneticisi, ev_data: dict):
        self.db = db_yoneticisi
        self.ev_bilgileri = ev_data
        self.malzeme_ihtiyaclari = {}
        self.toplam_proje_maliyeti = 0.0

    def _alan_hesapla(self):
        self.toplam_pencere_alani = sum(p["genislik"] * p["yukseklik"] * p["adet"] for p in self.ev_bilgileri["pencere_listesi"])
        self.toplam_kapi_alani = sum(k["genislik"] * k["yukseklik"] * k["adet"] for k in self.ev_bilgileri["kapi_listesi"])

        self.oda_alanlari = {}
        self.toplam_taban_alani = 0.0
        self.toplam_duvar_alani_brut = 0.0

        for oda in self.ev_bilgileri["oda_listesi"]:
            zemin_alani = oda["uzunluk"] * oda["genislik"]
            duvar_alani = 2 * (oda["uzunluk"] + oda["genislik"]) * oda["yukseklik"]
            self.oda_alanlari[oda["oda_adi"]] = {"zemin": zemin_alani, "duvar": duvar_alani}
            self.toplam_taban_alani += zemin_alani
            self.toplam_duvar_alani_brut += duvar_alani

        if self.ev_bilgileri["kat_sayisi"] > 0:
            taban_alani_her_kat = self.toplam_taban_alani / self.ev_bilgileri["kat_sayisi"]
        else:
            taban_alani_her_kat = self.toplam_taban_alani

        self.cati_alani_taban = taban_alani_her_kat

        if self.ev_bilgileri["cati_tipi"] != "Düz" and self.ev_bilgileri["cati_egim_acisi"] > 0:
            cos_angle = math.cos(math.radians(self.ev_bilgileri["cati_egim_acisi"]))
            if abs(cos_angle) < 0.001:
                self.cati_alani = self.cati_alani_taban * 2
            else:
                self.cati_alani = self.cati_alani_taban / cos_angle
        else:
            self.cati_alani = self.cati_alani_taban

    def malzeme_ihtiyacini_hesapla(self):
        self._alan_hesapla()
        self.malzeme_ihtiyaclari = {}
        toplam_maliyet_temp = 0.0

        # --- Duvar Panelleri ---
        net_duvar_alani_paneller_icin = self.toplam_duvar_alani_brut - self.toplam_pencere_alani - self.toplam_kapi_alani
        if net_duvar_alani_paneller_icin < 0: net_duvar_alani_paneller_icin = 0

        malzeme_info = self.db.malzeme_bilgisi_getir("Duvar Paneli")
        if malzeme_info and malzeme_info["fiyat"]:
            malzeme = malzeme_info["malzeme"]
            fiyat = malzeme_info["fiyat"]
            gerekli_miktar = net_duvar_alani_paneller_icin * (1 + malzeme["varsayilan_fire_orani"])
            maliyet = gerekli_miktar * fiyat["birim_fiyat"]
            self.malzeme_ihtiyaclari["Duvar Paneli (Toplam)"] = {
                "net_miktar": net_duvar_alani_paneller_icin,
                "gerekli_miktar": gerekli_miktar,
                "birim_olcu": malzeme["birim_olcu_tipi"],
                "birim_fiyat": fiyat["birim_fiyat"],
                "maliyet": maliyet
            }
            toplam_maliyet_temp += maliyet
        # else: # Flash mesajları FastAPI'de farklı yönetilecek
        #     flash("Duvar Paneli bilgisi veritabanında bulunamadı veya fiyatı yok.", "warning")

        # --- Çatı Kaplama Malzemesi ---
        malzeme_info = self.db.malzeme_bilgisi_getir("Çatı Paneli")
        if malzeme_info and malzeme_info["fiyat"]:
            malzeme = malzeme_info["malzeme"]
            fiyat = malzeme_info["fiyat"]
            gerekli_miktar = self.cati_alani * (1 + malzeme["varsayilan_fire_orani"])
            maliyet = gerekli_miktar * fiyat["birim_fiyat"]
            self.malzeme_ihtiyaclari["Çatı Kaplama (Toplam)"] = {
                "net_miktar": self.cati_alani,
                "gerekli_miktar": gerekli_miktar,
                "birim_olcu": malzeme["birim_olcu_tipi"],
                "birim_fiyat": fiyat["birim_fiyat"],
                "maliyet": maliyet
            }
            toplam_maliyet_temp += maliyet
        # else:
        #     flash("Çatı Paneli bilgisi veritabanında bulunamadı veya fiyatı yok.", "warning")


        # --- Odalara göre zemin ve duvar kaplamaları ---
        for oda in self.ev_bilgileri["oda_listesi"]:
            oda_adi = oda["oda_adi"]
            zemin_alani_net = self.oda_alanlari[oda_adi]["zemin"]

            oda_duvar_alani_brut = self.oda_alanlari[oda_adi]["duvar"]
            pencere_kapi_bosluk_orani = 0
            if self.toplam_duvar_alani_brut > 0:
                pencere_kapi_bosluk_orani = (oda_duvar_alani_brut / self.toplam_duvar_alani_brut) * (self.toplam_pencere_alani + self.toplam_kapi_alani)

            duvar_alani_net = oda_duvar_alani_brut - pencere_kapi_bosluk_orani
            if duvar_alani_net < 0: duvar_alani_net = 0

            # Zemin Kaplama
            malzeme_adi_zemin = oda["zemin_kaplama_tipi"]
            malzeme_info_zemin = self.db.malzeme_bilgisi_getir(malzeme_adi_zemin)
            if malzeme_info_zemin and malzeme_info_zemin["fiyat"]:
                malzeme = malzeme_info_zemin["malzeme"]
                fiyat = malzeme_info_zemin["fiyat"]

                uygulama_detaylari_raw = self.db.veri_sorgula("UygulamaDetaylari",
                                                    {"oda_tipi": oda_adi, "uygulama_alani": "Zemin"})
                ek_ozellikler = {}
                if uygulama_detaylari_raw and uygulama_detaylari_raw[0]["ek_ozellikler"]:
                    try:
                        ek_ozellikler = json.loads(uygulama_detaylari_raw[0]["ek_ozellikler"])
                    except json.JSONDecodeError:
                        pass # flash(f"{oda_adi} Zemin için uygulama detaylarındaki JSON hatalı.", "warning")

                if malzeme["malzeme_adi"] == "Fayans" and "fayans_boyut_m2" in ek_ozellikler and ek_ozellikler["fayans_boyut_m2"] > 0:
                    fayans_m2 = ek_ozellikler["fayans_boyut_m2"]
                    gerekli_adet = (zemin_alani_net / fayans_m2) * (1 + malzeme["varsayilan_fire_orani"])
                    maliyet = gerekli_adet * fiyat["birim_fiyat"]
                    self.malzeme_ihtiyaclari[f"{oda_adi} Zemin ({malzeme['malzeme_adi']})"] = {
                        "net_miktar": zemin_alani_net,
                        "gerekli_miktar": gerekli_adet,
                        "birim_olcu": "adet",
                        "birim_fiyat": fiyat["birim_fiyat"],
                        "maliyet": maliyet
                    }
                else:
                    gerekli_miktar = zemin_alani_net * (1 + malzeme["varsayilan_fire_orani"])
                    maliyet = gerekli_miktar * fiyat["birim_fiyat"]
                    self.malzeme_ihtiyaclari[f"{oda_adi} Zemin ({malzeme['malzeme_adi']})"] = {
                        "net_miktar": zemin_alani_net,
                        "gerekli_miktar": gerekli_miktar,
                        "birim_olcu": malzeme["birim_olcu_tipi"],
                        "birim_fiyat": fiyat["birim_fiyat"],
                        "maliyet": maliyet
                    }
                toplam_maliyet_temp += maliyet
            # else:
            #     flash(f"{oda_adi} için {malzeme_adi_zemin} zemin kaplama bilgisi veritabanında bulunamadı veya fiyatı yok.", "warning")

            # Duvar Kaplama
            malzeme_adi_duvar = oda["duvar_kaplama_tipi"]
            malzeme_info_duvar = self.db.malzeme_bilgisi_getir(malzeme_adi_duvar)
            if malzeme_info_duvar and malzeme_info_duvar["fiyat"]:
                malzeme = malzeme_info_duvar["malzeme"]
                fiyat = malzeme_info_duvar["fiyat"]

                if malzeme["malzeme_adi"] == "Boya":
                    sarfiyat_data = [s for s in malzeme_info_duvar["sarfiyat"] if s["uygulama_turu"] == "Duvar"]
                    if sarfiyat_data:
                        sarfiyat_degeri = sarfiyat_data[0]["sarfiyat_degeri"]
                        uygulama_detaylari_raw = self.db.veri_sorgula("UygulamaDetaylari",
                                                            {"oda_tipi": oda_adi, "uygulama_alani": "Duvar"})
                        ek_ozellikler = {}
                        if uygulama_detaylari_raw and uygulama_detaylari_raw[0]["ek_ozellikler"]:
                            try:
                                ek_ozellikler = json.loads(uygulama_detaylari_raw[0]["ek_ozellikler"])
                            except json.JSONDecodeError:
                                pass # flash(f"{oda_adi} Duvar için uygulama detaylarındaki JSON hatalı.", "warning")

                        kat_sayisi = ek_ozellikler.get("kat_sayisi", 1)

                        gerekli_litre = duvar_alani_net * sarfiyat_degeri * kat_sayisi * (1 + malzeme["varsayilan_fire_orani"])
                        maliyet = gerekli_litre * fiyat["birim_fiyat"]
                        self.malzeme_ihtiyaclari[f"{oda_adi} Duvar ({malzeme['malzeme_adi']})"] = {
                            "net_miktar": duvar_alani_net,
                            "gerekli_miktar": gerekli_litre,
                            "birim_olcu": "litre",
                            "birim_fiyat": fiyat["birim_fiyat"],
                            "maliyet": maliyet
                        }
                        toplam_maliyet_temp += maliyet
                    # else:
                    #     flash(f"{oda_adi} için {malzeme_adi_duvar} boya sarfiyat bilgisi bulunamadı.", "warning")
                else:
                    gerekli_miktar = duvar_alani_net * (1 + malzeme["varsayilan_fire_orani"])
                    maliyet = gerekli_miktar * fiyat["birim_fiyat"]
                    self.malzeme_ihtiyaclari[f"{oda_adi} Duvar ({malzeme['malzeme_adi']})"] = {
                        "net_miktar": duvar_alani_net,
                        "gerekli_miktar": gerekli_miktar,
                        "birim_olcu": malzeme["birim_olcu_tipi"],
                        "birim_fiyat": fiyat["birim_fiyat"],
                        "maliyet": maliyet
                    }
                toplam_maliyet_temp += maliyet
            # else:
            #     flash(f"{oda_adi} için {malzeme_adi_duvar} duvar kaplama bilgisi veritabanında bulunamadı veya fiyatı yok.", "warning")

        # --- Pencereler ---
        for i, pencere in enumerate(self.ev_bilgileri["pencere_listesi"]):
            malzeme_info = self.db.malzeme_bilgisi_getir("PVC Pencere")
            if malzeme_info and malzeme_info["fiyat"]:
                malzeme = malzeme_info["malzeme"]
                fiyat = malzeme_info["fiyat"]

                gerekli_adet = pencere["adet"]
                maliyet = gerekli_adet * fiyat["birim_fiyat"]
                self.malzeme_ihtiyaclari[f"Pencere {i+1} ({pencere['genislik']}x{pencere['yukseklik']}m)"] = {
                    "net_miktar": pencere["adet"],
                    "gerekli_miktar": gerekli_adet,
                    "birim_olcu": "adet",
                    "birim_fiyat": fiyat["birim_fiyat"],
                    "maliyet": maliyet
                }
                toplam_maliyet_temp += maliyet
            # else:
            #     flash(f"Pencere {i+1} için 'PVC Pencere' bilgisi veritabanında bulunamadı veya fiyatı yok.", "warning")

        # --- Kapılar ---
        for i, kapi in enumerate(self.ev_bilgileri["kapi_listesi"]):
            kategori_kapi = "Dış Kapı" if "ana giriş" in kapi["kapi_adi"].lower() else "İç Kapı"
            malzeme_info = self.db.malzeme_bilgisi_getir(kategori_kapi)

            if malzeme_info and malzeme_info["fiyat"]:
                malzeme = malzeme_info["malzeme"]
                fiyat = malzeme_info["fiyat"]

                gerekli_adet = kapi["adet"]
                maliyet = gerekli_adet * fiyat["birim_fiyat"]
                self.malzeme_ihtiyaclari[f"Kapı {i+1} ({kapi['genislik']}x{kapi['yukseklik']}m)"] = {
                    "net_miktar": kapi["adet"],
                    "gerekli_miktar": gerekli_adet,
                    "birim_olcu": "adet",
                    "birim_fiyat": fiyat["birim_fiyat"],
                    "maliyet": maliyet
                }
                toplam_maliyet_temp += maliyet
            # else:
            #     flash(f"Kapı {i+1} için '{kategori_kapi}' bilgisi veritabanında bulunamadı veya fiyatı yok.", "warning")

        # --- Tesisat ve Bağlantı Elemanları (Basitleştirilmiş Yüzde Yaklaşımı) ---
        tesisat_baglanti_orani = 0.15
        tahmini_tesisat_maliyeti = toplam_maliyet_temp * tesisat_baglanti_orani
        self.malzeme_ihtiyaclari["Tesisat ve Bağlantı Elemanları (Tahmini)"] = {
            "net_miktar": "N/A",
            "gerekli_miktar": "N/A",
            "birim_olcu": "TL",
            "birim_fiyat": 1.0,
            "maliyet": tahmini_tesisat_maliyeti
        }
        toplam_maliyet_temp += tahmini_tesisat_maliyeti

        self.toplam_proje_maliyeti = toplam_maliyet_temp
        return self.malzeme_ihtiyaclari, self.toplam_proje_maliyeti


# --- FastAPI Uygulama Nesnesi ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# FastAPI startup event'i ile veritabanını başlatma ve örnek verileri ekleme
@app.on_event("startup")
async def startup_event():
    db_yoneticisi.baglan()
    db_yoneticisi.tablo_olustur()

    print("Veritabanına örnek malzemeler ekleniyor...")
    current_date = datetime.date.today().isoformat()

    malzeme_ids = {}
    malzeme_ids['Duvar Paneli'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "Duvar Paneli", "malzeme_kategori": "Yapısal", "birim_olcu_tipi": "m2", "varsayilan_fire_orani": 0.05, "aciklama": "Prefabric ev duvar paneli"})
    malzeme_ids['Çatı Paneli'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "Çatı Paneli", "malzeme_kategori": "Yapısal", "birim_olcu_tipi": "m2", "varsayilan_fire_orani": 0.07, "aciklama": "Prefabric ev çatı paneli"})
    malzeme_ids['Fayans'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "Fayans", "malzeme_kategori": "Zemin Kaplama", "birim_olcu_tipi": "m2", "varsayilan_fire_orani": 0.10, "aciklama": "Standart seramik fayans"})
    malzeme_ids['Laminat Parke'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "Laminat Parke", "malzeme_kategori": "Zemin Kaplama", "birim_olcu_tipi": "m2", "varsayilan_fire_orani": 0.07, "aciklama": "8mm laminat parke"})
    malzeme_ids['Boya'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "Boya", "malzeme_kategori": "Duvar Kaplama", "birim_olcu_tipi": "litre", "varsayilan_fire_orani": 0.05, "aciklama": "Su bazlı iç cephe boyası"})
    malzeme_ids['PVC Pencere'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "PVC Pencere", "malzeme_kategori": "Doğrama", "birim_olcu_tipi": "adet", "varsayilan_fire_orani": 0.00, "aciklama": "Isıcamlı PVC pencere"})
    malzeme_ids['İç Kapı'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "İç Kapı", "malzeme_kategori": "Doğrama", "birim_olcu_tipi": "adet", "varsayilan_fire_orani": 0.00, "aciklama": "Panel iç oda kapısı"})
    malzeme_ids['Dış Kapı'] = db_yoneticisi.veri_ekle("Malzemeler", {"malzeme_adi": "Dış Kapı", "malzeme_kategori": "Doğrama", "birim_olcu_tipi": "adet", "varsayilan_fire_orani": 0.00, "aciklama": "Çelik dış kapı"})

    if malzeme_ids['Duvar Paneli']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['Duvar Paneli'], "birim_fiyat": 120.00, "gecerlilik_tarihi": current_date, "tedarikci": "A Panel"})
    if malzeme_ids['Çatı Paneli']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['Çatı Paneli'], "birim_fiyat": 150.00, "gecerlilik_tarihi": current_date, "tedarikci": "B Çatı"})
    if malzeme_ids['Fayans']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['Fayans'], "birim_fiyat": 50.00, "gecerlilik_tarihi": current_date, "tedarikci": "C Yapı"})
    if malzeme_ids['Laminat Parke']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['Laminat Parke'], "birim_fiyat": 80.00, "gecerlilik_tarihi": current_date, "tedarikci": "D Zemin"})
    if malzeme_ids['Boya']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['Boya'], "birim_fiyat": 50.00, "gecerlilik_tarihi": current_date, "tedarikci": "E Boya"})
    if malzeme_ids['PVC Pencere']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['PVC Pencere'], "birim_fiyat": 1500.00, "gecerlilik_tarihi": current_date, "tedarikci": "F Pencere"})
    if malzeme_ids['İç Kapı']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['İç Kapı'], "birim_fiyat": 800.00, "gecerlilik_tarihi": current_date, "tedarikci": "G Kapı"})
    if malzeme_ids['Dış Kapı']: db_yoneticisi.veri_ekle("Fiyatlar", {"malzeme_id": malzeme_ids['Dış Kapı'], "birim_fiyat": 2000.00, "gecerlilik_tarihi": current_date, "tedarikci": "G Kapı"})

    if malzeme_ids['Boya']: db_yoneticisi.veri_ekle("Sarfiyatlar", {"malzeme_id": malzeme_ids['Boya'], "uygulama_turu": "Duvar", "sarfiyat_degeri": 0.15, "sarfiyat_birimi": "litre/m2"})

    if malzeme_ids['Fayans']: db_yoneticisi.veri_ekle("UygulamaDetaylari", {"oda_tipi": "Banyo", "uygulama_alani": "Zemin", "varsayilan_malzeme_kategori": "Fayans", "varsayilan_malzeme_id": malzeme_ids['Fayans'], "ek_ozellikler": json.dumps({"fayans_boyut_m2": 0.09})})
    if malzeme_ids['Fayans']: db_yoneticisi.veri_ekle("UygulamaDetaylari", {"oda_tipi": "Mutfak", "uygulama_alani": "Zemin", "varsayilan_malzeme_kategori": "Fayans", "varsayilan_malzeme_id": malzeme_ids['Fayans'], "ek_ozellikler": json.dumps({"fayans_boyut_m2": 0.09})})
    if malzeme_ids['Boya']: db_yoneticisi.veri_ekle("UygulamaDetaylari", {"oda_tipi": "Salon", "uygulama_alani": "Duvar", "varsayilan_malzeme_kategori": "Boya", "varsayilan_malzeme_id": malzeme_ids['Boya'], "ek_ozellikler": json.dumps({"kat_sayisi": 2})})
    if malzeme_ids['Boya']: db_yoneticisi.veri_ekle("UygulamaDetaylari", {"oda_tipi": "Yatak Odası", "uygulama_alani": "Duvar", "varsayilan_malzeme_kategori": "Boya", "varsayilan_malzeme_id": malzeme_ids['Boya'], "ek_ozellikler": json.dumps({"kat_sayisi": 2})})
    if malzeme_ids['Laminat Parke']: db_yoneticisi.veri_ekle("UygulamaDetaylari", {"oda_tipi": "Salon", "uygulama_alani": "Zemin", "varsayilan_malzeme_kategori": "Laminat Parke", "varsayilan_malzeme_id": malzeme_ids['Laminat Parke'], "ek_ozellikler": json.dumps({})})

    print("Veritabanı kurulumu tamamlandı ve örnek veriler eklendi.")

# FastAPI shutdown event'i ile veritabanı bağlantısını kapatma
@app.on_event("shutdown")
async def shutdown_event():
    db_yoneticisi.baglantiyi_kapat()

# Bağımlılık ekleme için bir yardımcı fonksiyon
def get_db():
    try:
        yield db_yoneticisi
    finally:
        db_yoneticisi.baglantiyi_kapat() # Her request sonunda bağlantıyı kapat


def get_oda_kaplama_tipleri(db: VeritabaniYoneticisi = Depends(get_db)):
    malzemeler_raw = db.veri_sorgula("Malzemeler", kosullar={})
    kaplama_tipleri = set()
    for row in malzemeler_raw:
        if row['malzeme_kategori'] in ('Zemin Kaplama', 'Duvar Kaplama'):
            kaplama_tipleri.add(row['malzeme_adi'])
    return sorted(list(kaplama_tipleri))


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request, db: VeritabaniYoneticisi = Depends(get_db)):
    oda_kaplama_tipleri = get_oda_kaplama_tipleri(db)
    return templates.TemplateResponse("index.html", {"request": request, "oda_kaplama_tipleri": oda_kaplama_tipleri})


@app.post("/", response_class=HTMLResponse)
async def calculate_cost(
    request: Request,
    ev_tipi: str = Form(...),
    kat_sayisi: int = Form(...),
    cati_tipi: str = Form(...),
    cati_egim_acisi: Optional[float] = Form(0.0),
    oda_adi: List[str] = Form(..., alias="oda_adi[]"),
    oda_uzunluk: List[float] = Form(..., alias="oda_uzunluk[]"),
    oda_genislik: List[float] = Form(..., alias="oda_genislik[]"),
    oda_yukseklik: List[float] = Form(..., alias="oda_yukseklik[]"),
    oda_zemin_kaplama: List[str] = Form(..., alias="oda_zemin_kaplama[]"),
    oda_duvar_kaplama: List[str] = Form(..., alias="oda_duvar_kaplama[]"),
    pencere_genislik: List[float] = Form(..., alias="pencere_genislik[]"),
    pencere_yukseklik: List[float] = Form(..., alias="pencere_yukseklik[]"),
    pencere_adet: List[int] = Form(..., alias="pencere_adet[]"),
    kapi_adi: List[str] = Form(..., alias="kapi_adi[]"),
    kapi_genislik: List[float] = Form(..., alias="kapi_genislik[]"),
    kapi_yukseklik: List[float] = Form(..., alias="kapi_yukseklik[]"),
    kapi_adet: List[int] = Form(..., alias="kapi_adet[]"),
    db: VeritabaniYoneticisi = Depends(get_db)
):
    # Flash mesajları FastAPI'de doğrudan render_template ile çalışmadığı için,
    # Hata durumlarını HTTPExceptions veya response'a özel context'ler ile yönetmek gerekir.
    # Şimdilik basit bir hata yönetimi yapalım.
    messages = [] # Hata mesajlarını toplamak için

    try:
        ev_data = {
            "ev_tipi": ev_tipi,
            "kat_sayisi": kat_sayisi,
            "cati_tipi": cati_tipi,
            "cati_egim_acisi": cati_egim_acisi,
            "oda_listesi": [],
            "pencere_listesi": [],
            "kapi_listesi": []
        }

        for i in range(len(oda_adi)):
            if oda_adi[i].strip():
                ev_data["oda_listesi"].append({
                    "oda_adi": oda_adi[i].strip(),
                    "uzunluk": oda_uzunluk[i],
                    "genislik": oda_genislik[i],
                    "yukseklik": oda_yukseklik[i],
                    "zemin_kaplama_tipi": oda_zemin_kaplama[i],
                    "duvar_kaplama_tipi": oda_duvar_kaplama[i]
                })

        for i in range(len(pencere_adet)):
            if pencere_adet[i] > 0:
                ev_data["pencere_listesi"].append({
                    "pencere_adi": f"Pencere {i+1}",
                    "genislik": pencere_genislik[i],
                    "yukseklik": pencere_yukseklik[i],
                    "adet": pencere_adet[i]
                })

        for i in range(len(kapi_adet)):
            if kapi_adet[i] > 0:
                ev_data["kapi_listesi"].append({
                    "kapi_adi": kapi_adi[i].strip(),
                    "genislik": kapi_genislik[i],
                    "yukseklik": kapi_yukseklik[i],
                    "adet": kapi_adet[i]
                })

        hesaplayici = EvHesaplayici(db, ev_data)
        malzeme_ihtiyaclari, toplam_maliyet = hesaplayici.malzeme_ihtiyacini_hesapla()

        oda_kaplama_tipleri = get_oda_kaplama_tipleri(db)
        return templates.TemplateResponse("index.html", {
            "request": request,
            "ev_data": ev_data,
            "malzeme_ihtiyaclari": malzeme_ihtiyaclari,
            "toplam_maliyet": toplam_maliyet,
            "oda_kaplama_tipleri": oda_kaplama_tipleri,
            "messages": messages # Mesajları template'e gönder
        })
    except ValueError as ve:
        messages.append({"type": "danger", "message": f"Geçersiz giriş: Lütfen sayısal alanlara doğru değerler girin. Hata: {ve}"})
        oda_kaplama_tipleri = get_oda_kaplama_tipleri(db)
        return templates.TemplateResponse("index.html", {
            "request": request,
            "oda_kaplama_tipleri": oda_kaplama_tipleri,
            "messages": messages
        })
    except Exception as e:
        messages.append({"type": "danger", "message": f"Beklenmedik bir hata oluştu: {e}"})
        oda_kaplama_tipleri = get_oda_kaplama_tipleri(db)
        return templates.TemplateResponse("index.html", {
            "request": request,
            "oda_kaplama_tipleri": oda_kaplama_tipleri,
            "messages": messages
        })


@app.get("/admin/fiyatlar", response_class=HTMLResponse)
async def get_admin_fiyatlar(request: Request, db: VeritabaniYoneticisi = Depends(get_db)):
    conn = db.baglan()
    if not conn:
        messages = [{"type": "danger", "message": "Veritabanı bağlantısı kurulamadı. Fiyatlar görüntülenemiyor."}]
        return templates.TemplateResponse('admin_fiyatlar.html', {"request": request, "malzemeler": [], "messages": messages})

    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            M.malzeme_id,
            M.malzeme_adi,
            M.birim_olcu_tipi,
            F.birim_fiyat,
            F.gecerlilik_tarihi
        FROM Malzemeler AS M
        LEFT JOIN (
            SELECT
                malzeme_id,
                birim_fiyat,
                gecerlilik_tarihi,
                ROW_NUMBER() OVER (PARTITION BY malzeme_id ORDER BY gecerlilik_tarihi DESC, fiyat_id DESC) as rn
            FROM Fiyatlar
        ) AS F ON M.malzeme_id = F.malzeme_id AND F.rn = 1
        ORDER BY M.malzeme_adi;
    """)
    malzemeler_ve_fiyatlar = cursor.fetchall()

    return templates.TemplateResponse('admin_fiyatlar.html', {"request": request, "malzemeler": malzemeler_ve_fiyatlar, "messages": []})


@app.post("/admin/fiyatlar", response_class=HTMLResponse) # veya RedirectResponse
async def post_admin_fiyatlar(request: Request, db: VeritabaniYoneticisi = Depends(get_db)):
    messages = []
    try:
        conn = db.baglan()
        if not conn:
            messages.append({"type": "danger", "message": "Veritabanı bağlantısı kurulamadı. Fiyatlar güncellenemedi."})
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="DB Connection Error")

        today_date = datetime.date.today().isoformat()
        form_data = await request.form()

        for key, value in form_data.items():
            if key.startswith('birim_fiyat_'):
                malzeme_id = key.replace('birim_fiyat_', '')
                birim_fiyat = float(value)

                existing_price_for_today = conn.execute("""
                    SELECT fiyat_id
                    FROM Fiyatlar
                    WHERE malzeme_id = ? AND gecerlilik_tarihi = ?
                """, (malzeme_id, today_date)).fetchone()

                if existing_price_for_today:
                    db.veri_guncelle(
                        "Fiyatlar",
                        {"birim_fiyat": birim_fiyat},
                        {"fiyat_id": existing_price_for_today['fiyat_id']}
                    )
                else:
                    db.veri_ekle(
                        "Fiyatlar",
                        {"malzeme_id": int(malzeme_id), "birim_fiyat": birim_fiyat, "gecerlilik_tarihi": today_date}
                    )
        messages.append({"type": "success", "message": "Fiyatlar başarıyla güncellendi!"})
        # Başarılı olduğunda GET isteğine yönlendir
        return RedirectResponse(url="/admin/fiyatlar", status_code=status.HTTP_303_SEE_OTHER)

    except ValueError:
        messages.append({"type": "danger", "message": "Geçersiz fiyat değeri girildi. Lütfen sayısal bir değer girin."})
    except HTTPException:
        pass # Already handled
    except Exception as e:
        messages.append({"type": "danger", "message": f"Fiyatlar güncellenirken bir hata oluştu: {e}"})

    # Hata durumunda formu tekrar render et
    conn = db.baglan()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            M.malzeme_id,
            M.malzeme_adi,
            M.birim_olcu_tipi,
            F.birim_fiyat,
            F.gecerlilik_tarihi
        FROM Malzemeler AS M
        LEFT JOIN (
            SELECT
                malzeme_id,
                birim_fiyat,
                gecerlilik_tarihi,
                ROW_NUMBER() OVER (PARTITION BY malzeme_id ORDER BY gecerlilik_tarihi DESC, fiyat_id DESC) as rn
            FROM Fiyatlar
        ) AS F ON M.malzeme_id = F.malzeme_id AND F.rn = 1
        ORDER BY M.malzeme_adi;
    """)
    malzemeler_ve_fiyatlar = cursor.fetchall()
    return templates.TemplateResponse('admin_fiyatlar.html', {"request": request, "malzemeler": malzemeler_ve_fiyatlar, "messages": messages})

# Uygulama başlatıldığında çalıştırılacak global db yöneticisi
db_yoneticisi = VeritabaniYoneticisi()

# main.py veya başka bir dosyada `uvicorn app:app --reload` ile çalıştırılabilir.
# `if __name__ == '__main__':` bloğu, FastAPI ile genellikle uvicorn üzerinden çalıştığı için kaldırılır.