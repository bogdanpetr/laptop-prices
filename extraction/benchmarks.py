"""
Scoruri de performanță pentru procesoarele și plăcile video de laptop, de pe Notebookcheck:
    https://www.notebookcheck.net/Mobile-Processors-Benchmark-List.2436.0.html
    https://www.notebookcheck.net/Mobile-Graphics-Cards-Benchmark-List.844.0.html

    python extraction/benchmarks.py      # descarcă cele două liste (2 cereri) în data/external/

Ajunge o singură descărcare; opțional încă una înainte de analiza finală, ca să prindă procesoarele apărute între timp.
build_dataset.py le leagă de fiecare laptop prin cpu_model / gpu_model (extraction/specs.py) și adaugă
în products.csv coloanele din BENCH_COLUMNS. Tabelele descărcate rămân locale (data/external/ nu se
publică); în raport se citează Notebookcheck ca sursă.
"""
import re
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = ROOT / "data" / "external"
SOURCES = {
    "cpu": "https://www.notebookcheck.net/Mobile-Processors-Benchmark-List.2436.0.html",
    "gpu": "https://www.notebookcheck.net/Mobile-Graphics-Cards-Benchmark-List.844.0.html",
}
# câmpul nostru -> regex pe antetul coloanei din tabelul Notebookcheck
FIELDS = {
    "cpu": {"codename": r"^codename", "tdp_w": r"^tdp watt", "mhz": r"^mhz", "cores_threads": r"^cores",
            "perf": r"^perf", "cb23_single": r"cinebench r23 single", "cb23_multi": r"cinebench r23 multi",
            "gb6_single": r"geekbench 6.*single", "gb6_multi": r"geekbench 6.*multi"},
    "gpu": {"arch": r"^architecture", "perf": r"^perf", "timespy": r"time spy", "firestrike": r"fire strike"},
}
MIN_MODELS = 100  # listele au sute de modele; mai puține = pagină blocată sau structură schimbată
BENCH_COLUMNS = [  # nucleele din Notebookcheck completează cpu_cores (din titlu), nu au coloană separată
    "nbc_cpu", "nbc_cpu_codename", "nbc_cpu_threads", "nbc_cpu_tdp_w", "nbc_cpu_turbo_mhz",
    "nbc_cpu_perf", "nbc_cpu_cb23_single", "nbc_cpu_cb23_multi", "nbc_cpu_gb6_single", "nbc_cpu_gb6_multi",
    "nbc_gpu", "nbc_gpu_arch", "nbc_gpu_perf", "nbc_gpu_timespy", "nbc_gpu_firestrike",
]


def key(name):
    """Nume normalizat pentru potrivire: 'Intel Core Ultra 7 255H' și 'Core Ultra 7 255H' -> 'ultra 7 255h'."""
    s = str(name or "").lower().replace("™", "").replace("®", "")
    s = re.sub(r"\b(intel|amd|apple|qualcomm|nvidia|geforce|core(?= ultra| i\d| \d)|laptop gpu|laptop|gpu|"
               r"graphics|processor|mobile|max-q|soc)\b", " ", s)
    return re.sub(r"[\s\-()/]+", " ", s).strip()


# ---------------------------------------------------------------- descărcare și parsare

def _cells(tr):
    """Textul celulelor unui rând; o celulă cu colspan=2 apare de două ori, ca să rămână aliniată cu antetul."""
    out = []
    for c in tr.find_all(["td", "th"]):
        out += [c.get_text(" ", strip=True).replace("\xa0", " ")] * int(c.get("colspan") or 1)
    return out


def _num(text, last=False):
    """'~67.3 62%' -> 67.3 (scor estimat), '1833 n7' -> 1833 (media a 7 teste), '1800 ‑ 4900' -> 4900 (last)"""
    nums = re.findall(r"\d+(?:\.\d+)?", text or "")
    return float(nums[-1 if last else 0]) if nums else None


def parse_table(html, kind):
    """Tabelul Notebookcheck ('cpu' sau 'gpu') ca DataFrame cu valori numerice."""
    from bs4 import BeautifulSoup  # doar la descărcare; build_dataset.py are nevoie numai de pandas

    tables = BeautifulSoup(html, "lxml").find_all("table")
    if not tables:
        return pd.DataFrame(columns=["name", "key"])
    table = max(tables, key=lambda t: len(t.find_all("tr")))
    header, rows = None, []
    for tr in table.find_all("tr"):
        cells = _cells(tr)
        if cells and cells[0] == "Pos":  # antetul se repetă de mai multe ori în tabel
            header = [h.lower() for h in cells]
            continue
        if not header or len(cells) < 3 or not tr.find("a"):
            continue
        raw = {}
        for field, pat in FIELDS[kind].items():
            i = next((j for j, h in enumerate(header) if re.search(pat, h)), None)
            raw[field] = cells[i] if i is not None and i < len(cells) else ""
        row = {"name": cells[1], "key": key(cells[1])}
        if kind == "cpu":
            ct = re.search(r"(\d+)\s*/\s*(\d+)", raw["cores_threads"])
            row.update(codename=raw["codename"] or None, cores=int(ct.group(1)) if ct else None,
                       threads=int(ct.group(2)) if ct else None, tdp_w=_num(raw["tdp_w"]),
                       turbo_mhz=_num(raw["mhz"], last=True), perf=_num(raw["perf"]),
                       cb23_single=_num(raw["cb23_single"]), cb23_multi=_num(raw["cb23_multi"]),
                       gb6_single=_num(raw["gb6_single"]), gb6_multi=_num(raw["gb6_multi"]))
        else:
            row.update(arch=raw["arch"] or None, perf=_num(raw["perf"]), timespy=_num(raw["timespy"]),
                       firestrike=_num(raw["firestrike"]))
        rows.append(row)
    return pd.DataFrame(rows).drop_duplicates("key") if rows else pd.DataFrame(columns=["name", "key"])


def download(out_dir=EXTERNAL):
    """Descarcă cele două liste (cu fetcher-ul și verificarea robots.txt din crawl.py) și le salvează ca CSV.
    O descărcare eșuată (rețea, blocare, pagină schimbată) nu înlocuiește lista bună salvată deja."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from crawl import HttpFetcher, Robots  # import aici: crawl.py nu e necesar pentru build_dataset.py

    out_dir.mkdir(parents=True, exist_ok=True)
    robots, http = Robots(), HttpFetcher()
    try:
        for i, (kind, url) in enumerate(SOURCES.items()):
            if i:
                time.sleep(5)
            path = out_dir / f"notebookcheck_{kind}.csv"
            keep = f"; păstrez {path.name} existent" if path.exists() else ""
            if not robots.allowed(url):
                print(f"{kind}: robots.txt nu permite {url}{keep}")
                continue
            try:
                html, status, _ = http.get(url)
                table = parse_table(html or "", kind)
            except Exception as e:  # noqa: BLE001
                print(f"{kind}: descărcarea a eșuat ({e}){keep}")
                continue
            if len(table) < MIN_MODELS:
                print(f"{kind}: doar {len(table)} modele în pagină (blocată sau schimbată?){keep}")
                continue
            table.to_csv(path, index=False)
            print(f"{kind}: HTTP {status}, {len(table)} modele -> {path}")
    finally:
        http.close()


def load_tables(folder=EXTERNAL):
    """(tabel CPU, tabel GPU) salvate de download(); (None, None) dacă lipsesc."""
    paths = [folder / f"notebookcheck_{kind}.csv" for kind in SOURCES]
    if not all(p.exists() for p in paths):
        return None, None
    return tuple(pd.read_csv(p) for p in paths)


# ---------------------------------------------------------------- potrivirea cu laptopurile

def _pick(k, keys, prefer=None):
    """Cheia din tabel pentru k: potrivire exactă; altfel singurul nume care continuă k cu un cuvânt
    ('rtx pro 1000' -> 'rtx pro 1000 blackwell generation') sau cu sufixul lipsă din titlu ('i7 13620' ->
    'i7 13620h'). Între mai mulți candidați decide prefer()."""
    if not k:
        return None
    if k in keys:
        return k
    cands = ([c for c in keys if c.startswith(k + " ")]
             or [c for c in keys if c.startswith(k) and re.fullmatch(r"[a-z]{1,2}", c[len(k):])])
    if len(cands) > 1 and prefer:
        cands = prefer(cands)
    return cands[0] if len(cands) == 1 else None


def _most_cores(base, keys):
    """Varianta cu cele mai multe nuclee ('m5 10 core' față de 'm5 9 core'), când titlul nu le spune."""
    variants = [(int(m.group(1)), c) for c in keys if (m := re.fullmatch(rf"{re.escape(base)} (\d+) core", c))]
    return max(variants)[1] if variants else None


def match_cpu(cpu_brand, cpu_model, cpu_cores, keys):
    if not isinstance(cpu_model, str):
        return None
    if cpu_brand == "Apple":  # Notebookcheck: 'Apple M5 10-Core', 'Apple M5 Pro 18-Core', 'Apple M5 Max'
        base = key(cpu_model)
        if base in keys:
            return base
        if pd.notna(cpu_cores):  # titlul spune câte nuclee: doar varianta exactă, nu una apropiată
            return f"{base} {int(cpu_cores)} core" if f"{base} {int(cpu_cores)} core" in keys else None
        return _most_cores(base, keys)
    k = key(cpu_model)
    found = _pick(k, keys)
    if found is None and re.search(r"\bpro\b", k):  # 'Ryzen 5 PRO 8640HS' = 'Ryzen 5 8640HS' ca performanță
        found = _pick(re.sub(r"\s*\bpro\b", "", k), keys)
    m = re.fullmatch(r"Core i(\d)-(\d{3}[A-Z]*)", cpu_model)  # 'i5-210H' scris greșit în titlu = 'Core 5 210H'
    if found is None and m:
        found = _pick(key(f"Core {m.group(1)} {m.group(2)}"), keys)
    # 'Core 7 255HX' fără 'Ultra', 'Intel 240H' / 'Intel 358H' fără nivel, 'Intel 13420H' fără i5
    num = re.search(r"\b(\d{3,5}[a-z]{0,2})$", k)
    if found is None and cpu_brand == "Intel" and num:
        same = [c for c in keys if re.fullmatch(rf"(?:ultra |i)?x?[3579] {re.escape(num.group(1))}", c)]
        found = same[0] if len(same) == 1 else None
    if found is None and cpu_brand == "AMD" and num and re.fullmatch(r"ryzen \d{3}", k):  # 'AMD 260 8 C' fără nivel
        same = [c for c in keys if re.fullmatch(rf"ryzen (?:ai )?[3579] (?:pro )?{re.escape(num.group(1))}", c)]
        same = [c for c in same if " pro " not in c] or same  # titlul nu spune PRO
        found = same[0] if len(same) == 1 else None
    if found is None and cpu_brand == "AMD":  # 'AI' sau '+' lipsă / în plus: 'Ryzen 5 PRO 340' = 'Ryzen AI 5 PRO 340'
        alts = {k.replace("max+", "max"), k.replace("max ", "max+ "),
                re.sub(r"^ryzen (\d)", r"ryzen ai \1", k), k.replace("ryzen ai ", "ryzen ")} - {k}
        for alt in sorted(alts):
            found = _pick(alt, keys) or _pick(re.sub(r"\s*\bpro\b", "", alt), keys)
            if found:
                break
    return found


def match_gpu(gpu_brand, gpu_model, gpu_vram_gb, cpu_model, keys):
    if not isinstance(gpu_model, str):
        return None
    if gpu_brand == "Apple" and isinstance(cpu_model, str):  # 'Apple M5 10-Core GPU'
        base, n = key(cpu_model), re.match(r"(\d+)-core", gpu_model)
        if n:  # titlul spune câte nuclee: doar varianta exactă, nu una apropiată
            return f"{base} {n.group(1)} core" if f"{base} {n.group(1)} core" in keys else None
        return _most_cores(base, keys)

    def by_memory(cands):  # 'rtx 3050 4gb' / 'rtx 3050 6gb': după VRAM, altfel varianta mai mare
        if pd.notna(gpu_vram_gb):
            exact = [c for c in cands if c.endswith(f" {int(gpu_vram_gb)}gb")]
            if exact:
                return exact
        sized = [c for c in cands if re.search(r" \d+gb$", c)]
        return [max(sized, key=lambda c: int(re.search(r"(\d+)gb$", c).group(1)))] if sized else cands

    return _pick(key(gpu_model), keys, prefer=by_memory)


def add_benchmarks(products, cpu=None, gpu=None):
    """Adaugă coloanele BENCH_COLUMNS (goale dacă tabelele lipsesc) și completează cpu_cores cu nucleele
    din Notebookcheck (mai sigure decât titlul: 'Ultra 7 255HX (12-core)' are de fapt 20)."""
    out = products.copy()
    for col in BENCH_COLUMNS + ["nbc_cpu_cores"]:
        out[col] = None
    if cpu is not None:
        keys, cache = set(cpu["key"]), {}
        matched = []
        for r in out[["cpu_brand", "cpu_model", "cpu_cores"]].itertuples(index=False):
            k = (r.cpu_brand, r.cpu_model, None if pd.isna(r.cpu_cores) else int(r.cpu_cores))
            if k not in cache:
                cache[k] = match_cpu(*k, keys)
            matched.append(cache[k])
        t = cpu.set_index("key").reindex(matched)
        for col, src in (("nbc_cpu", "name"), ("nbc_cpu_codename", "codename"), ("nbc_cpu_cores", "cores"),
                         ("nbc_cpu_threads", "threads"), ("nbc_cpu_tdp_w", "tdp_w"),
                         ("nbc_cpu_turbo_mhz", "turbo_mhz"), ("nbc_cpu_perf", "perf"),
                         ("nbc_cpu_cb23_single", "cb23_single"), ("nbc_cpu_cb23_multi", "cb23_multi"),
                         ("nbc_cpu_gb6_single", "gb6_single"), ("nbc_cpu_gb6_multi", "gb6_multi")):
            out[col] = t[src].to_numpy()
    if gpu is not None:
        keys, cache = set(gpu["key"]), {}
        matched = []
        for r in out[["gpu_brand", "gpu_model", "gpu_vram_gb", "cpu_model"]].itertuples(index=False):
            k = (r.gpu_brand, r.gpu_model, None if pd.isna(r.gpu_vram_gb) else int(r.gpu_vram_gb), r.cpu_model)
            if k not in cache:
                cache[k] = match_gpu(*k, keys)
            matched.append(cache[k])
        t = gpu.set_index("key").reindex(matched)
        for col, src in (("nbc_gpu", "name"), ("nbc_gpu_arch", "arch"), ("nbc_gpu_perf", "perf"),
                         ("nbc_gpu_timespy", "timespy"), ("nbc_gpu_firestrike", "firestrike")):
            out[col] = t[src].to_numpy()
    for col in ("nbc_cpu_cores", "nbc_cpu_threads"):
        out[col] = pd.to_numeric(out[col]).astype("Int64")
    out["cpu_cores"] = out.pop("nbc_cpu_cores").fillna(out["cpu_cores"])
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    download()
