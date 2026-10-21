"""
Extragerea specificațiilor unui laptop din titlul produsului.

Titlurile de la cele trei magazine conțin aproape toate specificațiile, în forme diferite:
    eMAG    Laptop Gaming Lenovo Legion 5 15IAX11 cu procesor Intel Core Ultra 7 251HX pana la 5.1GHz,
            15.3" WQXGA, OLED, 165Hz, 32GB DDR5, 1TB SSD, NVIDIA GeForce RTX 5070 12GB GDDR7, No OS, ...
    evoMAG  Laptop ASUS Vivobook 16 M1605NAQ (Procesor AMD Ryzen 7 170 (16M Cache, up to 4.75 GHz),
            16" WUXGA, 16GB, 512GB SSD, AMD Radeon 660M Graphics, Negru)
    CEL     Laptop gaming 17.3" Asus TUF A17 AMD Ryzen 7 7445HS (22MB 4.7GHz) 16GB DDR5 512GB SSD
            RTX 4050 6GB FHD 144Hz No OS Mecha Gray

parse_specs(titlu) întoarce un dicționar cu coloanele din SPEC_COLUMNS; ce nu apare în titlu rămâne None.
Singura deducție: un laptop obișnuit (nu de gaming, nu stație de lucru) fără placă video în titlu are placa
integrată a procesorului (gpu_dedicated=False, gpu_vram_gb=0), fiindcă plăcile dedicate sunt trecute în titlu.
Test rapid:
    python extraction/specs.py "Laptop ASUS Vivobook 16 (Procesor AMD Ryzen 7 170, 16GB, 512GB SSD)"
"""
import json
import re
import sys

from parsing import norm

SPEC_COLUMNS = [
    "brand", "cpu_brand", "cpu_model", "cpu_tier", "cpu_suffix", "cpu_cores",
    "ram_gb", "ram_type", "storage_gb",
    "gpu_brand", "gpu_model", "gpu_dedicated", "gpu_vram_gb",
    "screen_in", "resolution", "megapixels", "refresh_hz", "panel", "touch",
    "os", "is_gaming", "is_refurbished",
]


def _normalize(title):
    """Text pentru căutări (fără diacritice, litere mici), cu ghilimelele de inch unificate."""
    t = norm(title or "").replace("ᵉ", "e")  # 'Iris Xᵉ'
    for q in ("''", "”", "“", "″"):
        t = t.replace(q, '"')
    return t


# ---------------------------------------------------------------- brand

# sub-mărcile duc la producător (Legion -> Lenovo, ROG -> ASUS, Omen -> HP ...)
BRANDS = [
    ("apple|macbook", "Apple"),
    ("lenovo|thinkpad|thinkbook|ideapad|legion|yoga|loq", "Lenovo"),
    ("asus|rog|tuf|vivobook|zenbook|proart|expertbook", "ASUS"),
    ("hp|omen|victus|pavilion|elitebook|probook|envy|omnibook|spectre|zbook", "HP"),
    ("dell|alienware|xps|latitude|inspiron|vostro|precision", "Dell"),
    ("acer|predator|nitro|aspire|swift|travelmate|extensa", "Acer"),
    (r"msi|katana|stealth|raider|titan(?!\s*gr[ae]y)|cyborg|crosshair|sword|vector|prestige", "MSI"),
    ("gigabyte|aorus", "Gigabyte"),
    ("razer|blade", "Razer"),
    ("samsung|galaxy book", "Samsung"),
    ("microsoft|surface", "Microsoft"),
    ("huawei|matebook", "Huawei"),
    ("honor|magicbook", "Honor"),
    ("xiaomi|redmibook", "Xiaomi"),
    ("lg", "LG"),
    ("medion|erazer", "Medion"),
    ("dynabook|toshiba", "Dynabook"),
    ("fujitsu", "Fujitsu"),
    ("panasonic|toughbook", "Panasonic"),
    ("chuwi", "Chuwi"),
    ("blackview", "Blackview"),
]
BRAND_RES = [(re.compile(rf"\b(?:{pat})\b"), brand) for pat, brand in BRANDS]
NOT_BRANDS = {"gaming", "business", "convertibil", "ultraportabil", "pentru", "student"}


def _brand(t):
    """Marca găsită cel mai devreme în titlu; altfel cuvântul de după 'Laptop' (mărci mici din marketplace)."""
    hits = [(m.start(), brand) for rx, brand in BRAND_RES if (m := rx.search(t))]
    if hits:
        return min(hits)[1]
    m = re.match(r"(?:nou!?\s*)?(?:laptop|notebook|ultrabook)\s+(?:gaming\s+)?(?:[\d.,]+\s*\"\s*)?([a-z][a-z0-9-]{2,})", t)
    if not m or m.group(1) in NOT_BRANDS:
        return None
    word = m.group(1)  # ex. 'lenovothinkpad' (scris lipit) -> Lenovo
    return next((brand for pat, brand in BRANDS if re.match(rf"(?:{pat})", word)), word.capitalize())


# ---------------------------------------------------------------- procesor

def _suffix(number):
    """'13650hx' -> 'HX', '255u' -> 'U', '170' -> None"""
    m = re.search(r"\d([a-z]+)$", number)
    return m.group(1).upper() if m else None


def _cpu(t, brand):
    """(producător, model, nivel 3/5/7/9, sufix U/H/HX...)"""
    if brand == "Apple" or "apple m" in t or "apple a" in t:
        m = (re.search(r"\b(m[1-5])(?:\s+(pro|max|ultra))?\b", t)
             or re.search(r"\b(a1[4-9])(?:\s+(pro|bionic))?\b", t))  # MacBook Neo: Apple A18 Pro
        if m:
            return "Apple", m.group(1).upper() + (f" {m.group(2).capitalize()}" if m.group(2) else ""), None, None
    if re.search(r"\bn1x\b", t) and "nvidia" in t:  # NVIDIA N1X (ARM), cu GPU RTX Spark integrat
        return "NVIDIA", "N1X", None, None
    m = re.search(r"snapdragon\s*(x\d?)\s*(elite\s*extreme|elite|plus)?\s*(x\d[a-z]?\s*-?\s*\d{2}\s*-?\s*\d{3})?", t)
    if m:
        parts = ["Snapdragon", m.group(1).upper(), (m.group(2) or "").title(), re.sub(r"\s", "", m.group(3) or "").upper()]
        return "Qualcomm", " ".join(p for p in parts if p), None, None
    ultra = re.search(r"\bultra\s*(x?[579])\b", t)  # 'Ultra 7', 'Ultra X9' (Panther Lake)
    m = re.search(r"(?:core\s*)?ultra\s*(x?[579])(?:\s+|\s*-\s*)(\d{3}[a-z]{0,2})\b", t)  # 'Ultra X7-358H'
    if m:
        return "Intel", f"Core Ultra {m.group(1).upper()} {m.group(2).upper()}", int(m.group(1)[-1]), _suffix(m.group(2))
    m = re.search(r"\bi([3579])\s*(?:processor\s+)?-?\s*(n?\d{3,5}[a-z]{0,2})\b", t)
    if m:
        return "Intel", f"Core i{m.group(1)}-{m.group(2).upper()}", int(m.group(1)), _suffix(m.group(2))
    m = re.search(r"\b(?:intel\s+core|intel|core)\s+([3579])\s+(\d{3}[a-z]{0,2})\b", t)  # 'Intel Core 5 120U', 'Intel 9 270H'
    if m:
        return "Intel", f"Core {m.group(1)} {m.group(2).upper()}", int(m.group(1)), _suffix(m.group(2))
    m = re.search(r"\bintel\s+(?:core\s+)?(\d{3}(?:hx|h|u|v))\b", t)  # 'Intel 285HX', 'Intel 240H': nivelul separat sau lipsă
    if m:
        tier = int(ultra.group(1)[-1]) if ultra else None
        name = f"Core Ultra {ultra.group(1).upper()} {m.group(1).upper()}" if ultra else f"Core {m.group(1).upper()}"
        return "Intel", name, tier, _suffix(m.group(1))
    if ultra:  # 'Intel Core Ultra 9' fără model
        return "Intel", f"Core Ultra {ultra.group(1).upper()}", int(ultra.group(1)[-1]), None
    m = re.search(r"\bcore\s*i([3579])\b", t)
    if m:
        return "Intel", f"Core i{m.group(1)}", int(m.group(1)), None
    # 'a[il]': unele titluri scriu 'Ryzen Al 9' (cu L în loc de I)
    m = re.search(r"ryzen\s*a[il]\s*max\s*(\+?)\s*(?:pro\s*)?(\d{3})\b", t)  # 'Max+ 395', 'Max 390'
    if m:
        return "AMD", f"Ryzen AI Max{m.group(1)} {m.group(2)}", 9, None
    m = re.search(r"ryzen\s*(a[il]\s*)?([3579])\s+(pro\s+)?(hx\s+)?(\d{3,4}[a-z]{0,3})\b", t)
    if m:
        name = ("Ryzen AI " if m.group(1) else "Ryzen ") + m.group(2) + (" PRO" if m.group(3) else "")
        name += (" HX" if m.group(4) else "") + " " + m.group(5).upper()
        return "AMD", name, int(m.group(2)), "HX" if m.group(4) else _suffix(m.group(5))
    m = re.search(r"ryzen\s*(a[il]\s*)?([3579])\b", t)  # model lipsă sau trunchiat ('Ryzen 3 30')
    if m:
        return "AMD", ("Ryzen AI " if m.group(1) else "Ryzen ") + m.group(2), int(m.group(2)), None
    m = re.search(r"\b(celeron|pentium(?:\s+silver|\s+gold)?|atom)\s*(n?\d{3,4}[a-z]?)?", t)
    if m:
        return "Intel", m.group(1).title() + (f" {m.group(2).upper()}" if m.group(2) else ""), None, None
    m = re.search(r"\bintel\s+(?:processor\s+)?(n\d{2,3})\b", t)
    if m:
        return "Intel", m.group(1).upper(), None, None
    for word, maker in (("intel", "Intel"), ("amd", "AMD"), ("mediatek", "MediaTek")):  # doar producătorul
        if re.search(rf"\b{word}\b", t):
            return maker, None, None, None
    return None, None, None, None


def _cores(t):
    """Nucleele procesorului: '24 C / 24 T', '10-core CPU', 'CPU 15-core', '18 nuclee', '16 cores',
    nu ale plăcii video ('16-core GPU', 'GPU 16-core', '40 nuclee GPU').
    Eticheta stă fie după număr ('15-core CPU 16-core GPU'), fie înainte ('CPU 15-core GPU 16-core');
    stilul titlului se vede la prima apariție."""
    m = re.search(r"\b(\d{1,2})\s*c\s*/\s*\d{1,2}\s*t\b", t)
    if m:
        return int(m.group(1))
    found = list(re.finditer(r"\b(\d{1,2})\s*-?\s*(?:nuclee|cores?)\b", t))
    if found:
        label_first = re.search(r"\b(?:cpu|gpu)\s*:?\s*$", t[:found[0].start()])
        labels = []
        for m in found:
            lab = (re.search(r"\b(cpu|gpu)\s*:?\s*$", t[:m.start()]) if label_first
                   else re.match(r"\s*(cpu|procesor|gpu|grafic|graphics)", t[m.end():]))
            labels.append(lab.group(1) if lab else None)
        for want in (("cpu", "procesor"), (None,)):  # întâi cel etichetat CPU, apoi primul fără etichetă
            m = next((m for m, lab in zip(found, labels) if lab in want), None)
            if m:
                return int(m.group(1))
    m = re.search(r"\b(dual|quad|hexa|octa)\s*-?\s*core", t)
    return {"dual": 2, "quad": 4, "hexa": 6, "octa": 8}[m.group(1)] if m else None


# ---------------------------------------------------------------- memorie și stocare

SIZE_RE = re.compile(r"(?<![\d.,])(\d{1,4}(?:[.,]\d{1,2})?)\s*(tb|gb|g)\b")
RAM_SIZES = {4, 8, 12, 16, 18, 20, 24, 32, 36, 40, 48, 64, 96, 128, 192}


def _sizes(t):
    """(RAM, stocare, memorie video) în GB, după contextul fiecărui 'N GB/TB' din titlu.
    O etichetă dintre două valori poate ține de oricare ('24GB RAM 1TB SSD', 'RAM 16GB SSD 256GB'),
    așa că decide și mărimea: RAM-ul are cel mult 192 GB, stocarea cel puțin 128 GB."""
    ram, storage, vram, other = [], [], [], []
    prev_end = 0
    for m in SIZE_RE.finditer(t):
        value = float(m.group(1).replace(",", ".")) * (1024 if m.group(2) == "tb" else 1)
        before, after = t[max(prev_end, m.start() - 25):m.start()], t[m.end():m.end() + 14]
        prev_end = m.end()
        storage_label = (re.search(r"(?:ssd|hdd|emmc|stocare|storage)(?:\s+de)?\s*:?\s*$", before)
                         or re.match(r"\s*(?:de\s+)?(?:ssd|hdd|emmc|ufs|nvme|pcie|m\.2|stocare|storage)", after))
        if re.search(r"(?:ram|memorie)\s*:?\s*$", before) and value <= 192:
            ram.append(value)
        elif storage_label and value >= 128:
            storage.append(value)
        elif (re.match(r"[\s,]*(?:gddr\d|vram|dedicat)", after)
                or re.search(r"(?:rtx|gtx|rx|arc a\d+m?|mx\s*\d+)[\w\s@-]{0,12}$", before)):
            vram.append(value)
        elif re.match(r"\s*(?:(?:lp)?ddr\d|ram\b|memorie|unified)", after) and value <= 192:
            ram.append(value)
        else:
            other.append(value)
    # fără context: prima valoare tipică pentru RAM e memoria, următoarea de cel puțin 128 GB e stocarea
    ram_gb = ram[0] if ram else next((v for v in other if v in RAM_SIZES), None)
    if not storage:
        storage = [v for v in other if v >= 128 and v != ram_gb]
    as_int = lambda v: int(round(v)) if v is not None else None  # noqa: E731
    return as_int(ram_gb), as_int(storage[0] if storage else None), as_int(vram[0] if vram else None)


# ---------------------------------------------------------------- placa video

def _gpu(t, cpu_brand):
    """(producător, model, dedicată True/False); (None, None, None) dacă titlul nu o menționează"""
    if re.search(r"rtx\s*spark", t):  # integrat în procesorul NVIDIA N1X
        return "NVIDIA", "RTX Spark", False
    m = re.search(r"\b(rtx|gtx)[\s,]*(pro\s+)?(a?\d{3,4})(\s*(?:ti|super))?(\s*ada)?\b", t)  # și 'RTX, 4050'
    if m:
        name = f"{m.group(1).upper()}{' PRO' if m.group(2) else ''} {m.group(3).upper()}"
        name += (f" {m.group(4).strip().title()}" if m.group(4) else "") + (" Ada" if m.group(5) else "")
        return "NVIDIA", name, True
    m = re.search(r"\b(?:nvidia|geforce)\s+(?:geforce\s+)?([2-5]0[5-9]0)\b", t)  # 'nVidia 5090'
    if m:
        return "NVIDIA", f"RTX {m.group(1)}", True
    m = re.search(r"\bmx\s*(\d{3}a?)\b", t)
    if m:
        return "NVIDIA", f"MX{m.group(1).upper()}", True
    m = re.search(r"\bquadro\s+(t\d{3,4}|p\d{3,4})\b|\bnvidia\s+(t\d{3,4})\b", t)  # 'Quadro T1000', 'NVIDIA T550'
    if m:
        name = (m.group(1) or m.group(2)).upper()
        return "NVIDIA", name if name.startswith("T") else f"Quadro {name}", True
    if re.search(r"\b(?:geforce|nvidia)\b", t):
        return "NVIDIA", None, True
    m = re.search(r"\b(?:radeon\s*)?rx\s*(\d{4}[a-z]{0,2})\b", t)
    if m:
        return "AMD", f"Radeon RX {m.group(1).upper()}", True
    m = re.search(r"\barc\s*(a\d{3}m)\b", t)
    if m:
        return "Intel", f"Arc {m.group(1).upper()}", True
    # integrate în procesor
    m = re.search(r"\bradeon[^,()]{0,40}?\b(\d{3}m|\d{4}s)\b", t)  # 860M, 8060S
    if m:
        return "AMD", f"Radeon {m.group(1).upper()}", False
    if "radeon" in t:
        return "AMD", "Radeon Graphics", False
    m = re.search(r"\barc\s*(1[34]0[tv]|b3\d0)\b", t) or ("arc" in t and re.search(r"\b(b3\d0)\b", t))  # B390: Panther Lake
    if m:
        return "Intel", f"Arc {m.group(1).upper()}", False
    if re.search(r"\barc\b", t):
        return "Intel", "Arc Graphics", False
    if re.search(r"iris\s*xe", t):
        return "Intel", "Iris Xe", False
    if re.search(r"\buhd\s*(?:graphics|\d{3})|\b(?:intel|gma)\s*uhd\b|\bhd graphics", t):  # 'Intel UHD', 'GMA UHD'
        return "Intel", "UHD Graphics", False
    integrated = re.search(r"graphics|grafic\w*\s+(?:intel|integrat)|video\s+integrat", t)  # 'grafica intel'
    if integrated and cpu_brand == "Intel":
        return "Intel", "Intel Graphics", False
    if integrated and cpu_brand == "AMD":
        return "AMD", "Radeon Graphics", False
    if "adreno" in t:
        return "Qualcomm", "Adreno", False
    m = re.search(r"\b(\d{1,2})\s*-?\s*(?:core|nuclee)\s*(?:gpu|graphics|grafic)"
                  r"|\bgpu\s*:?\s*(\d{1,2})\s*-?\s*(?:core|nuclee)", t)
    if m or cpu_brand == "Apple":
        n = (m.group(1) or m.group(2)) if m else None
        return "Apple", f"{n}-core GPU" if n else "Apple GPU", False
    return None, None, None


# ---------------------------------------------------------------- ecran

# (regex, etichetă, (lățime, înălțime) sau None dacă eticheta e folosită pentru mai multe rezoluții)
RESOLUTIONS = [
    (r"wquxga|4k\s*\+|uhd\s*\+", "WQUXGA", (3840, 2400)),
    (r"\b4k\b|\buhd\b(?!\s*(?:graphics|\d))", "4K", (3840, 2160)),
    (r"3\.2k", "3.2K", (3200, 2000)),
    (r"\b3k\b", "3K", None),
    (r"2\.8k", "2.8K", (2880, 1800)),
    (r"2\.5k|wqxga|qhd\s*\+", "QHD+", (2560, 1600)),
    (r"\bw?qhd\b|1440p", "QHD", (2560, 1440)),
    (r"2\.2k", "2.2K", (2240, 1400)),
    (r"\b2k\b", "2K", None),
    (r"wuxga|fhd\s*\+|full\s*hd\s*\+", "FHD+", (1920, 1200)),
    (r"\bfhd\b|full\s*hd|1080p", "FHD", (1920, 1080)),
    (r"\bhd\s*\+", "HD+", (1600, 900)),
    (r"\bhd\b(?!\s*(?:graphics|\d))", "HD", (1366, 768)),
    (r"retina", "Retina", None),
]


def _screen(t):
    """Diagonala în inch: '15.6"', '15,6 inci', '14inch', '16 inch', 'MacBook Pro 14' (nu '16x10inch')"""
    m = (re.search(r"(?<![\d.,x×])(1\d(?:[.,]\d{1,2})?)\s*(?:\"|-?inch|inci|inchi)", t)
         or re.search(r"macbook\s+(?:air|pro)\s+(1[3-6])\b", t))
    return float(m.group(1).replace(",", ".")) if m else None


def _resolution(t):
    """(rezoluție, megapixeli): '2560x1600' dacă apare explicit, altfel eticheta (FHD+, QHD+, 2.8K...)"""
    m = re.search(r"\b(\d{4})\s*[x×]\s*(\d{3,4})\b", t)
    if m and 1000 <= int(m.group(1)) <= 4000 and 600 <= int(m.group(2)) <= 3000:
        w, h = int(m.group(1)), int(m.group(2))
        return f"{w}x{h}", round(w * h / 1e6, 2)
    for pat, label, dims in RESOLUTIONS:
        if re.search(pat, t):
            return label, round(dims[0] * dims[1] / 1e6, 2) if dims else None
    return None, None


def _panel(t):
    if re.search(r"\b(?:a?oled)\b", t):
        return "OLED"
    if re.search(r"mini\s*-?\s*led|\bxdr\b", t):
        return "Mini-LED"
    if re.search(r"\bips\b|retina", t):
        return "IPS"
    return None


# ---------------------------------------------------------------- restul

def _os(t, brand):
    if re.search(r"\bwin(?:dows)?\s*1[01]\s*pro\b", t):
        return "Windows Pro"
    if re.search(r"\bwin(?:dows)?\s*1[01]\b|\bwindows\b", t):
        return "Windows"
    if re.search(r"\bno\s*os\b|fara\s*(?:os|sistem)|free\s*-?dos|\bdos\b|without\s*os", t):
        return "Fara OS"
    if re.search(r"mac\s*os", t) or brand == "Apple":
        return "macOS"
    if re.search(r"linux|ubuntu", t):
        return "Linux"
    if re.search(r"chrome\s*os|chromebook", t):
        return "ChromeOS"
    return None


# stații de lucru și modele de creație: au des placă dedicată nemenționată în titlu
WORKSTATION_RE = re.compile(r"workstation|zbook|precision|pro\s*max|\bxps\s*1[5-7]\b|thinkpad\s*p1\d?\b(?!s)|"
                            r"creator|studio")
GAMING_RE = re.compile(r"gaming|\b(?:legion|loq|rog|tuf|strix|zephyrus|omen|victus|predator|nitro|katana|"
                       r"alienware|aorus|raider|stealth|titan(?!\s*gr[ae]y)|cyborg|vector|crosshair|sword|helios|"
                       r"blade|erazer)\b")
TOUCH_RE = re.compile(r"\btouch(?:screen)?\b|\btactil|\b2\s*-?\s*in\s*-?\s*1\b|\b2in1\b|\bx360\b|\bflip\b|convertibil")
REFURB_RE = re.compile(r"refurbished|renewed|resigilat|reconditionat|second\s*hand|\bgrade\s*[abc]\b")


def parse_specs(title):
    """Specificațiile din titlu, ca dicționar cu cheile din SPEC_COLUMNS."""
    t = _normalize(title)
    brand = _brand(t)
    cpu_brand, cpu_model, cpu_tier, cpu_suffix = _cpu(t, brand)
    gpu_brand, gpu_model, gpu_dedicated = _gpu(t, cpu_brand)
    ram_gb, storage_gb, vram_gb = _sizes(t)
    resolution, megapixels = _resolution(t)
    ram_type = re.search(r"\b(lpddr\d+x?|ddr\d)\b", t)
    refresh = re.search(r"\b(\d{2,3})\s*hz\b", t)
    is_gaming = bool(GAMING_RE.search(t))
    if (gpu_dedicated is None and cpu_brand in ("Intel", "AMD", "Qualcomm")
            and not is_gaming and not WORKSTATION_RE.search(t)):
        # placa dedicată apare în titlu; fără ea (la laptopuri obișnuite) placa e cea integrată în procesor
        gpu_brand, gpu_dedicated = cpu_brand, False
    # Arc 130V/140V există doar la procesoarele cu sufix V (Lunar Lake), Arc 130T/140T doar la cele H;
    # altfel titlul greșește placa ('Ultra 7 265U ... Arc 140V'), iar cea reală e 'Intel Graphics'
    arc = re.fullmatch(r"Arc 1[34]0([TV])", gpu_model or "")
    if arc and cpu_suffix and cpu_suffix != {"V": "V", "T": "H"}[arc.group(1)]:
        gpu_model = "Intel Graphics"
    return {
        "brand": brand, "cpu_brand": cpu_brand, "cpu_model": cpu_model, "cpu_tier": cpu_tier,
        "cpu_suffix": cpu_suffix, "cpu_cores": _cores(t),
        "ram_gb": ram_gb, "ram_type": ram_type.group(1).upper() if ram_type else None, "storage_gb": storage_gb,
        "gpu_brand": gpu_brand, "gpu_model": gpu_model, "gpu_dedicated": gpu_dedicated,
        "gpu_vram_gb": vram_gb if gpu_dedicated else (0 if gpu_dedicated is False else None),  # integrată: 0
        "screen_in": _screen(t), "resolution": resolution, "megapixels": megapixels,
        "refresh_hz": int(refresh.group(1)) if refresh and 60 <= int(refresh.group(1)) <= 500 else None,
        "panel": _panel(t), "touch": bool(TOUCH_RE.search(t)),
        "os": _os(t, brand), "is_gaming": is_gaming, "is_refurbished": bool(REFURB_RE.search(t)),
    }


if __name__ == "__main__":
    print(json.dumps(parse_specs(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
