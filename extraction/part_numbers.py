"""
Part number-ul fiecărui laptop (codul producătorului, ex. 21Y2004BRI): identifică același laptop
în toate zilele și în toate magazinele.

Paginile de listă, din care crawl.py ia zilnic prețurile, nu conțin part number-ul, așa că fiecare
produs e deschis o singură dată, în pagina lui:
    CEL     datele structurate ale paginii (JSON-LD 'mpn')     ex. 21WN00DWRI, AD3Y8ET
    evoMAG  codul produsului ('productSKU')                     ex. 21Y2004BRI
    eMAG    JSON-LD 'mpn'. Vânzătorii din marketplace pun des acolo coduri proprii ('213440GB1TB'),
            mai ales la configurațiile modificate (RAM / SSD mărite), care nu au un part number real;
            de la ei se păstrează doar codurile cu forma part number-ului producătorului (PN_FORMS)
La Dell, magazinele folosesc codul configurației (ex. PB14250FU7265U32GB1TBW3Y-05, BTO119_MC16250_EMEA_UBU).
Part number-ul e păstrat doar cu litere mari și cifre (normalize), fiindcă CEL îl scrie fără semne
(MDVC4ROA, NHU64EX00B, BTO108PC16250EMEA), iar eMAG și evoMAG cu semne (MDVC4RO/A, NH.U64EX.00B,
BTO108_PC16250_EMEA): altfel același laptop n-ar fi recunoscut în magazine diferite.

Rezultatul e data/raw/part_numbers.csv, publicat ca restul datelor, un rând pe (magazin, produs):
    shop, product_id, part_number (gol dacă nu există), code (codul afișat de magazin, înainte de
    verificare, cu semnele lui), seller, checked_at
Codul magazinului (product_id) rămâne cheia colectării zilnice, fiindcă doar el apare în lista de produse;
tabelul acesta îi asociază part number-ul. Fiecare produs e verificat o singură dată. crawl.py verifică
la final produsele zilei încă neverificate, cel mult --max-part-numbers pagini.
Rulat separat (python extraction/part_numbers.py) face același lucru pentru toate produsele colectate.
"""
import argparse
import html as htmllib
import json
import os
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from specs import parse_specs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FILE_NAME = "part_numbers.csv"
COLUMNS = ["shop", "product_id", "part_number", "code", "seller", "checked_at"]
DEFAULT_MAX = 300  # pagini de produs pe rulare (~40 de minute); acoperă produsele noi ale zilei (~190)
TZ = ZoneInfo("Europe/Bucharest")
SHOP_ORDER = {"cel": 0, "evomag": 1, "emag": 2}
# pauza minimă între două cereri la același magazin: evoMAG a cerut captcha după ~60 de pagini de produs
# la ~5 s una de alta, dar nu și la ~15 s (când cererile alternau cu celelalte magazine); eMAG a blocat
# adresa IP („trafic neobișnuit de mare”) după zile cu sute de pagini de produs la 3-6 s
HOST_GAP = {"evomag": 15.0, "emag": 8.0}
# după atâtea erori la rând (ex. eMAG în mentenanță, HTTP 503), magazinul e lăsat pentru rularea următoare:
# altfel fiecare produs ar aștepta ~3 minute de reîncercări
MAX_ERRORS = 3
# Unii vânzători din marketplace-ul eMAG pun în pagină coduri proprii, nu part number-ul producătorului
# (configurații cu RAM / SSD mărite, coduri interne): paginile produselor lor nu se deschid, oricare ar fi prețul,
# fiindcă ar fi cereri aproape fără rezultat. Un vânzător e recunoscut după cel puțin SKIP_MIN_PAGES pagini
# verificate, cu part number la mai puțin de SKIP_MAX_SHARE dintre ele. Pe 5.11.2026: ANABLUE COMPUTERS 52 din
# 1.567 ('213440GB1TB'), LAPTOK.COM 0 din 101 ('18681296151'), MM CORP SPÓŁKA 0 din 98 ('3176-32GB-4TB'),
# TEKADVICE 0 din 54, DMAG 0 din 38, Home Center Distribution 0 din 30 ('PKY435147DPMO'), Metrostore 0 din 14
SKIP_MIN_PAGES = 10
SKIP_MAX_SHARE = 0.05
# Lista eMAG arată vânzătorul doar din 31.10.2026. La produsele fără vânzător cunoscut (văzute ultima dată
# înainte), prețul întreg (fără bani) ține locul lui: atunci era aproape mereu ANABLUE COMPUTERS (din 1.627 de
# pagini verificate până pe 23.10, doar 5% aveau part number, față de 65% la prețurile cu bani)
WHOLE_PRICE_SKIP = {"emag"}
SHOP_SELLERS = {"emag", "evomag", "cel.ro"}  # vândute de magazin: codul vine din catalogul producătorului
NO_SELLER_SHOPS = {"cel", "evomag"}  # magazine fără marketplace în care pagina nu arată mereu vânzătorul
# forma part number-ului la producătorii principali (pentru codurile puse de vânzătorii din marketplace)
PN_FORMS = {
    "lenovo": r"[28]\d[A-Z0-9]{6}[A-Z]{2}",                                                 # 83F50047BM, 21Y2004BRI
    "hp": r"(?=.*\d)[0-9A-Z]{4,6}(?:EA|ET|UA|UT)R?(?:#[A-Z0-9]{3})?",                        # CX5P8EA, AD3Y8ET
    "asus": r"[A-Z]{1,3}\d{3,4}[A-Z0-9]{0,4}-[A-Z0-9]{3,8}|90NB[0-9A-Z]{4}-M[0-9A-Z]{5,6}",  # FA401EA-RG005W
    "acer": r"N[XH]\.[A-Z0-9]{5}\.[A-Z0-9]{3}",                                               # NH.QYVEX.009
    "apple": r"[MZ][A-Z0-9]{3,4}\d?[A-Z]{1,2}/A|Z[0-9A-Z]{7,8}",                             # MW2U3ZE/A, Z1L2000D3
    "msi": r"9S7-[0-9A-Z]{6}-[0-9A-Z]{3}|[A-Z]\d{2}[A-Z]{2,5}-\d{3,4}[A-Z]{2,5}",            # B13VFK-1426XRO
}


# ---------------------------------------------------------------- extragerea din pagina produsului

def _product_nodes(page):
    """Obiectele schema.org Product din blocurile JSON-LD ale paginii (cele de pe primul nivel întâi)."""
    nodes = []
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S | re.I):
        try:  # strict=False: unele descrieri de la eMAG conțin caractere de control (taburi, rânduri noi)
            queue = [json.loads(block.strip(), strict=False)]
        except ValueError:
            continue
        while queue:
            node = queue.pop(0)
            if isinstance(node, list):
                queue += node
            elif isinstance(node, dict):
                kind = node.get("@type")
                if "Product" in (kind if isinstance(kind, list) else [kind]):
                    nodes.append(node)
                queue += [v for v in node.values() if isinstance(v, (dict, list))]
    return nodes


def _name(value):
    """'Lenovo' sau {'@type': 'Brand', 'name': 'Lenovo'} -> 'Lenovo'"""
    if isinstance(value, list):
        value = value[0] if value else None
    return (value.get("name") if isinstance(value, dict) else value) or None


def _page_fields(shop, page):
    """(codul afișat de magazin, vânzătorul, marca), încă neverificate."""
    code = seller = brand = None
    for node in _product_nodes(page):
        code = code or node.get("mpn")
        brand = brand or _name(node.get("brand"))
        offers = node.get("offers") or []
        for offer in offers if isinstance(offers, list) else [offers]:
            if isinstance(offer, dict):
                seller = seller or _name(offer.get("seller"))
    if not code and shop == "evomag":
        m = re.search(r'"productSKU"\s*:\s*"([^"]+)"', page)
        code = m.group(1).replace("\\/", "/") if m else None
    if not seller:  # CEL: 'Vandut de: CEL.RO'
        m = re.search(r"(?i)v(?:a|â|&acirc;)ndut de\s*:?\s*(?:<[^>]+>\s*)*([^<]{2,60}?)\s*<", page)
        seller = htmllib.unescape(m.group(1)).strip() if m else None
    return code, seller, brand


def validate(shop, code, seller, brand="", title=""):
    """Part number-ul (sau None) dintr-un cod afișat de magazin. Codurile din catalogul magazinului sunt
    păstrate; cele puse de vânzătorii din marketplace doar dacă au forma part number-ului producătorului."""
    code = re.sub(r"\s+", "", str(code)).upper() if isinstance(code, str) and code.strip() else None
    if not code or not re.fullmatch(r"(?=.*\d)[A-Z0-9][A-Z0-9./#_-]{4,40}", code):
        return None  # lipsă sau alt fel de text ('WEWWQAZSCDF')
    # eMAG fără vânzător în pagină: nu știm dacă e marketplace, deci se verifică forma codului
    trusted = seller.lower() in SHOP_SELLERS if isinstance(seller, str) else shop in NO_SELLER_SHOPS
    if trusted:
        return code
    brands = [brand, parse_specs(title if isinstance(title, str) else "")["brand"]]
    forms = [PN_FORMS[b.lower()] for b in brands if isinstance(b, str) and b.lower() in PN_FORMS]
    return code if forms and re.fullmatch(forms[0], code) else None


def normalize(part_number):
    """'MDVC4RO/A' -> 'MDVC4ROA', 'NH.U64EX.00B' -> 'NHU64EX00B': forma comună tuturor magazinelor."""
    return re.sub(r"[^A-Z0-9]", "", part_number.upper()) if isinstance(part_number, str) else None


def extract(shop, page, title=""):
    """{'part_number', 'code', 'seller'} din HTML-ul paginii unui produs (None unde lipsesc)."""
    code, seller, brand = _page_fields(shop, page or "")
    code = str(code).strip() if code else None
    return {"part_number": normalize(validate(shop, code, seller, brand, title)), "code": code, "seller": seller}


# ---------------------------------------------------------------- tabelul part_numbers.csv

def load(path):
    """Tabelul salvat (gol dacă nu există încă); product_id rămâne text."""
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS, dtype=object)
    table = pd.read_csv(path, dtype=str)
    for col in COLUMNS:  # fișierele scrise înainte de adăugarea unei coloane (ex. code)
        if col not in table:
            table[col] = pd.Series(None, index=table.index, dtype=object)
    table["part_number"] = _keys(table)
    return table[COLUMNS]


def _keys(table):
    """part_number fără semne (și la rândurile scrise înainte de normalize). Excepție: coduri care în același
    magazin diferă doar prin semne sunt produse diferite (evoMAG: DLMC1625168054779/P și DLMC1625168054779.P,
    două configurații Dell), așa că acolo rămâne forma afișată, din code."""
    key = table["part_number"].map(normalize)
    shown = table["code"].astype("string").str.replace(r"\s+", "", regex=True).str.upper()
    shown = shown.fillna(table["part_number"].astype("string")).where(key.notna())
    clash = shown.groupby([table["shop"], key]).transform("nunique").gt(1)
    return key.mask(clash, shown)


def _save(new, path):
    """Adaugă rezultatele noi în tabel. Fișierul e recitit chiar înainte de scriere, ca două rulări simultane
    (ex. completarea de noapte și crawlerul zilnic) să nu-și șteargă una alteia rezultatele, și e scris
    printr-un fișier temporar, ca o oprire bruscă să nu-l strice."""
    frames = [f for f in (load(path), pd.DataFrame(new, columns=COLUMNS)) if not f.empty]
    out = pd.concat(frames, ignore_index=True).drop_duplicates(["shop", "product_id"], keep="last")
    out["part_number"] = _keys(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    out.sort_values(["shop", "product_id"]).to_csv(tmp, index=False, encoding="utf-8-sig")
    try:
        tmp.replace(path)
    except PermissionError:
        print(f"  nu pot scrie {path}: e deschis în alt program (ex. Excel). Închide-l; reîncerc la următoarea salvare")


def low_yield_sellers(table):
    """Vânzătorii (cu litere mici) ale căror pagini verificate au rareori part number: pun coduri proprii."""
    seller = table["seller"].astype("string").str.strip().str.lower()
    stats = table.assign(_seller=seller).dropna(subset=["_seller"]).groupby("_seller")["part_number"].agg(
        ["size", "count"])
    low = (stats["size"] >= SKIP_MIN_PAGES) & (stats["count"] < SKIP_MAX_SHARE * stats["size"])
    return set(stats.index[low]) - SHOP_SELLERS


def skipped(targets, table):
    """True la produsele a căror pagină nu se deschide intenționat: oferte eMAG ale vânzătorilor cu coduri proprii
    (low_yield_sellers) sau, fără vânzător cunoscut (văzute ultima dată înainte de 31.10.2026), cu preț întreg."""
    seller = (targets["seller"] if "seller" in targets else pd.Series(None, index=targets.index, dtype=object))
    seller = seller.astype("string").str.strip().str.lower()
    seller = seller.mask(seller.eq("").fillna(False))
    by_seller = seller.isin(low_yield_sellers(table)).fillna(False).astype(bool)
    price = targets["price"] if "price" in targets else pd.Series(None, index=targets.index, dtype=float)
    cents = (pd.to_numeric(price, errors="coerce") * 100).round()
    whole = seller.isna().to_numpy() & (cents % 100 == 0).to_numpy()
    return targets["shop"].isin(WHOLE_PRICE_SKIP) & (by_seller | whole)


def targets_from_raw(raw_dir=RAW):
    """Produsele din CSV-urile zilnice, cu cel mai recent URL, titlu, preț și vânzător (cele văzute recent întâi).
    Vânzătorul lipsește din CSV-urile de dinainte de 31.10.2026."""
    files = sorted(raw_dir.glob("*/*.csv.gz"))
    cols = ["date", "shop", "product_id", "url", "title", "price", "seller"]
    if not files:
        return pd.DataFrame(columns=cols[1:])
    df = pd.concat([pd.read_csv(f, dtype={"product_id": str}, usecols=lambda c: c in cols) for f in files],
                   ignore_index=True).reindex(columns=cols)
    return df.sort_values("date", ascending=False).drop_duplicates(["shop", "product_id"])[cols[1:]]


def update(targets, max_pages=DEFAULT_MAX, delay=(3.0, 6.0), path=RAW / FILE_NAME, fetcher=None, robots=None):
    """Deschide paginile produselor încă neverificate (cel mult max_pages) și adaugă rezultatul în path.
    targets: DataFrame cu shop, product_id, url, title și, opțional, price și seller. Întoarce numărul de pagini
    deschise."""
    from crawl import Blocked, HttpFetcher, Robots, looks_blocked, redirected_to_block  # aici: crawl.py importă modulul

    table = load(path)
    done = set(zip(table["shop"], table["product_id"]))
    todo = targets.drop_duplicates(["shop", "product_id"])
    todo = todo[[(s, str(p)) not in done for s, p in zip(todo["shop"], todo["product_id"])]]
    skip = skipped(todo, table)
    if skip.any():
        who = (todo.loc[skip, "seller"] if "seller" in todo
               else pd.Series(None, index=todo.index[skip.to_numpy()], dtype=object))
        who = who.astype("string").fillna("preț întreg, fără vânzător în listă").value_counts()
        print(f"part number: sar peste {skip.sum()} produse eMAG ale vânzătorilor care pun coduri proprii, nu "
              f"part number-ul producătorului ({', '.join(f'{k} {v}' for k, v in who.head(4).items())}"
              f"{', ...' if len(who) > 4 else ''})")
    todo = todo[~skip]
    # cererile alternează între magazine (CEL, evoMAG, eMAG, CEL, ...): fiecare site primește o cerere
    # la ~15 secunde, nu sute la rând, ca să nu declanșăm verificările anti-bot
    todo = todo.assign(_n=todo.groupby("shop").cumcount(), _s=todo["shop"].map(SHOP_ORDER).fillna(len(SHOP_ORDER)))
    todo = todo.sort_values(["_n", "_s"], kind="stable").drop(columns=["_n", "_s"])
    today = datetime.now(TZ).date().isoformat()
    rest = len(todo)
    if todo.empty or max_pages <= 0:
        print("part number: toate produsele sunt verificate" if not rest else
              f"part number: {rest} produse neverificate (nu deschid nicio pagină)")
        return 0
    n = min(len(todo), max_pages)
    minutes = n * (sum(delay) / 2 + 1) / 60
    print(f"part number: {rest} produse neverificate; deschid {n} pagini de produs "
          f"(~{f'{minutes / 60:.1f} ore' if minutes > 90 else f'{max(1, round(minutes))} min'})")
    own = fetcher is None
    fetcher, robots = fetcher or HttpFetcher(), robots or Robots()
    new, stopped, opened, found = [], set(), 0, 0
    last_hit = {}  # magazin -> momentul ultimei cereri (time.monotonic)
    errors = {}  # magazin -> erori la rând
    try:
        for r in todo.itertuples(index=False):
            if opened >= max_pages:
                break
            if r.shop in stopped:
                continue
            row = {"shop": r.shop, "product_id": str(r.product_id), "part_number": None, "code": None,
                   "seller": None, "checked_at": today}
            if not robots.allowed(r.url):  # nepermis: notat ca verificat, ca să nu fie reîncercat zilnic
                new.append(row)
                continue
            if opened:
                gap = HOST_GAP.get(r.shop, 0) - (time.monotonic() - last_hit.get(r.shop, float("-inf")))
                time.sleep(max(random.uniform(*delay), gap, robots.crawl_delay(r.url) or 0))
            opened += 1
            last_hit[r.shop] = time.monotonic()
            try:
                page, status, final_url = fetcher.get(r.url)
            except Blocked as e:
                print(f"  [{r.shop}] blocat ({e}): opresc pentru azi verificarea la {r.shop}")
                stopped.add(r.shop)
                continue
            except Exception as e:  # noqa: BLE001 - rețea: produsul rămâne neverificat, se reia data viitoare
                print(f"  [{r.shop}] eroare la {r.url}: {e}")
                errors[r.shop] = errors.get(r.shop, 0) + 1
                if errors[r.shop] >= MAX_ERRORS:
                    print(f"  [{r.shop}] {MAX_ERRORS} erori la rând (site indisponibil?): opresc pentru azi "
                          f"verificarea la {r.shop}")
                    stopped.add(r.shop)
                continue
            errors[r.shop] = 0
            if page and (redirected_to_block(r.url, final_url, page) or looks_blocked(page)):
                # nu e notat ca verificat: produsul se reia la rularea următoare
                print(f"  [{r.shop}] pagină de verificare anti-bot ({final_url[:70]}): "
                      f"opresc pentru azi verificarea la {r.shop}")
                stopped.add(r.shop)
                continue
            if page:  # fără pagină (404): produsul a fost retras, rămâne fără part number
                row.update(extract(r.shop, page, r.title))
            new.append(row)
            found += row["part_number"] is not None
            if len(new) % 25 == 0:
                _save(new, path)
                print(f"  {opened} pagini deschise, {found} part number-uri găsite")
    except KeyboardInterrupt:
        print("  oprit manual (Ctrl+C); salvez ce s-a găsit până acum")
    finally:
        if new:
            _save(new, path)
        if own:
            fetcher.close()
    left = rest - len(new)
    print(f"part number: {len(new)} produse verificate, {found} cu part number"
          + (f"; rămân {left} neverificate (continuă la rularea următoare)" if left else ""))
    return opened


def main():
    for stream in (sys.stdout, sys.stderr):  # ca în crawl.py: ă/ș/ț și cu ieșirea redirecționată
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="Part number-ul produselor colectate, din paginile lor")
    ap.add_argument("--max", type=int, default=DEFAULT_MAX,
                    help=f"câte pagini de produs deschide (implicit {DEFAULT_MAX})")
    ap.add_argument("--shops", nargs="+", choices=list(SHOP_ORDER), default=list(SHOP_ORDER))
    ap.add_argument("--delay", type=float, nargs=2, default=(3.0, 6.0), metavar=("MIN", "MAX"),
                    help="pauza dintre pagini, în secunde (implicit 3-6)")
    ap.add_argument("--raw", type=Path, default=RAW, help="folderul cu CSV-urile zilnice (implicit data/raw)")
    ap.add_argument("--out", type=Path, help=f"tabelul rezultat (implicit <raw>/{FILE_NAME})")
    args = ap.parse_args()

    targets = targets_from_raw(args.raw)
    if targets.empty:
        sys.exit(f"Nu există date în {args.raw}. Rulează întâi crawlerul: python extraction/crawl.py")
    update(targets[targets["shop"].isin(args.shops)], args.max, tuple(args.delay), args.out or args.raw / FILE_NAME)


if __name__ == "__main__":
    main()
