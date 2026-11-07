"""
Prețurile de Black Friday epuizate la eMAG (6.11.2026), citite din pagina produsului.

De Black Friday, eMAG a vândut unele laptopuri la un preț special, în stoc limitat. Multe s-au epuizat în primele
ore, iar lista de produse arată apoi oferta altui vânzător sau prețul obișnuit, așa că o rulare a crawlerului făcută
mai târziu nu vede prețul de Black Friday. Pagina produsului îl păstrează însă, într-un banner:
    „Ne pare rau. Ai ajuns putin prea tarziu... Acest produs a fost in oferta noastra de Black Friday la un pret
    special 14.999,99 Lei stoc epuizat la acest pret”
și în datele paginii: banners: [{"type":"bf_out_of_stock", ..., "price":14999.99}]

Scriptul deschide o singură dată pagina fiecărui produs vândut de eMAG în listă, cele mai probabile întâi (vândute de
eMAG înainte de Black Friday și apoi trecute la alt vânzător, scoase din listă sau epuizate), și scrie
data/raw/black_friday.csv, un rând pe produs:
    shop, product_id, bf_price (prețul de Black Friday epuizat; gol dacă pagina nu are bannerul),
    page_price, page_seller, page_stock (oferta din pagină la verificare), checked_at
Paginile intră în aceeași limită zilnică de pagini eMAG ca part number-ul (part_numbers.SHOP_MAX). crawl.py îl rulează
la final, înaintea part number-ului, până pe UNTIL; part number-ul nu se schimbă, deci poate aștepta câteva zile.

    python extraction/black_friday.py              # următoarele produse, în limita zilei
    python extraction/black_friday.py --max 20 --priorities 0
"""
import argparse
import html as htmllib
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import part_numbers  # noqa: E402
from parsing import parse_price  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FILE_NAME = part_numbers.BF_FILE_NAME
COLUMNS = ["shop", "product_id", "bf_price", "page_price", "page_seller", "page_stock", "checked_at"]
TZ = ZoneInfo("Europe/Bucharest")
SINCE = "2026-10-31"      # de când lista eMAG arată vânzătorul ofertei
BEFORE_BF = "2026-11-05"  # ultima zi colectată înainte de Black Friday (6.11.2026)
# până atunci crawl.py verifică zilnic paginile rămase. Bannerul a dispărut pe 7.11.2026, la mentenanța eMAG de la ~03:00
# (rularea de noapte, până la 03:08, l-a mai găsit; dimineața nu mai era), deci verificarea zilnică s-a oprit atunci
UNTIL = "2026-11-07"
# grupurile verificate implicit (candidates): toate produsele vândute de eMAG, cele mai probabile întâi. Pe 6.11.2026
# bannerul a apărut la 44 din 67 de produse cu prioritatea 0 și la 0 din 10 cu prioritatea 2. Ofertele vânzătorilor
# din marketplace nu au banner, deci nu se verifică
PRIORITIES = (0, 1, 2, 3)

_BANNERS = re.compile(r"\bbanners\s*:\s*(\[)")
_HTML_BANNER = re.compile(r"banner-bf-outofstock.*?banner-price[^>]*>(.*?)</span>\s*</span>", re.S)


def extract(page):
    """{'bf_price', 'page_price', 'page_seller', 'page_stock'} din HTML-ul paginii unui produs eMAG (None unde lipsesc)."""
    page = page or ""
    bf = None
    m = _BANNERS.search(page)
    if m:
        try:
            banners, _ = json.JSONDecoder(strict=False).raw_decode(page, m.start(1))
            bf = next((float(b["price"]) for b in banners if isinstance(b, dict)
                       and b.get("type") == "bf_out_of_stock" and b.get("price")), None)
        except (ValueError, TypeError):
            bf = None
    if bf is None:  # aceeași valoare, din bannerul afișat
        h = _HTML_BANNER.search(page)
        bf = parse_price(re.sub(r"<[^>]+>", " ", htmllib.unescape(h.group(1)))) if h else None
    price = seller = stock = None
    for node in part_numbers._product_nodes(page):
        offers = node.get("offers") or []
        for offer in offers if isinstance(offers, list) else [offers]:
            if isinstance(offer, dict):
                price = price or offer.get("price")
                stock = stock or str(offer.get("availability") or "").rsplit("/", 1)[-1] or None
                seller = seller or part_numbers._name(offer.get("seller"))
    try:
        price = float(price) if price else None
    except (TypeError, ValueError):
        price = None
    return {"bf_price": bf, "page_price": price, "page_seller": seller, "page_stock": stock}


def load(path):
    """Tabelul salvat (gol dacă nu există încă); product_id rămâne text."""
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path, dtype={"product_id": str, "shop": str, "checked_at": str})


def _save(new, path):
    """Adaugă rezultatele noi (fișierul e recitit chiar înainte și scris printr-un fișier temporar, ca în
    part_numbers._save)."""
    frames = [f for f in (load(path), pd.DataFrame(new, columns=COLUMNS)) if not f.empty]
    out = pd.concat(frames, ignore_index=True).drop_duplicates(["shop", "product_id"], keep="last")
    tmp = path.with_name(f"{path.name}.tmp")
    out.sort_values(["shop", "product_id"]).to_csv(tmp, index=False, encoding="utf-8-sig")
    try:
        tmp.replace(path)
    except PermissionError:
        print(f"  nu pot scrie {path}: e deschis în alt program (ex. Excel). Închide-l; reîncerc la următoarea salvare")


def candidates(raw_dir=RAW):
    """Produsele eMAG vândute de eMAG în listă din SINCE, cu ultimul URL, în ordinea verificării. Prioritate:
    0  vândute de eMAG înainte de Black Friday (BEFORE_BF), apoi scoase din listă, trecute la alt vânzător sau epuizate
    1  vândute de eMAG doar în alte zile (ex. produsele noi în date din 6.11.2026, sub 10.000 sau peste 20.000 lei)
    2  vândute de eMAG înainte de Black Friday, fără reducere în listă (același preț sau mai mare)
    3  vândute de eMAG înainte de Black Friday și mai ieftine în listă (reducerea se vede deja în date)
    La prioritate egală, cele văzute mai recent întâi."""
    files = [f for f in sorted(raw_dir.glob("*/emag.csv.gz")) if f.parent.name >= SINCE]
    if not files:
        return pd.DataFrame(columns=["shop", "product_id", "url", "priority"])
    df = pd.concat([pd.read_csv(f, dtype={"product_id": str}) for f in files], ignore_index=True)
    df["out"] = df["in_stock"].astype(str).str.lower().isin(["false", "0", "0.0"])
    last_day = df["date"].max()
    last = df.sort_values("date").drop_duplicates("product_id", keep="last").set_index("product_id")
    pre = df[(df["date"] == BEFORE_BF) & (df["seller"] == "eMAG")].set_index("product_id")
    sold = last.index[last.index.isin(df.loc[df["seller"] == "eMAG", "product_id"])]
    out = last.loc[sold, ["date", "url", "price", "seller", "out"]].copy()
    was = out.index.isin(pre.index)
    p0 = pre.reindex(out.index)
    gone = (out["date"] < last_day) | (out["seller"] != "eMAG") | (out["out"] & ~p0["out"].fillna(False).astype(bool))
    cheaper = out["price"] < p0["price"] - 0.005
    out["priority"] = 1
    out.loc[was & cheaper.fillna(False).to_numpy(), "priority"] = 3
    out.loc[was & ~cheaper.fillna(False).to_numpy(), "priority"] = 2
    out.loc[was & gone.to_numpy(), "priority"] = 0
    out = out.reset_index().sort_values(["priority", "date"], ascending=[True, False], kind="stable")
    return out.assign(shop="emag")[["shop", "product_id", "url", "priority"]]


def update(max_pages=None, delay=(3.0, 6.0), raw_dir=RAW, fetcher=None, robots=None, priorities=PRIORITIES):
    """Deschide paginile produselor încă neverificate din grupurile priorities (None = toate), în limita zilnică de
    pagini eMAG, comună cu part number-ul, și adaugă rezultatul în raw_dir/black_friday.csv. Întoarce numărul de
    pagini deschise."""
    from crawl import Blocked, HttpFetcher, Robots, looks_blocked, redirected_to_block  # aici: crawl.py importă modulul

    path = raw_dir / FILE_NAME
    done = load(path)
    todo = candidates(raw_dir)
    todo = todo[~todo["product_id"].isin(set(done["product_id"]))]
    if priorities is not None:
        todo = todo[todo["priority"].isin(priorities)]
    today = datetime.now(TZ).date().isoformat()
    used = int(part_numbers.pages_today(part_numbers.load(raw_dir / part_numbers.FILE_NAME), raw_dir, today)
               .get("emag", 0))
    quota = max(0, part_numbers.SHOP_MAX["emag"] - used)
    n = min(len(todo), quota, quota if max_pages is None else max_pages)
    if todo.empty:
        print("Black Friday: niciun produs nou de verificat")
        return 0
    if n <= 0:
        print(f"Black Friday: {len(todo)} produse neverificate; limita zilei la eMAG e atinsă "
              f"({part_numbers.SHOP_MAX['emag']} de pagini, deschise azi: {used}); continuă mâine")
        return 0
    print(f"Black Friday: {len(todo)} produse vândute de eMAG neverificate "
          f"({', '.join(f'prioritatea {k}: {v}' for k, v in todo['priority'].value_counts().sort_index().items())}); "
          f"deschid {n} pagini (~{max(1, round(n * max(sum(delay) / 2, part_numbers.HOST_GAP['emag']) / 60))} min)")
    own = fetcher is None
    fetcher, robots = fetcher or HttpFetcher(), robots or Robots()
    new, opened, found, errors = [], 0, 0, 0
    last_hit = float("-inf")
    try:
        for r in todo.head(n).itertuples(index=False):
            row = {"shop": "emag", "product_id": str(r.product_id), "bf_price": None, "page_price": None,
                   "page_seller": None, "page_stock": None, "checked_at": today}
            if not robots.allowed(r.url):
                new.append(row)
                continue
            if opened:
                gap = part_numbers.HOST_GAP["emag"] - (time.monotonic() - last_hit)
                time.sleep(max(random.uniform(*delay), gap, robots.crawl_delay(r.url) or 0))
            opened += 1
            last_hit = time.monotonic()
            try:
                page, status, final_url = fetcher.get(r.url)
            except Blocked as e:
                print(f"  [emag] blocat ({e}): opresc pentru azi")
                break
            except Exception as e:  # noqa: BLE001 - rețea: produsul rămâne neverificat, se reia data viitoare
                print(f"  [emag] eroare la {r.url}: {e}")
                errors += 1
                if errors >= part_numbers.MAX_ERRORS:
                    print(f"  [emag] {part_numbers.MAX_ERRORS} erori la rând: opresc pentru azi")
                    break
                continue
            errors = 0
            if page and (redirected_to_block(r.url, final_url, page) or looks_blocked(page)):
                print(f"  [emag] pagină de verificare anti-bot ({final_url[:70]}): opresc pentru azi")
                break
            if page:  # fără pagină (404): produsul a fost retras
                row.update(extract(page))
            new.append(row)
            if row["bf_price"] is not None:
                found += 1
                print(f"  {r.product_id}: preț de Black Friday epuizat {row['bf_price']:,.2f} lei "
                      f"(acum {row['page_price'] or 0:,.2f} lei, {row['page_seller'] or '?'})")
            if len(new) % 25 == 0:
                _save(new, path)
    except KeyboardInterrupt:
        print("  oprit manual (Ctrl+C); salvez ce s-a găsit până acum")
    finally:
        if new:
            _save(new, path)
        if own:
            fetcher.close()
    left = len(todo) - len(new)
    print(f"Black Friday: {len(new)} pagini verificate, {found} cu preț de Black Friday epuizat"
          + (f"; rămân {left} (continuă la rulările următoare, până pe {UNTIL})" if left else ""))
    return opened


def main():
    for stream in (sys.stdout, sys.stderr):  # ca în crawl.py: ă/ș/ț și cu ieșirea redirecționată
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="Prețurile de Black Friday epuizate, din paginile produselor eMAG")
    ap.add_argument("--max", type=int, help=f"câte pagini deschide (implicit cât permite limita zilei, "
                                            f"{part_numbers.SHOP_MAX['emag']} de pagini eMAG cu tot cu part number)")
    ap.add_argument("--delay", type=float, nargs=2, default=(3.0, 6.0), metavar=("MIN", "MAX"),
                    help="pauza dintre pagini, în secunde (implicit 3-6; la eMAG cel puțin 8)")
    ap.add_argument("--raw", type=Path, default=RAW, help="folderul cu CSV-urile zilnice (implicit data/raw)")
    ap.add_argument("--priorities", type=int, nargs="+", default=list(PRIORITIES), metavar="P",
                    help="grupurile verificate (vezi candidates; implicit toate: 0 1 2 3; ex. --priorities 0 pentru "
                         "cele mai probabile)")
    args = ap.parse_args()
    update(args.max, tuple(args.delay), args.raw, priorities=tuple(args.priorities))


if __name__ == "__main__":
    main()
