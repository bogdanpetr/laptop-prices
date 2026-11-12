"""
Statistici descriptive pentru prezentare (Faza 1).

Citește CSV-urile zilnice (data/raw) și products.csv (data/processed) și scrie:
    descriptives/tables/*.csv     tabelele
    descriptives/figures/*.png    graficele

    python descriptives/descriptives.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # fără fereastră, doar fișiere
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
FIG = ROOT / "descriptives" / "figures"
TAB = ROOT / "descriptives" / "tables"
STUDY = (10_000, 20_000)
BF_DAY = pd.Timestamp("2026-11-06")
BEFORE_BF = pd.Timestamp("2026-11-05")
KEY = ["shop", "product_id"]
MIN_GROUP = 30  # sub atâtea produse, un nivel de RAM nu apare în F4 (mediana n-ar fi de încredere)
SPECS = ["brand", "cpu_model", "cpu_tier", "cpu_suffix", "ram_gb", "ram_type", "storage_gb", "gpu_model",
         "screen_in", "resolution", "refresh_hz", "panel", "os", "nbc_cpu_perf", "nbc_gpu_perf"]


def load():
    files = sorted(RAW.glob("*/*.csv.gz"))
    daily = pd.concat((pd.read_csv(f, dtype={"product_id": str}) for f in files), ignore_index=True)
    daily = daily.dropna(subset=["price"])
    daily["date"] = pd.to_datetime(daily["date"])
    # ANABLUE: din 31.10 după seller; înainte, prețul întreg la eMAG (vezi README, „Ruptură în datele eMAG”)
    old = daily["seller"].isna() & (daily["date"] < "2026-10-31")
    daily["anablue"] = (daily["shop"] == "emag") & (
        (daily["seller"] == "ANABLUE COMPUTERS") | (old & (daily["price"] % 1 == 0)))

    products = pd.read_csv(ROOT / "data" / "processed" / "products.csv",
                           dtype={"product_id": str, "part_number": str})
    flag = daily.sort_values("date").groupby(KEY)["anablue"].last().reset_index()
    products = products.merge(flag, on=KEY, how="left")
    products["anablue"] = products["anablue"].fillna(False).astype(bool)
    products["group"] = np.where(products["anablue"], "emag ANABLUE", products["shop"])
    return daily, products


def factory(study):
    """Doar configurațiile de fabrică: produsele cu part number real. Ofertele din marketplace cu RAM / SSD mărite
    (ANABLUE și alți vânzători, ex. 8 GB RAM + 8 TB SSD la 19.000 lei) nu au part number, iar filtrul ANABLUE
    singur nu le prinde pe toate (seller gol înainte de 31.10, alți vânzători)."""
    return study[study["part_number"].notna()]


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / f"{name}.png", dpi=150)
    plt.close(fig)


def tables(daily, products, study):
    with_pn = study.dropna(subset=["part_number"])
    multi = (with_pn.groupby("part_number")["shop"].nunique() >= 2).sum()
    overview = pd.Series({
        "zile colectate": daily["date"].nunique(),
        "prețuri zilnice (rânduri)": len(daily),
        "produse (magazin, produs)": len(products),
        "în studiu (median 10-20k lei)": len(study),
        "  din care ANABLUE": int(study["anablue"].sum()),
        "  cu part number": len(with_pn),
        "part number-uri în ≥2 magazine": int(multi),
        "cu preț de Black Friday (bf_price)": int(study["bf_price"].notna().sum()),
    }, name="valoare")
    overview.to_csv(TAB / "t1_overview.csv")
    print(overview.to_string(), "\n")

    by_group = study.groupby("group")["price_median"].describe().round(0)
    by_group.to_csv(TAB / "t2_price_by_shop.csv")
    print(by_group.to_string(), "\n")

    numeric = ["ram_gb", "storage_gb", "screen_in", "refresh_hz", "gpu_vram_gb", "cpu_cores",
               "nbc_cpu_perf", "nbc_gpu_perf", "nbc_cpu_cb23_multi", "nbc_gpu_timespy"]
    clean = factory(study)  # configurațiile modificate ar deforma corelația cu RAM / SSD
    corr = clean[numeric + ["price_median"]].corr(method="spearman")["price_median"].drop("price_median")
    t3 = pd.DataFrame({"spearman": corr.round(2), "n": clean[numeric].notna().sum()})
    t3 = t3.sort_values("spearman", ascending=False)
    t3.to_csv(TAB / "t3_spearman.csv")
    print(f"Corelația Spearman cu prețul (configurații de fabrică, {len(clean)} produse):")
    print(t3.to_string(), "\n")


def fig_daily_counts(daily):
    d = daily[daily["price"].between(*STUDY)]  # același interval în toate zilele
    group = np.where(d["anablue"], "emag ANABLUE", d["shop"])
    counts = d.groupby(["date", group]).size().unstack()
    fig, ax = plt.subplots(figsize=(9, 4))
    counts.plot(ax=ax, marker="o")
    ax.axvline(BF_DAY, ls="--", c="gray")
    ax.text(BF_DAY, ax.get_ylim()[1] * 0.95, " Black Friday", color="gray")
    ax.set(xlabel="", ylabel="produse 10.000-20.000 lei", title="Produse colectate pe zi")
    save(fig, "f1_daily_counts")


def fig_price_by_shop(study):
    order = ["emag", "emag ANABLUE", "evomag", "cel"]
    data = [study.loc[study["group"] == g, "price_median"] for g in order]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.boxplot(data)  # etichetele separat: parametrul labels= a fost redenumit în matplotlib 3.9 și scos ulterior
    ax.set_xticks(range(1, len(order) + 1), [f"{g}\n(n={len(x)})" for g, x in zip(order, data)])
    ax.set(ylabel="preț median (lei)", title="Distribuția prețului pe magazin")
    save(fig, "f2_price_by_shop")


def fig_completeness(study):
    filled = study[SPECS].notna().mean().sort_values() * 100
    fig, ax = plt.subplots(figsize=(7, 5))
    filled.plot.barh(ax=ax)
    ax.set(xlabel="% produse cu valoare", xlim=(0, 100), title="Specificații completate (din titlu)")
    save(fig, "f3_completeness")


def fig_price_vs_specs(study):
    clean = factory(study)
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4))
    sizes = clean["ram_gb"].value_counts()
    levels = sorted(sizes[sizes >= MIN_GROUP].index)
    data = [clean.loc[clean["ram_gb"] == r, "price_median"] for r in levels]
    a.boxplot(data)
    a.set_xticks(range(1, len(levels) + 1), [f"{int(r)}\n(n={len(x)})" for r, x in zip(levels, data)])
    a.set(xlabel="RAM (GB)", ylabel="preț median (lei)", title="Preț după RAM")
    dedicated = clean["gpu_dedicated"].astype(str) == "True"
    for mask, label in [(~dedicated, "placă integrată"), (dedicated, "placă dedicată")]:
        b.scatter(clean.loc[mask, "nbc_cpu_perf"], clean.loc[mask, "price_median"], s=8, alpha=.5, label=label)
    b.set(xlabel="performanță procesor (Notebookcheck)", ylabel="preț median (lei)",
          title="Preț după performanța procesorului")
    b.legend()
    fig.suptitle(f"Configurații de fabrică (cu part number): {len(clean)} produse")
    save(fig, "f4_price_vs_specs")


def fig_discounts(daily, products):
    d = daily[~daily["anablue"]]
    # doar produsele din studiu prezente înainte de Black Friday (fără cele apărute prin lărgirea intervalului)
    seen = d.loc[(d["date"] == BEFORE_BF) & d["price"].between(*STUDY), KEY].drop_duplicates()
    base = d[d["date"] < BF_DAY].groupby(KEY)["price"].median().rename("base").reset_index()
    d = d.merge(seen, on=KEY).merge(base, on=KEY)
    d["reduced"] = d["price"] / d["base"] <= 0.95
    share = d.groupby(["date", "shop"])["reduced"].mean().unstack() * 100
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4))
    share.plot(ax=a, marker="o")
    a.axvline(BF_DAY, ls="--", c="gray")
    a.set(xlabel="", ylabel="% produse", title="Produse cu ≥5% sub prețul lor obișnuit")

    # reducerea de Black Friday: cel mai mic dintre prețul din listă (6.11) și bf_price, față de 5.11
    before = d[d["date"] == BEFORE_BF].set_index(KEY)["price"]
    on_bf = d[d["date"] == BF_DAY].set_index(KEY)["price"]
    bf = products.set_index(KEY)["bf_price"].dropna()
    best = pd.concat([on_bf, bf], axis=1).min(axis=1)
    disc = (1 - best / before).dropna() * 100
    disc = disc[disc > 0]
    b.hist(disc, bins=30)
    b.set(xlabel="reducere față de 5.11 (%)", ylabel="produse",
          title=f"Reduceri de Black Friday (n={len(disc)}, mediana {disc.median():.1f}%)")
    save(fig, "f5_discounts")


def fig_cross_shop(study):
    pn = study.dropna(subset=["part_number"])
    g = pn.groupby("part_number").filter(lambda x: x["shop"].nunique() >= 2).groupby("part_number")
    spread = (g["price_median"].max() / g["price_median"].min() - 1) * 100
    cheapest = pn.loc[g["price_median"].idxmin(), "shop"].value_counts()
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4))
    a.hist(spread, bins=30)
    a.set(xlabel="diferența max/min între magazine (%)", ylabel="laptopuri",
          title=f"Același laptop în ≥2 magazine (n={len(spread)}, mediana {spread.median():.1f}%)")
    cheapest.plot.bar(ax=b)
    b.set(xlabel="", ylabel="laptopuri", title="Cel mai ieftin magazin")
    save(fig, "f6_cross_shop")


def main():
    FIG.mkdir(parents=True, exist_ok=True)
    TAB.mkdir(parents=True, exist_ok=True)
    daily, products = load()
    study = products[products["in_price_range"]]
    tables(daily, products, study)
    fig_daily_counts(daily)
    fig_price_by_shop(study)
    fig_completeness(study)
    fig_price_vs_specs(study)
    fig_discounts(daily, products)
    fig_cross_shop(study)
    print(f"Grafice în {FIG}, tabele în {TAB}")


if __name__ == "__main__":
    main()
