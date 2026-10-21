"""Stocarea în SQLite: data/laptops.db

prices      un rând pe (zi, magazin, produs): prețul laptopului în ziua respectivă
crawl_runs  un rând pe (zi, magazin): cum a mers rularea (pagini, produse, motivul opririi)

Rularea de două ori în aceeași zi înlocuiește datele zilei pentru magazinul respectiv.
O rulare fără produse (blocată, structură schimbată) NU șterge datele deja salvate.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    date         TEXT NOT NULL,      -- ziua colectării, AAAA-LL-ZZ
    scraped_at   TEXT NOT NULL,
    shop         TEXT NOT NULL,
    product_id   TEXT NOT NULL,
    title        TEXT,
    url          TEXT,
    price        REAL,               -- prețul curent, lei
    old_price    REAL,               -- prețul tăiat (înainte de reducere)
    rrp          REAL,               -- PRP / preț recomandat de producător
    lowest_30d   REAL,               -- cel mai mic preț din ultimele 30 de zile
    rating       REAL,
    n_reviews    INTEGER,
    stock_text   TEXT,
    in_stock     INTEGER,            -- 1 / 0 / NULL (necunoscut)
    page         INTEGER,
    rank         INTEGER,
    parse_method TEXT,
    PRIMARY KEY (date, shop, product_id)
);
CREATE INDEX IF NOT EXISTS idx_prices_product ON prices (shop, product_id, date);

CREATE TABLE IF NOT EXISTS crawl_runs (
    date            TEXT NOT NULL,
    shop            TEXT NOT NULL,
    started_at      TEXT,
    finished_at     TEXT,
    price_min       REAL,
    price_max       REAL,
    pages           INTEGER,
    seen_total      INTEGER,         -- produse găsite în liste, indiferent de preț
    products        INTEGER,         -- produse păstrate (în intervalul de preț)
    no_price        INTEGER,
    stopped_because TEXT,
    PRIMARY KEY (date, shop)
);
"""

PRICE_COLS = ["date", "scraped_at", "shop", "product_id", "title", "url", "price", "old_price",
              "rrp", "lowest_30d", "rating", "n_reviews", "stock_text", "in_stock",
              "page", "rank", "parse_method"]


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def count_saved(con, day, shop_name):
    """Câte produse sunt deja salvate pentru (zi, magazin)."""
    return con.execute("SELECT COUNT(*) FROM prices WHERE date=? AND shop=?", (day, shop_name)).fetchone()[0]


def typical_count(con, shop_name, before_day, price_min, price_max, days=7):
    """Câte produse are de obicei magazinul: mediana rulărilor din ultimele zile dinaintea lui before_day,
    cu același interval de preț. None dacă nu există încă istoric."""
    counts = sorted(r[0] for r in con.execute(
        "SELECT products FROM crawl_runs WHERE shop=? AND date<? AND price_min=? AND price_max=? AND products>0 "
        "ORDER BY date DESC LIMIT ?", (shop_name, before_day, price_min, price_max, days)))
    return counts[len(counts) // 2] if counts else None


def save_run(con, rows, log, shop_name, day, scraped_at):
    """Salvează jurnalul și (dacă există) produsele. Returnează numărul de produse scrise."""
    lo, hi = (log.get("price_range") or [None, None])
    with con:  # o singură tranzacție
        con.execute(
            # o rulare goală nu înlocuiește jurnalul unei rulări reușite din aceeași zi
            f"INSERT OR {'REPLACE' if rows else 'IGNORE'} INTO crawl_runs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (day, shop_name, log.get("started_at"), log.get("finished_at"), lo, hi,
             log.get("pages"), log.get("seen_total"), log.get("products"),
             log.get("no_price"), log.get("stopped_because")))
        if not rows:
            return 0
        con.execute("DELETE FROM prices WHERE date=? AND shop=?", (day, shop_name))
        data = []
        for r in rows:
            r = dict(r, date=day, scraped_at=scraped_at, shop=shop_name)
            r["in_stock"] = None if r.get("in_stock") is None else int(bool(r["in_stock"]))
            data.append(tuple(r.get(c) for c in PRICE_COLS))
        con.executemany(
            f"INSERT INTO prices ({','.join(PRICE_COLS)}) VALUES ({','.join('?' * len(PRICE_COLS))})",
            data)
    return len(rows)
