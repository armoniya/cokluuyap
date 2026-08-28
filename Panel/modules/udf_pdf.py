# -*- coding: utf-8 -*-
"""
UDF -> PDF dönüştürücü
========================
`UDF Converter GUI/converter.py`'nin TERSİ (kullanıcı isteği, 2026-08-27:
"O modülde pdf udf haline dönüştürülüyor. Bu modülde de tam tersi olacak.").

UDF dosyası bir ZIP'tir, içinde `content.xml` taşır (bkz. converter.py::
docx_to_udf_xml/pdf_to_udf_xml — bu modül YAZDIĞI için şema TAHMİN DEĞİL,
doğrudan o kodun ürettiği format okunarak çıkarıldı):

  <template format_id="1.8">
    <content><![CDATA[TÜM belge metni, TEK blok]]></content>
    <properties><pageFormat leftMargin=".." rightMargin=".." topMargin=".."
                            bottomMargin=".." /></properties>
    <elements resolver="hvl-default">
      <paragraph Alignment="0|1|2|3" ...>
        <content startOffset=".." length=".." family=".." size=".."
                 bold="true|false" italic="true|false" foreground=".." />
        <tab .../>  <image imageData="base64" .../>
      </paragraph>
      <table columnCount=".." columnSpans="c1,c2,.." border="borderCell|borderNone">
        <row><cell><paragraph>...</paragraph></cell>...</row>
      </table>
    </elements>
  </template>

`startOffset`/`length` TÜM belge boyunca GLOBAL'dir (paragraf başına değil) —
her run, `<content>` CDATA'sındaki metnin bir dilimidir.

Yazı tipi AİLESİ isimleri (Times New Roman/Arial/Calibri/Tahoma/...) gerçek
Windows TTF dosyalarına eşlenir (`C:\\Windows\\Fonts`) — Base-14 PDF fontları
YERİNE, çünkü Base-14 Türkçe ı/İ/ğ/Ğ/ş/Ş karakterlerini İÇERMEZ (WinAnsi/
Latin-1 dışı) ve hukuki metinlerde bu harfler kaçınılmaz. TTF bulunamazsa
(Windows dışı ortam) fitz'in Base-14 karşılığına düşülür — bu durumda Türkçe
harfler eksik/kutu görünebilir, bu BİLİNEN bir sınırlamadır.

Satır sarma/yükseklik KABA bir tahminle hesaplanır (karakter genişliği
yaklaşımı) — piksel-birebir Word/UYAP dökümü DEĞİL, ama metin/hizalama/
kalın-italik/tablo yapısı content.xml'den BİREBİR okunur, hiçbir içerik
UYDURULMAZ.
"""

import base64
import io
import math
import os
import xml.etree.ElementTree as ET
import zipfile

import fitz  # PyMuPDF — projede zaten bağımlılık (UDF Converter GUI/converter.py)

PAGE_W, PAGE_H = 595.0, 842.0  # A4, pt (converter.py'nin varsayılan marjlarıyla uyumlu)
_VARSAYILAN_MARJ = 42.525

_WINFONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# Aile adı (converter.py'nin ürettiği/rFonts'tan gelen isimler) -> (normal, bold, italic, bold-italic) TTF dosya adları.
_AILE_TTF = {
    "times new roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "courier new": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
}
_BASE14 = {  # TTF bulunamazsa düşülen fallback (fitz kısa adları)
    "times new roman": ("tiro", "tibo", "tiit", "tibi"),
    "courier new": ("cour", "cobo", "coit", "cobi"),
}


class _FontKayit:
    """Bir fitz.Document içinde ihtiyaç duyulan (aile, bold, italic)
    kombinasyonlarını tembel biçimde kaydeder (`insert_font`), tekrar tekrar
    aynı TTF'i gömmemek için ada göre önbellekler."""

    def __init__(self, doc):
        self.doc = doc
        self._onbellek = {}

    def coz(self, family, bold, italic):
        """Döner: (fontname, fontfile) — `fontfile` doluysa `insert_textbox`'a
        DOĞRUDAN geçilmeli (canlı test, 2026-08-27: `fitz.Document.insert_font`
        DİYE BİR METOD YOK — `Page.insert_textbox(..., fontfile=...)` kendi
        içinde gömer; TTF yerine yanlışlıkla Base-14'e düşülürse Türkçe ı/İ/
        ş/Ş/ğ/Ğ WinAnsi dışı olduğundan '?' ile render edilir — bu YÜZDEN
        gerçek Windows TTF'i kullanmak, salt Base-14 fallback DEĞİL, ZORUNLU)."""
        anahtar = (str(family or "").strip().lower(), bool(bold), bool(italic))
        if anahtar in self._onbellek:
            return self._onbellek[anahtar]
        aile, kalin, egik = anahtar
        idx = (2 if egik else 0) + (1 if kalin else 0)
        dosyalar = _AILE_TTF.get(aile)
        sonuc = None
        if dosyalar:
            yol = os.path.join(_WINFONTS, dosyalar[idx])
            if os.path.isfile(yol):
                sonuc = (f"udfpdf-{aile.replace(' ', '')}-{idx}", yol)
        if sonuc is None:
            temel = _BASE14.get(aile, ("helv", "hebo", "heit", "hebi"))
            sonuc = (temel[idx] if idx < len(temel) else temel[0], None)
        self._onbellek[anahtar] = sonuc
        return sonuc


def _argb_to_rgb01(argb_str):
    try:
        v = int(argb_str)
        if v < 0:
            v += 1 << 32
        r = (v >> 16) & 255
        g = (v >> 8) & 255
        b = v & 255
        return (r / 255.0, g / 255.0, b / 255.0)
    except Exception:
        return (0.0, 0.0, 0.0)


_ALIGN_MAP = {"0": fitz.TEXT_ALIGN_LEFT, "1": fitz.TEXT_ALIGN_CENTER,
              "2": fitz.TEXT_ALIGN_RIGHT, "3": fitz.TEXT_ALIGN_JUSTIFY}


def _satir_sayisi_tahmin(metin, size, genislik):
    """Kaba karakter-genişliği tahmini (piksel-birebir DEĞİL) — ortalama glif
    genişliğini `size`'ın ~0.5 katı varsayar (Times/Arial gibi orantılı
    fontlar için makul bir yaklaşım)."""
    if not metin:
        return 1
    satirlar = metin.split("\n")
    toplam = 0
    char_w = size * 0.5
    karakter_basi = max(1, int(genislik / char_w)) if char_w else 80
    for s in satirlar:
        toplam += max(1, math.ceil(len(s) / karakter_basi)) if s else 1
    return max(1, toplam)


class _SayfaYazici:
    def __init__(self, doc, fontlar, left_m, right_m, top_m, bottom_m):
        self.doc = doc
        self.fontlar = fontlar
        self.left_m, self.right_m = left_m, right_m
        self.top_m, self.bottom_m = top_m, bottom_m
        self.usable_w = PAGE_W - left_m - right_m
        self.sayfa = None
        self.y = 0.0
        self._yeni_sayfa()

    def _yeni_sayfa(self):
        self.sayfa = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y = self.top_m

    def _yer_ac(self, yukseklik):
        if self.y + yukseklik > PAGE_H - self.bottom_m:
            self._yeni_sayfa()

    def paragraf_yaz(self, metin, align, family, size, bold, italic, foreground=None, sol_girinti=0.0):
        if not metin.strip():
            self.y += max(size, 8) * 1.3
            return
        fontname, fontfile = self.fontlar.coz(family, bold, italic)
        color = _argb_to_rgb01(foreground) if foreground else (0.0, 0.0, 0.0)
        genislik = max(20.0, self.usable_w - sol_girinti)
        yukseklik = _satir_sayisi_tahmin(metin, size, genislik) * size * 1.35 + 4
        self._yer_ac(min(yukseklik, PAGE_H - self.top_m - self.bottom_m))
        rect = fitz.Rect(self.left_m + sol_girinti, self.y,
                          self.left_m + sol_girinti + genislik, PAGE_H - self.bottom_m)
        self.sayfa.insert_textbox(rect, metin, fontsize=size, fontname=fontname, fontfile=fontfile,
                                   align=_ALIGN_MAP.get(align, fitz.TEXT_ALIGN_LEFT), color=color)
        self.y += yukseklik

    def resim_yaz(self, png_bayt, width, height):
        try:
            genislik = min(float(width or 100), self.usable_w)
            yukseklik = min(float(height or 100), PAGE_H - self.top_m - self.bottom_m - 10)
        except Exception:
            genislik, yukseklik = 100.0, 100.0
        self._yer_ac(yukseklik + 6)
        rect = fitz.Rect(self.left_m, self.y, self.left_m + genislik, self.y + yukseklik)
        try:
            self.sayfa.insert_image(rect, stream=png_bayt)
        except Exception:
            pass
        self.y += yukseklik + 6

    def tablo_yaz(self, satirlar, sutun_paylari, kenarlikli):
        """`satirlar`: list[list[str]] (hücre metinleri, hücre başına paragraflar
        '\\n' ile birleştirilmiş). `sutun_paylari`: list[float] (0-1 arası oran)."""
        if not satirlar:
            return
        genislikler = [self.usable_w * p for p in sutun_paylari]
        satir_font_size = 10
        for satir in satirlar:
            hucre_yukseklikleri = []
            for hucre, w in zip(satir, genislikler):
                hucre_yukseklikleri.append(_satir_sayisi_tahmin(hucre, satir_font_size, w) * satir_font_size * 1.35 + 6)
            satir_h = max(hucre_yukseklikleri) if hucre_yukseklikleri else satir_font_size * 1.35 + 6
            self._yer_ac(satir_h)
            x = self.left_m
            for hucre, w in zip(satir, genislikler):
                rect = fitz.Rect(x, self.y, x + w, self.y + satir_h)
                if kenarlikli:
                    self.sayfa.draw_rect(rect, color=(0.5, 0.5, 0.5), width=0.5)
                fontname, fontfile = self.fontlar.coz("Times New Roman", False, False)
                self.sayfa.insert_textbox(rect + (2, 2, -2, -2), hucre, fontsize=satir_font_size,
                                          fontname=fontname, fontfile=fontfile, align=fitz.TEXT_ALIGN_LEFT)
                x += w
            self.y += satir_h


def _runlari_isle(el, full_text, yazici, align, sol_girinti):
    """Bir `<paragraph>` altındaki `<content>`/`<tab>`/`<image>` çocuklarını
    sırayla işler. Basitleştirme (bkz. modül başlığı): paragraf İÇİ birden
    fazla yazı tipi/boyut karışıksa TEK bir kutu yerine HER run KENDİ
    satırında yazılır (ayrı stil kutucukları) — aynı satırda karışık stil
    yan yana DİZİLMEZ, ama hiçbir metin KAYBOLMAZ/UYDURULMAZ."""
    for child in el:
        etiket = child.tag
        if etiket in ("content", "tab"):
            try:
                start = int(child.get("startOffset", "0"))
                length = int(child.get("length", "0"))
            except ValueError:
                continue
            parca = full_text[start:start + length]
            if not parca.strip("\n"):
                continue
            yazici.paragraf_yaz(
                parca, align,
                child.get("family", "Times New Roman"),
                float(child.get("size", "12") or 12),
                child.get("bold", "false") == "true",
                child.get("italic", "false") == "true",
                child.get("foreground"),
                sol_girinti,
            )
        elif etiket == "image":
            try:
                png = base64.b64decode(child.get("imageData", ""))
                yazici.resim_yaz(png, child.get("width"), child.get("height"))
            except Exception:
                pass


def _hucre_metni(cell_el, full_text):
    parcalar = []
    for p in cell_el.findall("paragraph"):
        for child in p:
            if child.tag == "content":
                try:
                    start = int(child.get("startOffset", "0"))
                    length = int(child.get("length", "0"))
                except ValueError:
                    continue
                parcalar.append(full_text[start:start + length])
    return "".join(parcalar).strip()


def udf_zip_mi(ham_bayt):
    """`ham_bayt` bir UDF (zip + content.xml) mi? — Content-Type başlığına
    DEĞİL, gerçek ZIP imzasına bakar (daha güvenilir; UYAP'ın bu tür evrak
    için hangi Content-Type döndüreceği canlı doğrulanmadı)."""
    if not ham_bayt or ham_bayt[:2] != b"PK":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(ham_bayt)) as z:
            return "content.xml" in z.namelist()
    except Exception:
        return False


def udf_pdf_uret(ham_bayt):
    """UDF baytlarından bir PDF üretir, PDF baytlarını döner. Şema
    beklenenden farklıysa (KeyError/ET.ParseError/vb.) istisna fırlatır —
    çağıran bunu placeholder-sayfa fallback'i için yakalamalı (uydurma
    render YAPILMAZ, bkz. evrak_indirici.py)."""
    with zipfile.ZipFile(io.BytesIO(ham_bayt)) as z:
        xml_bytes = z.read("content.xml")
    root = ET.fromstring(xml_bytes)

    content_el = root.find("content")
    full_text = content_el.text or "" if content_el is not None else ""

    pf = root.find("properties/pageFormat")
    left_m = float(pf.get("leftMargin", _VARSAYILAN_MARJ)) if pf is not None else _VARSAYILAN_MARJ
    right_m = float(pf.get("rightMargin", _VARSAYILAN_MARJ)) if pf is not None else _VARSAYILAN_MARJ
    top_m = float(pf.get("topMargin", _VARSAYILAN_MARJ)) if pf is not None else _VARSAYILAN_MARJ
    bottom_m = float(pf.get("bottomMargin", _VARSAYILAN_MARJ)) if pf is not None else _VARSAYILAN_MARJ

    elements_el = root.find("elements")

    doc = fitz.open()
    fontlar = _FontKayit(doc)
    yazici = _SayfaYazici(doc, fontlar, left_m, right_m, top_m, bottom_m)

    for el in (list(elements_el) if elements_el is not None else []):
        if el.tag == "paragraph":
            align = el.get("Alignment", "0")
            sol_girinti = float(el.get("LeftIndent", "0") or 0)
            _runlari_isle(el, full_text, yazici, align, sol_girinti)
        elif el.tag == "table":
            sutunlar_str = el.get("columnSpans", "")
            try:
                paylar = [float(x) for x in sutunlar_str.split(",") if x.strip()]
            except ValueError:
                paylar = []
            toplam = sum(paylar) or 1.0
            paylar = [p / toplam for p in paylar] if paylar else []
            satirlar = []
            for row in el.findall("row"):
                hucreler = row.findall("cell")
                if not paylar or len(paylar) != len(hucreler):
                    paylar = [1.0 / max(1, len(hucreler))] * len(hucreler)
                satirlar.append([_hucre_metni(c, full_text) for c in hucreler])
            yazici.tablo_yaz(satirlar, paylar, el.get("border", "borderCell") == "borderCell")

    buf = doc.tobytes()
    doc.close()
    return buf
