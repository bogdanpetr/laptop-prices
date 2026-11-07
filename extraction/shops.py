"""
Configurația fiecărui magazin + parsarea unei pagini de listare.

Pentru fiecare magazin încercăm întâi selectorii CSS specifici (metoda "selector").
Dacă aceștia nu găsesc niciun produs (de ex. magazinul și-a schimbat designul),
trecem automat la metoda "generic" din parsing.py, care nu depinde de clase CSS.
Coloana parse_method din CSV arată ce metodă a fost folosită.

Pentru a adăuga un magazin nou: adaugă un Shop în SHOPS și rulează
    python extraction/crawl.py --test --shops <nume>
"""
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from parsing import (best_title, clean_text, find_cards_generic, norm, parse_price,
                     parse_rating, parse_stock, pick_prices)


@dataclass
class Shop:
    name: str
    start_url: str            # pagina 1 a categoriei ({min} și {max} = intervalul de preț, dacă e cazul)
    product_re: re.Pattern    # forma URL-ului unui produs; grupul 1 = ID-ul produsului
    page_url: str = ""        # șablon pentru paginile 2, 3, ... (folosește {n}, opțional {min}/{max});
                              # gol = urmăm linkul "pagina următoare" găsit în pagină
    sorted_desc: bool = False  # start_url listează produsele de la cel mai scump la cel mai ieftin
    card_selector: str = ""   # selectori CSS (încercați primii; dacă lipsesc, metoda generic)
    title_selector: str = ""
    price_selector: str = ""
    price_json: str = ""      # atribut cu un JSON care conține "price"; are prioritate față de price_selector
    offer_attr: str = ""      # atributul cardului cu ID-ul ofertei, al cărei vânzător e în datele paginii (eMAG)
    listed_re: re.Pattern = None  # câte produse spune magazinul că are lista (grupul 1), pentru verificarea rulării
    use_browser: bool = False  # True dacă produsele sunt încărcate cu JavaScript
    wait_selector: str = ""    # ce așteaptă browserul să apară în pagină
    max_pages: int = 100       # limita de pagini pentru o listă
    list_page_limit: int = 0   # câte pagini arată magazinul dintr-o listă (0 = fără limită); o listă mai lungă
                               # e cerută din nou în două jumătăți ale intervalului de preț (crawl.crawl_ranges)

    def url_for_page(self, n: int, next_url: str = "", pmin: float = 0, pmax: float = 0):
        """URL-ul paginii n (None dacă nu îl putem determina)."""
        prices = {"min": int(pmin), "max": int(pmax)}
        if n == 1:
            return self.start_url.format(**prices)
        return self.page_url.format(n=n, **prices) if self.page_url else (next_url or None)


# Configurația a fost verificată pe paginile reale din 18.10.2026. Dacă un magazin dă 0 produse, rulează
#   python extraction/crawl.py --test
# și ajustează URL-ul, regex-ul sau selectorii de mai jos.
SHOPS = {
    "emag": Shop(
        name="emag",
        # doar laptopurile din intervalul de preț (filtrul eMAG), de la cel mai scump la cel mai ieftin.
        # La 10.000-20.000 lei lista a avut 78-89 de pagini, apoi 22 din 31.10.2026, după ce un vânzător din
        # marketplace și-a scos ofertele; la 5.000-25.000 lei (intervalul implicit din 6.11.2026) 64 de pagini, apoi
        # ~180 din 7.11.2026, când același vânzător (ANABLUE COMPUTERS) s-a întors.
        # eMAG arată cel mult 100 de pagini dintr-o listă; dacă lista crește peste atât, crawlerul o cere din nou
        # ca două liste, jumătatea scumpă și cea ieftină a intervalului (list_page_limit, crawl.crawl_ranges)
        start_url="https://www.emag.ro/laptopuri/pret,intre-{min}-si-{max}/sort-pricedesc/c",
        page_url="https://www.emag.ro/laptopuri/pret,intre-{min}-si-{max}/sort-pricedesc/p{n}/c",
        sorted_desc=True,
        product_re=re.compile(r"emag\.ro/[^?#]*?/pd/([A-Z0-9]+)/?"),
        card_selector="#card_grid > div.card-item",  # doar grila, fără caruselele de recomandări
        title_selector="a.card-v2-title, .card-v2-title, h2",
        price_selector=".product-new-price",
        price_json="data-product",  # prețul produsului; .product-new-price poate fi prețul unui pachet
        offer_attr="data-offer-id",  # vânzătorul ofertei afișate: eMAG sau un vânzător din marketplace
        # '<strong>1 - 60</strong> din <strong>1282</strong> de produse' (sub 20 de produse, fără „de”)
        listed_re=re.compile(r"din\s*<strong>\s*([\d.]+)\s*</strong>\s*(?:de\s+)?produse"),
        max_pages=300,
        list_page_limit=100,
    ),
    "evomag": Shop(
        name="evomag",
        start_url="https://www.evomag.ro/portabile-laptopuri-notebook/",
        # produs = .../portabile-laptopuri-notebook/<nume>-<ID numeric>.html; păstrăm doar ID-ul,
        # care nu se schimbă dacă magazinul editează titlul
        product_re=re.compile(r"evomag\.ro/portabile-laptopuri-notebook/[^?#]*-(\d+)\.html"),
        card_selector="div.nice_product_item",
        title_selector="h2.npi_name a",
        price_selector=".npi_price p",
        max_pages=60,
    ),
    "cel": Shop(
        name="cel",
        start_url="https://www.cel.ro/laptop-laptopuri/",
        # produs = .../<nume>-p<ID>-l/ (ID-ul conține și litere mari, ex. -pMCIwMjImPyk-l/)
        product_re=re.compile(r"cel\.ro/[a-z0-9-]+-(p[A-Za-z0-9]+)-l/?(?:[?#]|$)"),
        card_selector="div.product_data",
        title_selector="h2.productTitle a",
        price_selector=".pret_n",
        max_pages=60,
    ),
}

COLUMNS = [
    "date", "scraped_at", "shop", "product_id", "title", "url",
    "price", "old_price", "rrp", "lowest_30d",
    "rating", "n_reviews", "stock_text", "in_stock", "seller",
    "page", "rank", "parse_method",
]


def _struck_price(card):
    """Prețul tăiat (<s>, <del> sau o clasă care conține 'old')."""
    for el in card.select("s, del, [class*=old-price], [class*=price-old], [class*=old_price]"):
        p = parse_price(el.get_text(" ", strip=True))
        if p:
            return p
    return None


def _title_candidates(card, page_url, product_re):
    """Textul și atributul title ale tuturor linkurilor spre produs + alt-ul imaginilor.
    (Primul link e adesea imaginea, iar titlul complet stă în atributul title al altui link.)"""
    out = []
    for a in card.find_all("a", href=True):
        if product_re.search(urljoin(page_url, a["href"])):
            out += [a.get("title"), a.get_text(" ", strip=True)]
            out += [img.get("alt") for img in a.find_all("img")]
    return out


def _json_price(card, attr):
    """Prețul dintr-un atribut JSON al unui element din card (eMAG: data-product='{"price": 2055.79, ...}')."""
    el = card.select_one(f"[{attr}]") if attr else None
    try:
        price = json.loads(el[attr]).get("price") if el else None
        return float(price) if price else None
    except (ValueError, TypeError, AttributeError):
        return None


_OFFER_SELLER = re.compile(r'"offer":\{"id":(\d+),"vendor":\{"name":\{"default":"((?:[^"\\]|\\.)*)"')


def _offer_sellers(html):
    """ID-ul ofertei -> vânzătorul, din datele produselor pe care eMAG le pune în pagina de listă:
    "offer":{"id":443983537,"vendor":{"name":{"default":"eMAG","display":"eMAG"}}}"""
    sellers = {}
    for offer_id, name in _OFFER_SELLER.findall(html):
        try:
            sellers[offer_id] = json.loads(f'"{name}"')  # escape-urile JSON din nume (ex. MM CORP SPÓŁKA)
        except ValueError:
            sellers[offer_id] = name
    return sellers


def listed_count(shop, html):
    """Câte produse spune magazinul că are lista (None dacă nu afișează numărul sau nu îl găsim)."""
    m = shop.listed_re.search(html or "") if shop.listed_re else None
    return int(m.group(1).replace(".", "")) if m else None


def _row_from_card(card, pid, url, title, price_el=None, current=None, seller=None):
    text = card.get_text(" ", strip=True)
    if current is None and price_el is not None:
        current = parse_price(price_el.get_text(" ", strip=True))
    prices = pick_prices(text, current=current, struck=_struck_price(card))
    rating, n_reviews = parse_rating(text)
    stock_text, in_stock = parse_stock(text)
    return {
        "product_id": pid, "title": title,
        "url": re.sub(r"[?#].*", "", url),  # fără parametrii de tracking (?ref=..., &recid=...)
        **prices,
        "rating": rating, "n_reviews": n_reviews,
        "stock_text": stock_text, "in_stock": in_stock, "seller": seller,
    }


def _parse_with_selectors(shop, soup, page_url, sellers):
    rows = []
    if not shop.card_selector:
        return rows
    for card in soup.select(shop.card_selector):
        link = None
        for a in card.find_all("a", href=True):
            if shop.product_re.search(urljoin(page_url, a["href"])):
                link = a
                break
        if link is None:
            continue
        url = urljoin(page_url, link["href"])
        pid = shop.product_re.search(url).group(1)
        title_el = card.select_one(shop.title_selector) if shop.title_selector else None
        # titlul din selector are prioritate: alt-ul imaginilor poate avea cuvinte-cheie adăugate
        # (CEL: '... laptop laptopuri notebook ultrabook')
        title = (clean_text(card.get("data-name") or "")
                 or (best_title(title_el.get("title"), title_el.get_text(" ", strip=True)) if title_el else None)
                 or best_title(*_title_candidates(card, page_url, shop.product_re)))
        price_el = card.select_one(shop.price_selector) if shop.price_selector else None
        seller = sellers.get(card.get(shop.offer_attr)) if shop.offer_attr else None
        rows.append(_row_from_card(card, pid, url, title, price_el, _json_price(card, shop.price_json), seller))
    return rows


def _parse_generic(shop, soup, page_url):
    rows = []
    for pid, url, link, card in find_cards_generic(soup, page_url, shop.product_re):
        title = best_title(*_title_candidates(card, page_url, shop.product_re))
        row = _row_from_card(card, pid, url, title)
        if row["price"] is not None:  # un "card" fără preț nu e un produs (ex. link din meniu)
            rows.append(row)
    return rows


def parse_listing(shop: Shop, html: str, page_url: str):
    """Returnează (lista de produse, metoda folosită)."""
    soup = BeautifulSoup(html, "lxml")
    sellers = _offer_sellers(html) if shop.offer_attr else {}
    rows, method = _parse_with_selectors(shop, soup, page_url, sellers), "selector"
    # metoda generic doar dacă selectorii nu mai găsesc niciun produs cu preț (designul s-a schimbat);
    # altfel ar lua și produsele din meniuri, carusele sau "vizualizate recent"
    if not any(r["price"] for r in rows):
        rows, method = _parse_generic(shop, soup, page_url), "generic"
    # un produs poate apărea de două ori pe pagină (ex. în caruselul de promoții)
    unique, seen = [], set()
    for r in rows:
        if r["product_id"] not in seen:
            seen.add(r["product_id"])
            r["title"] = clean_text(r["title"] or "")
            unique.append(r)
    return unique, method


_NEXT_TEXTS = {"urmatoarea", "urmator", "next", "inainte", ">", "›", "»", ">>"}


def find_next_url(html: str, page_url: str):
    """Linkul spre pagina următoare a listei (rel=next, aria-label sau text 'Următoarea', »)."""
    soup = BeautifulSoup(html, "lxml")
    cands = soup.select("a[rel~=next], link[rel~=next]")
    for el in cands:
        if el.get("href"):
            return urljoin(page_url, el["href"])
    for a in soup.find_all("a", href=True):
        label = norm(a.get("aria-label") or a.get("title") or "")
        text = norm(a.get_text(" ", strip=True))
        if text in _NEXT_TEXTS or re.search(r"\b(urmatoarea|next)\b", label):
            href = urljoin(page_url, a["href"])
            if href.split("#")[0] != page_url.split("#")[0]:
                return href
    return None
