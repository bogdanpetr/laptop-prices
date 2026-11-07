"""
Construiește setul de date final din CSV-urile zilnice ale crawlerului:

    data/raw/<AAAA-LL-ZZ>/<magazin>.csv.gz  ->  data/processed/products.csv

Un rând pe (magazin, produs), cu:
- part number-ul (codul producătorului, din data/raw/part_numbers.csv): același laptop în mai multe magazine
  are același part_number
- specificațiile extrase din titlu (extraction/specs.py): brand, procesor, RAM, stocare, placă video, ecran...
- scorurile de performanță ale procesorului și plăcii video (extraction/benchmarks.py, Notebookcheck)
- rezumatul prețurilor observate: ultimul preț, minim, maxim, median, prima și ultima zi, numărul de zile
- in_price_range: prețul median e în intervalul studiului (10.000-20.000 lei). Crawlerul colectează din 6.11.2026
  5.000-25.000 lei, ca laptopurile din interval să fie urmărite și când sunt reduse sub 10.000 sau se scumpesc peste
  20.000; produsele cu prețul median în afara intervalului nu fac parte din studiu (in_price_range = False)
- bf_price: la eMAG, prețul de Black Friday (6.11.2026) epuizat înainte de rularea crawlerului, din bannerul paginii
  produsului (data/raw/black_friday.csv, extraction/black_friday.py); gol în rest

Exemplu:
    python extraction/benchmarks.py      # o singură dată: listele de benchmark în data/external/
    python extraction/build_dataset.py
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmarks import BENCH_COLUMNS, add_benchmarks, load_tables  # noqa: E402
from black_friday import FILE_NAME as BF_FILE, load as load_bf_prices  # noqa: E402
from part_numbers import FILE_NAME as PN_FILE, load as load_part_numbers, skipped  # noqa: E402
from specs import SPEC_COLUMNS, parse_specs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed" / "products.csv"
PRICE_SUMMARY = ["price_last", "price_min", "price_max", "price_median", "in_price_range", "rrp_last", "rating",
                 "n_reviews", "first_seen", "last_seen", "n_days"]
STUDY_RANGE = (10_000, 20_000)  # intervalul studiului, după prețul median (crawl.py colectează 5.000-25.000 lei)
INT_COLUMNS = ["cpu_tier", "cpu_cores", "ram_gb", "storage_gb", "gpu_vram_gb", "refresh_hz", "n_reviews"]
FLAGS = ["touch", "is_gaming", "is_refurbished"]  # mereu True/False


def load_prices(raw_dir):
    """Toate rândurile din CSV-urile zilnice (product_id rămâne text: la evoMAG e numeric)."""
    files = sorted(raw_dir.glob("*/*.csv.gz"))
    frames = [pd.read_csv(f, dtype={"product_id": str}) for f in files]
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), len(files)


def build_products(prices):
    """Un rând pe (magazin, produs): ultimul titlu și vânzător, specificațiile și rezumatul prețurilor."""
    prices = prices.dropna(subset=["price"]).sort_values("date")
    if "seller" not in prices:  # CSV-urile de dinainte de coloana seller (31.10.2026)
        prices["seller"] = None
    products = (prices.groupby(["shop", "product_id"])
                .agg(title=("title", "last"), url=("url", "last"), seller=("seller", "last"),
                     price_last=("price", "last"), price_min=("price", "min"), price_max=("price", "max"),
                     price_median=("price", "median"), rrp_last=("rrp", "last"),
                     rating=("rating", "last"), n_reviews=("n_reviews", "last"),
                     first_seen=("date", "min"), last_seen=("date", "max"), n_days=("date", "nunique"))
                .reset_index())
    products["in_price_range"] = products["price_median"].between(*STUDY_RANGE)
    specs = pd.DataFrame([parse_specs(t) for t in products["title"]], columns=SPEC_COLUMNS)
    products = pd.concat([products, specs], axis=1)
    for col in INT_COLUMNS:
        products[col] = products[col].astype("Int64")
    return products[["shop", "product_id", "seller", "title", *SPEC_COLUMNS, *PRICE_SUMMARY, "url"]]


def incomplete_days(raw_dir):
    """(zi, magazin, produse) pentru rulările salvate care nu au ajuns la sfârșitul listei."""
    out = []
    for log_path in sorted(raw_dir.glob("*/*_log.json")):
        log = json.loads(log_path.read_text(encoding="utf-8"))
        if log.get("complete") is False and (log_path.parent / f"{log['shop']}.csv.gz").exists():
            out.append((log_path.parent.name, log["shop"], log.get("products")))
    return out


def add_part_numbers(products, path):
    """Coloana part_number (goală la produsele fără part number sau încă neverificate) și vânzătorul: cel din
    ultima listă în care a apărut produsul (eMAG, din 31.10.2026) sau, dacă lipsește, cel din pagina produsului,
    citit o dată cu part number-ul. Întoarce și produsele a căror pagină n-a fost deschisă (True/False pe rând)."""
    table = load_part_numbers(path)[["shop", "product_id", "part_number", "seller"]]
    out = products.merge(table, on=["shop", "product_id"], how="left", indicator=True, suffixes=("", "_page"))
    out["seller"] = out["seller"].fillna(out.pop("seller_page"))
    return out, out.pop("_merge") == "left_only"


def add_bf_prices(products, path):
    """Coloana bf_price: prețul de Black Friday epuizat, din pagina produsului eMAG (black_friday.py); gol în rest."""
    table = load_bf_prices(path)
    table = table.assign(bf_price=pd.to_numeric(table["bf_price"], errors="coerce")).dropna(subset=["bf_price"])
    return products.merge(table[["shop", "product_id", "bf_price"]], on=["shop", "product_id"], how="left")


def unchecked_message(products, unchecked, path):
    """Câte produse nu au pagina verificată: sărite intenționat (part_numbers.skipped) și în așteptare."""
    rest = products[unchecked].rename(columns={"price_last": "price"})
    skip = skipped(rest, load_part_numbers(path))
    waiting = len(rest) - int(skip.sum())
    text = f"   {len(rest)} produse fără pagina verificată:"
    if skip.any():
        text += (f" {skip.sum()} sărite intenționat (oferte eMAG ale vânzătorilor care pun coduri proprii, nu part "
                 "number-ul producătorului, ex. ANABLUE COMPUTERS; rămân fără part number)")
    if waiting:
        text += (f"{';' if skip.any() else ''} {waiting} în așteptare (se verifică la rulările zilnice cât timp apar "
                 "în listă, iar cele scoase din listă cu  python extraction/part_numbers.py)")
    return text


def main():
    for stream in (sys.stdout, sys.stderr):  # ca în crawl.py: ă/ș/ț și cu ieșirea redirecționată
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Setul de date cu specificații, din CSV-urile crawlerului")
    ap.add_argument("--raw", type=Path, default=RAW, help="folderul cu CSV-urile zilnice (implicit data/raw)")
    ap.add_argument("--out", type=Path, default=OUT, help="fișierul rezultat (implicit data/processed/products.csv)")
    args = ap.parse_args()

    prices, n_files = load_prices(args.raw)
    if prices.empty:
        sys.exit(f"Nu există date în {args.raw}. Rulează întâi crawlerul: python extraction/crawl.py")
    products = build_products(prices)
    cpu, gpu = load_tables()
    products = add_benchmarks(products, cpu, gpu)
    products, unchecked = add_part_numbers(products, args.raw / PN_FILE)
    products = add_bf_prices(products, args.raw / BF_FILE)
    products = products[["shop", "product_id", "part_number", "seller", "title", *SPEC_COLUMNS, *BENCH_COLUMNS,
                         *PRICE_SUMMARY, "bf_price", "url"]]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    try:  # UTF-8 cu BOM: altfel Excel citește fișierul ca ANSI și arată 'XeÂ²' în loc de 'Xe²', 'Ã¢' în loc de 'â'
        products.to_csv(args.out, index=False, encoding="utf-8-sig")
    except PermissionError:
        sys.exit(f"Nu pot scrie {args.out}: fișierul e deschis în alt program (ex. Excel). Închide-l și rulează din nou.")

    print(f"{n_files} fișiere, {len(prices)} prețuri zilnice -> {len(products)} produse în {args.out}")
    print(products.groupby("shop").size().to_string())
    inside = products["in_price_range"]
    below = products["price_median"] < STUDY_RANGE[0]
    print(f"În intervalul studiului ({STUDY_RANGE[0]:,}-{STUDY_RANGE[1]:,} lei, după prețul median): {inside.sum()} "
          f"produse; în afara lui (in_price_range = False): {below.sum()} sub {STUDY_RANGE[0]:,} lei, "
          f"{(~inside & ~below).sum()} peste {STUDY_RANGE[1]:,} lei")
    has_pn = products["part_number"].notna()
    by_shop = ", ".join(f"{s} {v:.0%}" for s, v in has_pn.groupby(products["shop"]).mean().items())
    in_shops = products[has_pn].groupby("part_number")["shop"].nunique()
    print(f"\nPart number: {has_pn.mean():.0%} din produse ({by_shop}); "
          f"{(in_shops >= 2).sum()} part number-uri apar în cel puțin două magazine")
    if unchecked.any():
        print(unchecked_message(products, unchecked, args.raw / PN_FILE))
    bf = products["bf_price"].notna()
    if bf.any():
        print(f"Black Friday: {bf.sum()} produse eMAG cu preț de Black Friday epuizat (bf_price), "
              f"în medie cu {(1 - products.loc[bf, 'bf_price'] / products.loc[bf, 'price_max']).mean():.0%} sub "
              "prețul maxim observat")
    incomplete = incomplete_days(args.raw)
    if incomplete:
        print("\nAtenție, zile incomplete (rulare oprită de o blocare sau eroare): "
              + ", ".join(f"{day} {shop} ({n} produse)" for day, shop, n in incomplete)
              + ".\n   Produsele lipsă în acele zile au last_seen / n_days mai mici; în analiza în timp tratează"
                " aceste zile ca parțiale (crawl_runs / *_log.json, câmpul complete).")
    print("\nProduse cu fiecare specificație completată (%):")
    found = products[[c for c in SPEC_COLUMNS if c not in FLAGS]].notna().mean() * 100
    print(found.round().astype(int).to_string())
    if cpu is None:
        print("\nFără scoruri de performanță: rulează o dată  python extraction/benchmarks.py")
    else:
        dedicated = products["gpu_dedicated"] == True  # noqa: E712
        print(f"\nScoruri Notebookcheck: procesor {products['nbc_cpu'].notna().mean():.0%} din produse, "
              f"placă video {products['nbc_gpu'].notna().mean():.0%} "
              f"({products.loc[dedicated, 'nbc_gpu'].notna().mean():.0%} din cele cu placă dedicată)")


if __name__ == "__main__":
    main()
