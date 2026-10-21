"""
Funcții de parsare comune tuturor magazinelor.

- normalizarea textului (fără diacritice, spații multiple, simboluri ®™)
- extragerea prețurilor din text în format românesc (2.499,99 lei / 2.499 99 Lei)
- rating, număr de recenzii, disponibilitate (stoc)
- găsirea cardurilor de produs fără să depindem de clasele CSS (metoda "generic"),
  folosită automat când selectorii specifici unui magazin nu mai găsesc nimic
"""
import re
import unicodedata
from urllib.parse import urljoin

# ---------------------------------------------------------------- text

def strip_accents(s: str) -> str:
    """ă->a, î->i, ș/ş->s, ț/ţ->t etc."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def clean_text(s: str) -> str:
    """Scoate ® ™ ©, unifică spațiile (inclusiv non-breaking space)."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", "", s)  # resturi de tag-uri HTML (ex. X<sup>e</sup>)
    s = re.sub(r"[®™©]", "", s)
    s = s.replace("\xa0", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def norm(s: str) -> str:
    """Text pentru căutări: fără diacritice, litere mici."""
    return strip_accents(clean_text(s)).lower()


# ---------------------------------------------------------------- prețuri

# 2.499,99 lei | 2499,99 lei | 2.499 lei
# 2.499 99 lei   (evoMAG pune zecimalele în <sup>)
# 2.499 , 99 lei (eMAG: <sup><small>,</small>99</sup>, deci virgula ajunge între spații)
PRICE_RE = re.compile(
    r"(?<![\d.,])"
    r"(?P<int>\d{1,3}(?:[. ]\d{3})+|\d+)"
    r"(?:\s*,\s*(?P<dec>\d{2})|\s(?P<dec2>\d{2})(?=\s*(?:lei|ron)\b))?"
    r"\s*(?:lei|ron)\b"
)

MIN_PRICE, MAX_PRICE = 100, 200_000  # interval plauzibil pentru un laptop, în lei
MAX_DISCOUNT = 0.7  # o sumă sub 30% din cel mai mare preț din card nu e prețul laptopului
# evoMAG pune eticheta după sumă ('2.999,99 lei PRP'); urmată de ':' ar fi eticheta sumei următoare
RRP_AFTER_RE = re.compile(r"\s*(?:prp|pret recomandat)\b(?!\s*:)")


def _to_float(m: re.Match) -> float:
    whole = re.sub(r"[. ]", "", m.group("int"))
    dec = m.group("dec") or m.group("dec2") or "00"
    return float(f"{whole}.{dec}")


def parse_price(text: str):
    """Primul preț dintr-un text, ca float (ex. '2.499 99 Lei' -> 2499.99)."""
    m = PRICE_RE.search(norm(text or ""))
    return _to_float(m) if m else None


def find_prices(text: str):
    """Toate prețurile dintr-un text, fiecare cu un tip dedus din context:
    'price'       preț obișnuit (curent sau vechi, tăiat)
    'rrp'         PRP / preț recomandat de producător (eticheta înainte sau după sumă)
    'lowest_30d'  cel mai mic preț din ultimele 30 de zile (cerut de lege la reduceri)
    'installment' rată lunară (ex. 'De la 80 lei/luna')  -> ignorat
    'saving'      cât economisești (ex. 'Economisești 250 lei') -> ignorat
    'other'       garanție extinsă, voucher, cashback, preț 'Separat' la pachete...  -> ignorat
    """
    t = norm(text or "")
    out, prev_end = [], 0
    for m in PRICE_RE.finditer(t):
        before = t[max(prev_end, m.start() - 45):m.start()]
        after = t[m.end():m.end() + 12]
        end = m.end()
        rrp_after = RRP_AFTER_RE.match(t, m.end())
        if rrp_after:  # eticheta e consumată, ca să nu ajungă în contextul sumei următoare
            kind, end = "rrp", rrp_after.end()
        elif re.match(r"\s*/\s*lun", after) or re.search(r"\b(rate|rata)\b[^0-9]*$", before):
            kind = "installment"
        elif re.search(r"(economis\w*|reducere(\s+de)?|discount|-)\s*:?\s*$", before[-30:]):
            kind = "saving"
        elif re.search(r"(garantie extinsa|voucher|cashback|bonus|cupon|card cadou|asigurare|separat)"
                       r"[^0-9]{0,20}$", before[-35:]):
            kind = "other"
        elif re.search(r"\bprp\b|pret recomandat", before):
            kind = "rrp"
        elif re.search(r"minim|cel mai mic|mai mic pret|30 (de )?zile", before):
            kind = "lowest_30d"
        else:
            kind = "price"
        value = _to_float(m)
        if MIN_PRICE <= value <= MAX_PRICE:
            out.append((kind, value))
        prev_end = end
    return out


def pick_prices(card_text: str, current=None, struck=None):
    """Alege prețul curent / vechi / PRP / minim 30 zile dintr-un card.
    current: prețul găsit cu selectorul specific magazinului (dacă există)
    struck:  prețul tăiat găsit în <s>/<del> (dacă există)
    """
    found = find_prices(card_text)
    candidates = [v for k, v in found if k == "price"]
    if candidates:  # elimină sume mici rămase neclasificate (accesorii, vouchere)
        candidates = [v for v in candidates if v >= (1 - MAX_DISCOUNT) * max(candidates)]
    if current is None and candidates:
        current = min(candidates)  # prețul vechi e mereu mai mare decât cel curent
    old = None
    if struck and current and struck > current:
        old = struck
    elif current and candidates and max(candidates) > current:
        old = max(candidates)
    rrp = next((v for k, v in found if k == "rrp"), None)
    lowest = next((v for k, v in found if k == "lowest_30d"), None)
    return {"price": current, "old_price": old, "rrp": rrp, "lowest_30d": lowest}


# ---------------------------------------------------------------- rating & stoc

RATING_RE = re.compile(
    r"(?<![\d.,])([1-5][.,]\d{1,2})\s*\(\s*(\d{1,6})"
    r"(?:\s*(?:de\s*)?(?:review|recenz|evalu|opini)[\w-]*)?\s*\)"
)
# eMAG: nota, apoi numărul de recenzii ('4.31 13 review-uri (13)', '5 1 review (1)')
RATING_COUNT_RE = re.compile(
    r"(?<![\d.,])([1-5](?:[.,]\d{1,2})?)\s+(\d{1,6})\s*(?:de\s*)?(?:review|recenz|evalu|opini)"
)


def parse_rating(text: str):
    """'4.71 (170)' sau '4.71 170 review-uri' -> (4.71, 170). Dacă nu găsește: (None, None)."""
    t = norm(text or "")
    m = RATING_RE.search(t) or RATING_COUNT_RE.search(t)
    if not m:
        return None, None
    return float(m.group(1).replace(",", ".")), int(m.group(2))


# ordinea contează: expresiile mai lungi / negative înaintea celor scurte
STOCK_PHRASES = [
    ("nu este in stoc", False), ("stoc epuizat", False), ("indisponibil", False),
    ("precomanda", False), ("in curand", False),
    ("in stoc la furnizor", True), ("in stoc furnizor", True), ("stoc furnizor", True),
    ("ultimul produs", True), ("ultimele produse", True), ("stoc limitat", True),
    ("in stoc", True), ("disponibil", True),
]


def parse_stock(text: str):
    """Returnează (textul găsit, True/False/None)."""
    t = re.sub(r"ultimele \d+ produse", "ultimele produse", norm(text or ""))  # eMAG: 'ultimele 3 produse'
    for phrase, available in STOCK_PHRASES:
        if phrase in t:
            return phrase, available
    return None, None


# ---------------------------------------------------------------- carduri

def _ids_in(el, page_url, product_re):
    ids = set()
    for a in el.find_all("a", href=True):
        m = product_re.search(urljoin(page_url, a["href"]))
        if m:
            ids.add(m.group(1))
    return ids


def find_cards_generic(soup, page_url, product_re, max_card_chars=3000):
    """Găsește cardurile de produs fără clase CSS:
    1) ia toate linkurile spre pagini de produs (după forma URL-ului),
    2) pentru fiecare produs urcă în arborele HTML cât timp elementul părinte
       conține un singur produs -> cel mai mare astfel de element e cardul.
    Returnează o listă de (product_id, url, link, card).
    """
    first_link = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(page_url, a["href"])
        m = product_re.search(url)
        if m and m.group(1) not in first_link:
            first_link[m.group(1)] = (url, a)

    cards = []
    for pid, (url, link) in first_link.items():
        card = link
        while card.parent is not None and card.parent.name not in ("body", "html", "[document]"):
            parent = card.parent
            if len(_ids_in(parent, page_url, product_re)) > 1:
                break
            if len(parent.get_text(" ", strip=True)) > max_card_chars:
                break
            card = parent
        cards.append((pid, url, link, card))
    return cards


def best_title(*candidates):
    """Cel mai lung text 'rezonabil' dintre candidați (atribut title, text link, alt imagine)."""
    texts = [clean_text(c) for c in candidates if c and len(clean_text(c)) >= 10]
    return max(texts, key=len) if texts else None
