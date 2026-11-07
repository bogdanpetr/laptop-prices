"""
Crawler zilnic pentru prețurile laptopurilor (eMAG, evoMAG, CEL.ro).

Parcurge categoria "laptopuri" din fiecare magazin, păstrează doar laptopurile cu
prețul între --min-price și --max-price (implicit 5.000 - 25.000 lei; până pe 5.11.2026, 10.000 - 20.000)
și salvează într-o bază de date SQLite: data/laptops.db
    tabelul prices      - un rând pe (zi, magazin, produs)
    tabelul crawl_runs  - jurnalul fiecărei rulări
și în data/raw/<AAAA-LL-ZZ>/<magazin>.csv.gz (datele publicate în repo; --no-csv le oprește).
La final deschide pagina fiecărui produs nou, o singură dată, pentru part number (part_numbers.py).

Exemple:
    python extraction/crawl.py --test              # 1 pagină/magazin, verificare rapidă
    python extraction/crawl.py                     # rularea completă (o dată pe zi)
    python extraction/crawl.py --shops emag --max-pages 3
    python extraction/crawl.py --min-price 8000 --max-price 25000
    python extraction/crawl.py --browser           # browser automat pentru toate magazinele

Reguli de politețe: o singură cerere odată, pauză aleatoare între pagini,
respectă robots.txt și se oprește imediat dacă magazinul blochează accesul.
"""
import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from bs4 import BeautifulSoup
from protego import Protego

sys.path.insert(0, str(Path(__file__).resolve().parent))
import black_friday  # noqa: E402
import part_numbers  # noqa: E402
from db import connect, count_saved, save_run, typical_count  # noqa: E402
from shops import COLUMNS, SHOPS, find_next_url, listed_count, parse_listing  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DB_PATH = DATA / "laptops.db"
TZ = ZoneInfo("Europe/Bucharest")
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
}
BLOCK_CODES = {401, 403, 429, 451, 511}
LISTED_SHARE = 0.95  # o listă parcursă până la capăt are cel puțin atâtea din produsele anunțate de magazin


class Blocked(Exception):
    """Magazinul a refuzat accesul (captcha, 403, 429...). Ne oprim, nu insistăm."""

    def __init__(self, msg, html=None):
        super().__init__(msg)
        self.html = html


# ---------------------------------------------------------------- descărcare

class HttpFetcher:
    """Descarcă HTML-ul simplu, cu requests (rapid; merge pentru eMAG, evoMAG și CEL)."""

    def __init__(self, timeout=30, retries=3):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout, self.retries = timeout, retries

    def get(self, url, wait_selector=None):
        for attempt in range(1, self.retries + 1):
            try:
                r = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                print(f"    eroare de rețea ({e.__class__.__name__}), reîncerc în {15 * attempt}s")
                time.sleep(15 * attempt)
                continue
            if r.status_code in BLOCK_CODES:
                raise Blocked(f"HTTP {r.status_code}", html=r.text)
            if r.status_code == 404:
                return None, r.status_code, r.url
            if r.status_code >= 500:
                print(f"    HTTP {r.status_code}, reîncerc în {30 * attempt}s")
                time.sleep(30 * attempt)
                continue
            r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
            return r.text, r.status_code, r.url
        raise RuntimeError(f"nu am putut descărca {url} după {self.retries} încercări")

    def close(self):
        self.session.close()


class BrowserFetcher:
    """Deschide pagina într-un Chromium automat (Playwright), ca să ruleze JavaScript-ul.
    Util doar dacă un magazin încarcă lista de produse după deschiderea paginii
    (eMAG, evoMAG și CEL trimit prețurile direct în HTML)."""

    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=headless)
        self.context = self.browser.new_context(
            user_agent=USER_AGENT, locale="ro-RO", viewport={"width": 1366, "height": 900})
        self.page = self.context.new_page()

    def get(self, url, wait_selector=None):
        from playwright.sync_api import TimeoutError as PwTimeout
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        status = resp.status if resp else None
        if status in BLOCK_CODES:
            raise Blocked(f"HTTP {status}", html=self.page.content())
        if status == 404:
            return None, status, self.page.url
        if wait_selector:
            try:
                self.page.wait_for_selector(wait_selector, timeout=20_000)
            except PwTimeout:
                pass  # parsăm ce s-a încărcat; dacă e gol, crawl_shop se oprește
        for _ in range(8):  # derulăm în jos ca să se încarce și produsele "lazy"
            self.page.mouse.wheel(0, 2500)
            self.page.wait_for_timeout(350)
        self.page.wait_for_timeout(800)
        return self.page.content(), status, self.page.url

    def close(self):
        self.context.close()
        self.browser.close()
        self._pw.stop()


# ---------------------------------------------------------------- robots.txt

class Robots:
    def __init__(self):
        self._cache = {}

    def _rules(self, url):
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._cache:
            try:
                r = requests.get(base + "/robots.txt", headers=HEADERS, timeout=20)
                self._cache[base] = Protego.parse(r.text) if r.status_code == 200 else None
            except requests.RequestException:
                self._cache[base] = None
        return self._cache[base]

    def allowed(self, url) -> bool:
        rules = self._rules(url)
        return True if rules is None else rules.can_fetch(url, "*")

    def crawl_delay(self, url):
        rules = self._rules(url)
        return rules.crawl_delay("*") if rules is not None else None


def looks_blocked(html: str) -> bool:
    """Pagină de verificare anti-bot? Caută în textul vizibil, nu în scripturi: multe pagini
    normale conțin 'captcha' în cod (ex. RECAPTCHA_SITEKEY pentru formulare, la CEL)."""
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True).lower()[:300_000]
    return any(s in text for s in ("captcha", "access denied", "are you a robot", "verify you are human",
                                   "checking your browser", "verificare de securitate", "too many requests",
                                   "nu sunt robot", "nu sunteti robot", "security check", "attention required"))


# ---------------------------------------------------------------- crawl

def _same_page(url):
    """URL-ul fără parametri și fără '/' la final, ca să comparăm pagina cerută cu cea primită."""
    return url.split("#")[0].split("?")[0].rstrip("/")


def _site(url):
    """Domeniul fără 'www.' (evomag.ro), ca să recunoaștem o redirecționare spre alt site."""
    return urlsplit(url).netloc.lower().removeprefix("www.")


def redirected_to_block(url, final_url, html):
    """True dacă cererea a ajuns pe o pagină de verificare anti-bot: alt site (evoMAG: captcha.distinct.ro),
    'captcha' în adresă sau text de verificare într-o pagină la care am fost redirecționați."""
    if _site(final_url) != _site(url) or "captcha" in final_url.lower():
        return True
    return _same_page(final_url) != _same_page(url) and looks_blocked(html)


def _is_sorted_desc(prices):
    """True dacă prețurile de pe pagină scad (aproape) monoton: sortarea după preț e activă."""
    if len(prices) < 5:
        return False
    ok = sum(1 for x, y in zip(prices, prices[1:]) if x >= y * 0.98)
    return ok / (len(prices) - 1) >= 0.9


def crawl_shop(shop, fetcher, robots, max_pages, delay, price_range, debug_dir=None, list_range=None):
    """Parcurge lista unui magazin. price_range: produsele păstrate (după prețul afișat); list_range: intervalul
    cerut magazinului în URL, dacă e altul (o parte a listei, vezi crawl_ranges)."""
    pmin, pmax = price_range
    lmin, lmax = list_range or price_range
    started = datetime.now(TZ)
    rows, seen = [], set()
    # complete = rularea a ajuns la sfârșitul listei (nu s-a oprit din cauza unei blocări, erori sau a limitei)
    # listed = câte produse spune magazinul, pe pagina 1, că are lista (eMAG; None la celelalte)
    log = {"shop": shop.name, "started_at": started.isoformat(timespec="seconds"),
           "price_range": [pmin, pmax], "pages": 0, "products": 0, "seen_total": 0, "listed": None,
           "no_price": 0, "stopped_because": None, "complete": False, "methods": {}}
    crawl_delay = robots.crawl_delay(shop.start_url) or 0
    next_url, early_stop = "", False
    for n in range(1, max_pages + 1):
        if shop.list_page_limit and n > shop.list_page_limit:
            # magazinul nu arată mai multe pagini din listă; crawl_ranges o cere din nou, împărțită
            log["stopped_because"] = f"lista are peste {shop.list_page_limit} pagini"
            log["list_limit"] = True
            break
        url = shop.url_for_page(n, next_url, lmin, lmax)
        if url is None:
            log["stopped_because"] = f"nu există pagină următoare după pagina {n - 1}"
            log["complete"] = True
            break
        if not robots.allowed(url):
            log["stopped_because"] = f"robots.txt nu permite {url}"
            print(f"  [{shop.name}] {log['stopped_because']}")
            break
        try:
            html, status, final_url = fetcher.get(url, wait_selector=shop.wait_selector or None)
        except Blocked as e:
            log["stopped_because"] = f"blocat ({e}) la pagina {n}"
            log["blocked"] = True
            print(f"  [{shop.name}] BLOCAT: {e}. Ne oprim pentru azi.")
            if debug_dir and e.html:
                (debug_dir / f"{shop.name}_p{n}_blocat.html").write_text(e.html, encoding="utf-8")
            break
        except KeyboardInterrupt:
            log["stopped_because"] = "oprit manual (Ctrl+C)"
            log["interrupted"] = True
            break
        except Exception as e:  # noqa: BLE001
            log["stopped_because"] = f"eroare la pagina {n}: {e}"
            print(f"  [{shop.name}] eroare: {e}")
            break
        if html is None:
            log["stopped_because"] = (f"HTTP {status} la pagina {n}" +
                                      (" -> verifică start_url din shops.py" if n == 1 else
                                       " (sfârșitul listei)"))
            log["complete"] = n > 1
            print(f"  [{shop.name}] {log['stopped_because']}")
            break
        if redirected_to_block(url, final_url, html):
            # nu e sfârșitul listei: rularea rămâne incompletă și nu înlocuiește datele bune ale zilei
            log["stopped_because"] = f"blocat: pagina {n} a fost redirecționată la {final_url}"
            log["blocked"] = True
            print(f"  [{shop.name}] BLOCAT: pagina {n} a fost redirecționată la {final_url[:90]}. "
                  "Ne oprim pentru azi.")
            break
        if n > 1 and _same_page(final_url) != _same_page(url):
            # eMAG trimite o pagină de după sfârșitul listei filtrate la lista nefiltrată, pe același site
            log["stopped_because"] = f"pagina {n} a fost redirecționată la {final_url} (sfârșitul listei)"
            log["complete"] = True
            break
        if debug_dir:
            (debug_dir / f"{shop.name}_p{n}.html").write_text(html, encoding="utf-8")

        page_rows, method = parse_listing(shop, html, final_url)
        if n == 1:
            log["listed"] = listed_count(shop, html)
        if debug_dir and n == 1:
            log["sample"] = page_rows[:5]  # pentru --test: exemple de pe pagina 1, indiferent de preț
        log["pages"] = n
        log["methods"][method] = log["methods"].get(method, 0) + 1
        if (n == 1 and shop.list_page_limit and max_pages > shop.list_page_limit and log["listed"] and page_rows
                and log["listed"] > shop.list_page_limit * len(page_rows)):
            # magazinul spune pe pagina 1 că lista are mai multe pagini decât arată: crawl_ranges o cere din nou,
            # împărțită, fără să parcurgă întâi primele 100 de pagini (ar fi cerute de două ori). Pe 7.11.2026 ANABLUE
            # COMPUTERS și-a pus ofertele înapoi în listă: 10.634 de produse la 5.000-25.000 lei, ~178 de pagini
            log["stopped_because"] = (f"lista are {log['listed']} de produse, peste cele {shop.list_page_limit} de "
                                      "pagini pe care le arată magazinul")
            log["list_limit"] = True
            break
        if not page_rows:
            if looks_blocked(html):
                reason = "pagină blocată / captcha"
                log["blocked"] = True
            else:  # după pagina 1, o pagină fără produse înseamnă că lista s-a terminat
                reason = "fără produse (sfârșitul listei)" if n > 1 else "niciun produs găsit"
                log["complete"] = n > 1
            log["stopped_because"] = f"{reason} la pagina {n}"
            print(f"  [{shop.name}] pagina {n}: {reason}. Ne oprim.")
            break
        new = [r for r in page_rows if r["product_id"] not in seen]
        if not new:  # magazinul ne-a trimis înapoi la o pagină deja văzută -> am terminat
            log["stopped_because"] = f"pagina {n} nu mai are produse noi (sfârșitul listei)"
            log["complete"] = True
            break

        prices = [r["price"] for r in new if r["price"] is not None]
        if n == 1 and shop.sorted_desc:
            early_stop = _is_sorted_desc(prices)
            if not early_stop:
                print(f"  [{shop.name}] atenție: lista nu pare sortată după preț; "
                      f"parcurg toate paginile (max {max_pages})")

        kept = 0
        for r in new:
            seen.add(r["product_id"])
            if r["price"] is None:
                log["no_price"] += 1
            elif pmin <= r["price"] <= pmax:
                r.update(page=n, rank=len(seen), parse_method=method)
                rows.append(r)
                kept += 1
        print(f"  [{shop.name}] pagina {n}: {len(new)} produse, {kept} în intervalul "
              f"{pmin:,.0f}-{pmax:,.0f} lei ({method}) | total păstrate {len(rows)}")

        if early_stop and prices and max(prices) < pmin:
            log["stopped_because"] = f"prețurile au scăzut sub {pmin:,.0f} lei (listă sortată)"
            log["complete"] = True
            break
        if not shop.page_url:  # fără șablon de pagini: urmăm linkul "pagina următoare"
            next_url = find_next_url(html, final_url) or ""
        if n < max_pages:
            try:
                time.sleep(max(random.uniform(*delay), crawl_delay))
            except KeyboardInterrupt:
                log["stopped_because"] = "oprit manual (Ctrl+C)"
                log["interrupted"] = True
                break
    else:
        log["stopped_because"] = f"limita de {max_pages} pagini"

    log["products"] = len(rows)
    log["seen_total"] = len(seen)
    log["finished_at"] = datetime.now(TZ).isoformat(timespec="seconds")
    return rows, log


def crawl_ranges(shop, fetcher, robots, max_pages, delay, price_range, debug_dir=None, list_range=None):
    """crawl_shop pe tot intervalul. Dacă lista are mai multe pagini decât arată magazinul (eMAG: 100 de pagini,
    adică 6.000 de produse), e cerută din nou ca două liste, jumătatea scumpă și cea ieftină a intervalului,
    fiecare parcursă până la capăt (și împărțită la rândul ei, dacă e nevoie). Filtrul de preț al magazinului
    e exact, deci reuniunea listelor are toate produsele; cele găsite de mai multe ori sunt păstrate o dată.
    (Continuarea listei de la un preț nu e exactă: eMAG filtrează după alt preț decât cel afișat.)"""
    rows, log = crawl_shop(shop, fetcher, robots, max_pages, delay, price_range, debug_dir, list_range)
    lo, hi = list_range or price_range
    mid = math.floor((lo + hi) / 2)
    if not log.get("list_limit") or "{min}" not in (shop.page_url or "") or not lo < mid < hi:
        return rows, log
    print(f"  [{shop.name}] lista {lo:,.0f}-{hi:,.0f} lei are peste {shop.list_page_limit} pagini: o cer din nou "
          f"ca două liste, {mid:,}-{hi:,.0f} și {lo:,.0f}-{mid:,} lei")
    parts = [crawl_ranges(shop, fetcher, robots, max_pages, delay, price_range, debug_dir, (mid, hi))]
    if not (parts[0][1].get("interrupted") or parts[0][1].get("blocked")):
        time.sleep(random.uniform(*delay))  # aceeași pauză ca între pagini, și între cele două liste
        parts.append(crawl_ranges(shop, fetcher, robots, max_pages, delay, price_range, debug_dir, (lo, mid)))
    merged, ids = [], set()
    for r in [r for part_rows, _ in parts for r in part_rows] + rows:  # rândurile primei liste: doar ce lipsește
        if r["product_id"] not in ids:
            ids.add(r["product_id"])
            merged.append(r)
    for i, r in enumerate(merged, 1):
        r["rank"] = i  # poziția în listele parcurse, de la cel mai scump la cel mai ieftin
    logs = [part_log for _, part_log in parts]
    methods = {}
    for part_log in [log] + logs:
        for m, k in part_log["methods"].items():
            methods[m] = methods.get(m, 0) + k
    complete = len(logs) == 2 and all(part_log["complete"] for part_log in logs)
    out = dict(log, pages=log["pages"] + sum(part_log["pages"] for part_log in logs), products=len(merged),
               seen_total=sum(part_log["seen_total"] for part_log in logs) or log["seen_total"],
               no_price=sum(part_log["no_price"] for part_log in logs), methods=methods, complete=complete,
               finished_at=logs[-1]["finished_at"],
               stopped_because=f"lista {lo:,.0f}-{hi:,.0f} împărțită: "
                               + " | ".join(part_log["stopped_because"] or "" for part_log in logs))
    out.pop("list_limit")
    for flag in ("blocked", "interrupted"):
        if any(part_log.get(flag) for part_log in logs):
            out[flag] = True
    return merged, out


def check_count(log, n_rows, typical):
    """O rulare „completă” cu prea puține produse devine incompletă: de obicei e o pagină de blocare nerecunoscută,
    luată drept sfârșitul listei. Dacă magazinul spune câte produse are lista (eMAG), se compară cu acel număr;
    altfel cu typical, numărul obișnuit de produse (mediana zilelor anterioare). Întoarce o notă pentru rezumat
    când lista e completă, dar mult mai scurtă decât de obicei (altfel "")."""
    if not log["complete"]:
        return ""
    listed, short = log.get("listed"), bool(typical) and n_rows < typical / 2
    if listed and log["seen_total"] < listed * LISTED_SHARE:
        log["complete"] = False
        log["stopped_because"] += f" | suspect: {log['seen_total']} produse văzute, lista are {listed}"
        print(f"  ATENȚIE: am văzut doar {log['seen_total']} din cele {listed} produse ale listei: probabil o blocare "
              "sau o pagină schimbată; rularea e tratată ca incompletă")
    elif short and not listed:
        log["complete"] = False
        log["stopped_because"] += f" | suspect: doar {n_rows} produse, de obicei ~{typical}"
        print(f"  ATENȚIE: doar {n_rows} produse, de obicei ~{typical}: probabil o blocare sau o pagină schimbată; "
              "rularea e tratată ca incompletă")
    elif short:
        # lista chiar e mai scurtă și magazinul o confirmă (31.10.2026: un vânzător din marketplace-ul eMAG
        # și-a scos ~3.600 de oferte, iar lista a scăzut de la ~4.850 la 1.282 de produse)
        note = f"doar {n_rows} produse, de obicei ~{typical}, dar lista e completă: magazinul arată {listed}"
        print(f"  notă: {note}")
        return note
    return ""


def _write_atomic(path, write):
    """Scrie într-un fișier temporar, apoi îl pune în locul celui vechi: o oprire în timpul scrierii
    (Ctrl+C, laptop închis) lasă fișierul vechi întreg, nu unul pe jumătate scris."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    write(tmp)
    tmp.replace(path)


def save(rows, log, shop_name, day, scraped_at, out_dir):
    """Scrie snapshot-ul zilei și jurnalul rulării. Ca în baza de date, o rulare fără produse
    nu suprascrie CSV-ul și nici jurnalul unei rulări reușite din aceeași zi."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{shop_name}_log.json"
    if not rows:
        if not log_path.exists():
            _write_atomic(log_path, lambda p: p.write_text(json.dumps(log, ensure_ascii=False, indent=2),
                                                           encoding="utf-8"))
        return None
    for r in rows:
        r.update(date=day, scraped_at=scraped_at, shop=shop_name)
    df = pd.DataFrame(rows, columns=COLUMNS)
    for col in ("n_reviews", "page", "rank"):
        df[col] = df[col].astype("Int64")
    path = out_dir / f"{shop_name}.csv.gz"
    _write_atomic(path, lambda p: df.to_csv(p, index=False, compression="gzip"))
    _write_atomic(log_path, lambda p: p.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"))
    return path


def print_sample(rows, k=5):
    for r in rows[:k]:
        price = f"{r['price']:.2f} lei" if r["price"] else "FĂRĂ PREȚ"
        extra = []
        if r.get("old_price"):
            extra.append(f"vechi {r['old_price']:.2f}")
        if r.get("lowest_30d"):
            extra.append(f"min30z {r['lowest_30d']:.2f}")
        if r.get("rating"):
            extra.append(f"{r['rating']}★ ({r['n_reviews']})")
        if r.get("stock_text"):
            extra.append(r["stock_text"])
        if r.get("seller"):
            extra.append(f"vândut de {r['seller']}")
        print(f"     - {(r['title'] or '?')[:75]:<75} | {price} | {', '.join(extra)}")


def main():
    # pe Windows, cu ieșirea redirecționată într-un fișier (rularea programată), Python ar folosi
    # cp1252 și s-ar opri la primul mesaj cu ă/ș/ț; line_buffering: mesajele apar imediat în log
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    ap = argparse.ArgumentParser(description="Crawler zilnic pentru prețurile laptopurilor")
    ap.add_argument("--shops", nargs="+", choices=list(SHOPS), default=list(SHOPS))
    ap.add_argument("--max-pages", type=int, help="limită de pagini per magazin")
    ap.add_argument("--delay", type=float, nargs=2, default=(3.0, 6.0), metavar=("MIN", "MAX"),
                    help="pauza dintre pagini, în secunde (implicit 3-6)")
    # studiul privește laptopurile de 10.000-20.000 lei; colectarea merge de la 5.000 la 25.000, ca un laptop din
    # interval să fie urmărit și când e redus cu până la 50% sau se scumpește cu până la 25%. Până pe 5.11.2026
    # intervalul a fost 10.000-20.000: de Black Friday, produsele de la margini au ieșit din listă tocmai când au fost
    # reduse sau când oferta eMAG s-a epuizat și s-a afișat una mai scumpă (vezi README, „Intervalul de preț”)
    ap.add_argument("--min-price", type=float, default=5_000, help="preț minim, lei (implicit 5000)")
    ap.add_argument("--max-price", type=float, default=25_000, help="preț maxim, lei (implicit 25000)")
    ap.add_argument("--no-csv", action="store_true",
                    help="nu salva CSV.gz-ul zilnic în data/raw/ (implicit se salvează)")
    ap.add_argument("--db", type=Path, default=DB_PATH, help="fișierul SQLite (implicit data/laptops.db)")
    ap.add_argument("--test", action="store_true",
                    help="o pagină per magazin, afișează exemple, salvează HTML-ul în data/debug/")
    ap.add_argument("--browser", action="store_true",
                    help="folosește browserul automat (Playwright) pentru toate magazinele")
    ap.add_argument("--show-browser", action="store_true", help="arată fereastra browserului")
    ap.add_argument("--max-part-numbers", type=int, default=part_numbers.DEFAULT_MAX, metavar="N",
                    help="la final, câte pagini de produs deschide pentru part number-urile produselor noi "
                         f"(implicit {part_numbers.DEFAULT_MAX}; 0 = deloc)")
    args = ap.parse_args()

    now = datetime.now(TZ)
    day, scraped_at = now.date().isoformat(), now.isoformat(timespec="seconds")
    robots = Robots()
    debug_dir = DATA / "debug" if args.test else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    http, browser, con = None, None, None
    found, interrupted = [], False  # produsele găsite azi, pentru part number
    summary = []  # (magazin, complet?, ce s-a salvat, motivul opririi, notă) pentru rezumatul de la final
    try:
        for name in args.shops:
            shop = SHOPS[name]
            print(f"\n=== {name} ===")
            if shop.use_browser or args.browser:
                if browser is None:
                    try:
                        browser = BrowserFetcher(headless=not args.show_browser)
                    except Exception as e:  # noqa: BLE001
                        print(f"  nu pot porni browserul automat ({e}).\n"
                              "  Rulează: pip install playwright && playwright install chromium")
                        continue
                fetcher = browser
            else:
                http = http or HttpFetcher()
                fetcher = http

            if args.test:
                first = shop.url_for_page(1, "", args.min_price, args.max_price)
                print(f"  robots.txt: pagina 1 {'permisă' if robots.allowed(first) else 'NEPERMISĂ'}")
            max_pages = 1 if args.test else (args.max_pages or shop.max_pages)
            rows, log = crawl_ranges(shop, fetcher, robots, max_pages, tuple(args.delay),
                                     (args.min_price, args.max_price), debug_dir)

            if args.test:
                print(f"  pagina 1: {log['seen_total']} produse găsite, {len(rows)} în interval, "
                      f"{log['no_price']} fără preț (metoda: {', '.join(log['methods']) or '-'})"
                      + (f"; lista are {log['listed']} de produse" if log.get("listed") else ""))
                print("  primele produse de pe pagina 1 (compară titlul și prețul cu site-ul):")
                print_sample(log.get("sample", []))
                if log["seen_total"] and not rows:
                    print("  -> niciunul în intervalul de preț pe pagina 1: normal la eMAG (lista începe cu "
                          "cele mai scumpe) sau dacă lista nu e sortată.")
                if debug_dir and log["pages"]:
                    print(f"  HTML salvat în {debug_dir / f'{name}_p1.html'}")
                if not log["seen_total"]:
                    print("  -> 0 produse: deschide HTML-ul salvat. Dacă e o pagină de verificare anti-bot, oprește-te"
                          " și reîncearcă mâine; altfel s-a schimbat structura și trebuie ajustat shops.py.")
                continue

            if not log.get("blocked"):  # un magazin care ne blochează azi nu mai primește nici cereri pentru part number
                found += [{"shop": name, "product_id": r["product_id"], "url": r["url"], "title": r["title"],
                           "price": r["price"], "seller": r.get("seller")} for r in rows]
            if con is None:
                con = connect(args.db)
            note = check_count(log, len(rows), typical_count(con, name, day, args.min_price, args.max_price))
            already = count_saved(con, day, name)
            if rows and not log["complete"] and already >= len(rows):
                # o rulare oprită la jumătate (blocare, eroare, Ctrl+C) nu înlocuiește una mai completă din aceeași zi
                print(f"  rulare incompletă ({len(rows)} produse): păstrez cele {already} produse salvate deja azi")
                kept = f"păstrate cele {already} produse salvate deja azi"
            else:
                n = save_run(con, rows, log, name, day, scraped_at)
                if n:
                    print(f"  salvat {n} produse în {args.db}")
                    kept = f"salvate {n} produse"
                else:
                    print("  niciun produs de salvat (datele salvate deja rămân neschimbate)")
                    kept = f"niciun produs nou; rămân {already} salvate deja azi" if already else "niciun produs"
                if not args.no_csv:
                    save(rows, log, name, day, scraped_at, DATA / "raw" / day)
            summary.append((name, log["complete"], kept, log["stopped_because"], note))
            if log["stopped_because"]:
                print(f"  oprit: {log['stopped_because']}")
            if log.get("interrupted"):
                interrupted = True
                break
        # după ce prețurile zilei sunt salvate: o blocare aici nu le mai poate afecta
        if found and args.max_part_numbers > 0 and not interrupted:
            if day <= black_friday.UNTIL and any(f["shop"] == "emag" for f in found):
                # înaintea part number-ului: bannerul cu prețul de Black Friday epuizat dispare după campanie,
                # part number-ul nu (paginile eMAG ale zilei intră în aceeași limită, part_numbers.SHOP_MAX)
                print("\n=== Black Friday: prețurile epuizate, din pagina produsului ===")
                try:
                    black_friday.update(delay=tuple(args.delay), raw_dir=DATA / "raw")
                except Exception as e:  # noqa: BLE001 - prețurile zilei sunt salvate; part number-ul continuă
                    print(f"  eroare la verificarea pentru Black Friday: {e}")
            print("\n=== part number ===")
            part_numbers.update(pd.DataFrame(found), args.max_part_numbers, tuple(args.delay),
                                DATA / "raw" / part_numbers.FILE_NAME)
        if summary:
            print(f"\n=== rezumat {day} ===")
            for name, complete, kept, reason, note in summary:
                print(f"  {name:<7} {'complet   ' if complete else 'INCOMPLET '} {kept}"
                      + (f"\n          notă: {note}" if complete and note else "")
                      + ("" if complete else f"\n          motiv: {reason}"))
            if not all(complete for _, complete, *_ in summary):
                print("  -> pentru magazinele incomplete rulează din nou mai târziu sau mâine; datele bune salvate "
                      "deja nu sunt înlocuite de o rulare incompletă")
    finally:
        if http:
            http.close()
        if browser:
            browser.close()
        if con:
            con.close()


if __name__ == "__main__":
    main()
