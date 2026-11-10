# Prețurile laptopurilor în România: colectare zilnică

Proiect pentru cursul IAPD (Învățare automată și procesarea datelor), master Știința Datelor, ACS UPB.

**Echipa:** Petric Bogdan, Amet Levent

**Tema:** estimarea prețului unui laptop din specificațiile sale și analiza evoluției prețurilor
(inclusiv de Black Friday) pentru a prezice momentul optim de cumpărare.

Acest depozit conține deocamdată partea de **extracție**: un crawler care vizitează zilnic
eMAG, evoMAG și CEL.ro, păstrează laptopurile cu prețul între 5.000 și 25.000 lei (studiul privește
laptopurile de 10.000–20.000 lei; în afara acestui interval sunt urmărite reducerile și scumpirile lor, vezi
„Intervalul de preț”)
și le salvează în CSV-uri zilnice (`data/raw/`, publicate în acest depozit) și într-o bază de date SQLite locală.
Pentru fiecare produs nou se citește o singură dată part number-ul (codul producătorului), care leagă același
laptop între magazine. Din titlurile produselor se extrag apoi specificațiile (procesor, RAM, stocare, placă video,
ecran, brand).

## Structura

```
extraction/
    crawl.py          programul principal (se rulează o dată pe zi)
    shops.py          configurația fiecărui magazin (URL-uri, forma linkurilor de produs)
    parsing.py        extragerea prețurilor, ratingului și stocului din HTML
    db.py             schema și scrierea în SQLite
    part_numbers.py   part number-ul fiecărui produs, din pagina lui (o singură dată per produs)
    black_friday.py   prețurile de Black Friday 2026 epuizate la eMAG, din pagina produsului
    specs.py          extragerea specificațiilor din titlul produsului
    benchmarks.py     scorurile de performanță ale procesoarelor și plăcilor video (Notebookcheck)
    build_dataset.py  setul de date final: un rând pe produs, cu specificații, scoruri și rezumatul prețurilor
data/
    raw/              CSV-uri zilnice, <data>/<magazin>.csv.gz, part_numbers.csv și black_friday.csv (publicate în depozit)
    processed/        products.csv, creat de build_dataset.py (publicat în depozit)
    external/         listele de benchmark descărcate de benchmarks.py (local)
    laptops.db        aceleași date ca în raw/, în SQLite (local; se creează la prima rulare)
    debug/            HTML-ul paginilor, doar la rularea cu --test (local)
analysis/             scripturi pentru modele (urmează)
docs/                 prezentări și raport (urmează)
requirements.txt
```

## Instalare

Ai nevoie de Python 3.9 sau mai nou.

```bash
cd laptop-prices
python3 -m venv .venv                 # mediu virtual, ca să nu amesteci bibliotecile cu cele ale sistemului
source .venv/bin/activate             # activează mediul (pe Windows: .venv\Scripts\activate)
pip install -r requirements.txt       # instalează bibliotecile necesare
```

## Folosire

Toate comenzile se rulează din folderul proiectului, cu mediul `.venv` activat: în PowerShell
`.venv\Scripts\activate` (la începutul liniei apare `(.venv)`). În VS Code alege o singură dată
`Ctrl+Shift+P` → **Python: Select Interpreter** → `.venv`, iar terminalele noi îl activează singure.

### 1. Test rapid (prima dată, și după orice modificare în `shops.py`)

```bash
python extraction/crawl.py --test
```

Pentru fiecare magazin descarcă **o singură pagină**, afișează câteva produse găsite și salvează
HTML-ul în `data/debug/`. Nu scrie nimic în baza de date. Ce să urmărești:

| Mesaj | Ce înseamnă |
| --- | --- |
| `pagina 1: 60 produse găsite, 6 în interval` | Merge, dacă exemplele afișate au titlul și prețul de pe site. |
| `0 în interval` la eMAG | Normal: lista e sortată descrescător, iar pagina 1 are cele mai scumpe laptopuri. |
| `0 produse` | URL-ul sau structura paginii nu se potrivesc. Deschide fișierul din `data/debug/`. |
| `BLOCAT` sau `HTTP 403/429` | Magazinul a refuzat accesul. Oprește-te și încearcă a doua zi. |
| `HTTP 404 la pagina 1` | `start_url` din `shops.py` nu mai există. Găsește URL-ul categoriei în browser. |
| `robots.txt nu permite` | Magazinul interzice accesul automat la acea pagină. Crawlerul se oprește. |

### 2. Rularea completă

```bash
python extraction/crawl.py
```

Se face **o singură dată pe zi**. Durează câteva minute, din cauza pauzei de 3–6 secunde între pagini.
La final apare un **rezumat**: pentru fiecare magazin, `complet` sau `INCOMPLET` (cu motivul) și ce s-a salvat.
Dacă un magazin e `INCOMPLET` (de exemplu evoMAG a cerut un captcha), rulează din nou mai târziu sau a doua zi:
datele bune salvate deja în ziua respectivă nu sunt înlocuite de o rulare incompletă. O `notă` sub un magazin
`complet` înseamnă că lista a fost parcursă până la capăt, dar are mult mai puține produse decât de obicei (de
exemplu pe 31.10.2026, când un vânzător din marketplace-ul eMAG și-a scos ofertele); nu e nevoie să rulezi din nou.

După ce prețurile zilei sunt salvate, crawlerul deschide pagina fiecărui produs **nou** (cel mult 300 pe rulare,
cu cel puțin 8 secunde între două cereri la eMAG și 15 la evoMAG, ca să nu declanșeze blocarea anti-bot)
și îi citește part number-ul în `data/raw/part_numbers.csv`. Un produs se verifică o singură dată, așa că
zilnic rămân doar produsele apărute între timp; restul locurilor completează treptat produsele colectate în
zilele anterioare. Numărul de pagini îl dă `--max` (la `part_numbers.py`) sau `--max-part-numbers` (la crawler) și e
respectat exact. Sute de pagini eMAG pe zi au dus pe 21.10, 23.10 și 7.11.2026 la blocarea adresei IP (HTTP 511,
„trafic neobișnuit de mare”). La o blocare, verificarea se oprește pentru magazinul respectiv, iar produsele rămase
se reiau la rularea următoare.
Nu se deschid paginile ofertelor eMAG ale vânzătorilor din marketplace care pun în pagină coduri proprii în locul
part number-ului (configurații cu RAM sau SSD mărite, coduri interne). Un astfel de vânzător e recunoscut după cel
puțin 10 pagini verificate, cu part number la mai puțin de 5% dintre ele (pe 5.11.2026: ANABLUE COMPUTERS, LAPTOK.COM,
MM CORP SPÓŁKA, TEKADVICE, DMAG, Home Center Distribution, Metrostore). La produsele văzute ultima dată înainte de
31.10.2026, când lista nu arăta vânzătorul, prețul întreg
(fără bani) ține locul lui: atunci era aproape mereu ANABLUE COMPUTERS (doar 5% aveau part number, față de 65% la
prețurile cu bani). Aceste produse rămân fără part number; `build_dataset.py` le numără separat de cele care
așteaptă verificarea. O blocare
la acest pas nu afectează prețurile zilei, salvate deja; part number-ul nu se schimbă, deci nu se pierde
nimic dacă e citit peste câteva zile.
Pe 6–7.11.2026, înaintea part number-ului, crawlerul a deschis și paginile produselor vândute de eMAG, pentru
prețurile de Black Friday epuizate (`black_friday.py`, vezi mai jos), în limita de atunci de 150 de pagini eMAG pe zi.
Bannerul a dispărut pe 7.11, așa că acest pas nu mai rulează (`black_friday.UNTIL`).

**De ce part number-ul nu înlocuiește codul magazinului.** Lista de produse, din care se iau zilnic prețurile,
are doar codul intern al magazinului (`product_id`); part number-ul apare numai în pagina produsului. Colectarea
zilnică rămâne deci legată de `product_id`, iar `part_numbers.csv` îi asociază part number-ul. Acesta lipsește
la configurațiile modificate de vânzătorii din marketplace-ul eMAG (RAM sau SSD mărite), care nu au un part
number real: acolo vânzătorii trec coduri proprii (`213440GB1TB`), care sunt ignorate. Part number-ul e păstrat
doar cu litere mari și cifre, fiindcă CEL îl scrie fără semne (`MDVC4ROA`, `NHU64EX00B`), iar eMAG și evoMAG
cu semne (`MDVC4RO/A`, `NH.U64EX.00B`); forma afișată de magazin rămâne în coloana `code`.

### 3. Setul de date cu specificații

```bash
python extraction/benchmarks.py       # o singură dată (2 cereri către Notebookcheck); opțional încă o dată înainte de analiza finală
python extraction/build_dataset.py
```

`benchmarks.py` descarcă în `data/external/` listele de benchmark de pe Notebookcheck pentru procesoarele și
plăcile video de laptop. `build_dataset.py` citește toate CSV-urile din `data/raw/` și scrie
`data/processed/products.csv`: un rând pe (magazin, produs), cu specificațiile extrase din titlu, scorurile de
performanță ale procesorului și plăcii video și rezumatul prețurilor din toate zilele colectate. Se rulează oricând
e nevoie de date actualizate (de exemplu înainte de analiză) și nu face cereri către magazine. Dacă `products.csv`
e deschis în Excel, închide-l întâi. Fișierul e salvat în UTF-8 cu BOM, ca Excel să afișeze corect diacriticele
și simbolurile (`Xe²`, nu `XeÂ²`); pandas îl citește normal cu `pd.read_csv`.

| Coloane | Conținut |
| --- | --- |
| `part_number`, `seller` | Codul producătorului, doar litere și cifre (același laptop are același cod în toate magazinele; gol la produsele fără part number real sau încă neverificate) și vânzătorul: la eMAG cel din ultima listă în care a apărut produsul (din 31.10.2026), altfel cel din pagina produsului, citit o dată cu part number-ul |
| `brand` | Producătorul (Lenovo, ASUS, HP, Dell, Apple...); sub-mărcile duc la producător (Legion -> Lenovo) |
| `cpu_brand`, `cpu_model`, `cpu_tier`, `cpu_suffix`, `cpu_cores` | Procesorul: Intel / AMD / Apple / Qualcomm, modelul normalizat (`Core Ultra 7 255H`, `Ryzen AI 9 HX 370`, `M5 Pro`), nivelul 3/5/7/9, sufixul (U, H, HX...) și numărul de nuclee (din Notebookcheck; din titlu dacă procesorul nu e în listă) |
| `ram_gb`, `ram_type`, `storage_gb` | Memoria (GB, DDR5 / LPDDR5X...) și stocarea (GB; 1 TB = 1024) |
| `gpu_brand`, `gpu_model`, `gpu_dedicated`, `gpu_vram_gb` | Placa video (`RTX 5070`, `Radeon 860M`...), dacă e dedicată și memoria ei (0 la cele integrate). Plăcile dedicate apar în titlu, așa că un laptop fără placă în titlu, care nu e de gaming sau stație de lucru, are placa integrată a procesorului (`gpu_model` gol) |
| `screen_in`, `resolution`, `megapixels`, `refresh_hz`, `panel`, `touch` | Ecranul: diagonala, rezoluția (`FHD+`, `QHD+`, `2560x1600`...), frecvența, OLED / IPS / Mini-LED, tactil |
| `os`, `is_gaming`, `is_refurbished` | Sistemul de operare, laptop de gaming, resigilat / recondiționat |
| `nbc_cpu`, `nbc_cpu_codename`, `nbc_cpu_threads`, `nbc_cpu_tdp_w`, `nbc_cpu_turbo_mhz`, `nbc_cpu_perf`, `nbc_cpu_cb23_single`, `nbc_cpu_cb23_multi`, `nbc_cpu_gb6_single`, `nbc_cpu_gb6_multi` | Procesorul găsit în lista Notebookcheck (numele oficial, util ca identificator unic): generația, thread-urile, TDP, frecvența maximă, scorul de performanță, Cinebench R23 și Geekbench 6 |
| `nbc_gpu`, `nbc_gpu_arch`, `nbc_gpu_perf`, `nbc_gpu_timespy`, `nbc_gpu_firestrike` | Placa video găsită în lista Notebookcheck: arhitectura, scorul de performanță, 3DMark Time Spy și Fire Strike |
| `price_last`, `price_min`, `price_max`, `price_median`, `rrp_last` | Ultimul preț, minimul, maximul și mediana din zilele colectate; ultimul PRP afișat |
| `in_price_range` | `True` dacă prețul median e între 10.000 și 20.000 lei: populația studiului. `False` la produsele cu prețul median în afara intervalului, colectate din 6.11.2026 doar ca să fie urmărite reducerile și scumpirile (vezi „Intervalul de preț”) |
| `bf_price` | La eMAG, prețul de Black Friday (6.11.2026) epuizat înainte de rularea crawlerului, din pagina produsului; gol în rest (vezi „Prețurile de Black Friday epuizate”) |
| `rating`, `n_reviews`, `first_seen`, `last_seen`, `n_days` | Recenziile și perioada în care produsul a fost găsit |

**Celule goale.** O valoare rămâne goală doar când informația nu există în sursă; nu se inventează nimic.
Nicio coloană din `products.csv` nu e complet goală (pe datele din 18.10.2026: 20% celule goale, toate
specificațiile principale peste 85%). Coloanele cu multe goluri și de ce:

| Coloană (% completat) | De ce lipsește | În analiză |
| --- | --- | --- |
| `rrp_last` (3%), `rating`, `n_reviews` (4%) | Magazinele afișează PRP-ul și recenziile doar la puține produse; CEL nu arată recenzii în listă | indicator „are recenzii” sau se exclud |
| `refresh_hz` (12%) | Apare în titlu mai ales la gaming (68% din ele); la celelalte de obicei e 60 Hz, dar titlul nu o spune | indicator „afișaj rapid” sau se exclude |
| `nbc_gpu_*` (25–32%) | Există pentru 95% din plăcile dedicate; la cele integrate titlul spune doar „Intel UHD” / „Intel Graphics”, fără generație | se folosesc împreună cu `gpu_dedicated`; la cele integrate contează scorurile procesorului |
| `panel` (55%), `ram_type` (76%), `resolution` (85%), `os` (85%) | Nu toate titlurile le menționează | categorie separată „necunoscut” |
| `cpu_suffix` (77%), `cpu_tier` (90%) | Apple, Snapdragon și procesoarele AMD/Intel noi (`Ryzen AI 7 350`, `Core Ultra 7 355`) nu au sufix; Apple nu are nivel 3/5/7/9 | categorie „fără” |
| `nbc_cpu_*` (94%) | Titlul nu precizează modelul („Core Ultra 7”) sau modelul nu e în lista Notebookcheck (`Ryzen 7 7445HS`) | imputare după `cpu_tier` / `cpu_brand` |

În CSV-urile zilnice (`data/raw/`), `lowest_30d` e goală: niciun magazin nu scrie în listă „cel mai mic preț
din ultimele 30 de zile”, nici de Black Friday (6.11.2026). `old_price` apare doar la produsele reduse: e prețul
tăiat de pe card. La ofertele vândute de eMAG înseamnă o scădere reală față de ziua anterioară, dar unii vânzători
din marketplace (de exemplu LAPTOK.COM) afișează zilnic același preț tăiat, deci reducerea reală se calculează din
istoricul `price`. Un produs epuizat (`in_stock` = 0) poate rămâne în listă cu un preț vechi: la evoMAG un laptop
a stat din 18.10 până pe 5.11.2026 „stoc epuizat” la 10.999,99 lei și a revenit în stoc la 15.399,99. La CEL stocul se
încarcă prin JavaScript, deci `stock_text` / `in_stock` rămân goale. Pentru a vedea ce se extrage dintr-un titlu:

```bash
python extraction/specs.py "Laptop ASUS Vivobook 16 (Procesor AMD Ryzen 7 170, 16 inch WUXGA, 16GB, 512GB SSD)"
```

**Ruptură în datele eMAG (30–31.10.2026).** ANABLUE COMPUTERS, vânzătorul din marketplace cu ~75% din laptopurile
eMAG de 10.000–20.000 lei (configurații cu RAM sau SSD mărite, prețuri fără bani, coduri inventate), și-a scos
aproape toate ofertele din listă: de la 4.849 de produse pe 30.10 la 1.289 pe 31.10. Paginile verificate ale
produselor există în continuare, dar fără stoc. Ofertele celorlalți vânzători au rămas (produsele cu bani:
1.106 → 1.126). Numărul de produse, prețul median sau procentul de reduceri ale eMAG se schimbă deci din compoziție,
nu din prețuri: compară prețurile pe produs, nu mediile zilnice, și tratează separat ofertele ANABLUE. Din 31.10
le arată exact coloana `seller` din CSV-urile zilnice, iar înainte prețul fără bani.

**Revenirea ANABLUE (7.11.2026).** A doua zi după Black Friday, ANABLUE COMPUTERS și-a pus ofertele înapoi în listă:
lista eMAG de 5.000–25.000 lei a crescut de la 3.837 de produse (6.11) la 10.634, iar pe 7.11 6.648 din cele 10.444
de produse salvate erau ANABLUE (64%, toate cu prețuri întregi). 3.881 dintre ele sunt produse văzute deja în
octombrie. Fără ANABLUE, lista a rămas aproape la fel (3.796 de produse, față de 3.835 pe 6.11). Ca și la ruptura din
30–31.10, numărul de produse și mediile zilnice ale eMAG se schimbă din compoziție; ofertele ANABLUE se recunosc
după coloana `seller`.

**Intervalul de preț (schimbat pe 6.11.2026).** Studiul privește laptopurile de 10.000–20.000 lei. Până pe
5.11.2026 crawlerul păstra doar acest interval, așa că un laptop ieșea din date exact când prețul trecea de margini:
- **Sub 10.000 lei, când era redus.** De Black Friday (6.11.2026), în rularea de la 13:05, 152 de produse
  dispăruseră din lista eMAG, iar 88 dintre ele costau cu o zi înainte 10.000–11.000 lei.
- **Peste 20.000 lei, de obicei când oferta eMAG s-a epuizat** și lista a arătat oferta mai scumpă a altui vânzător
  (de exemplu un HP OMEN 16 vândut de eMAG cu 16.999,99 lei a apărut la 20.086 lei).

Din 6.11.2026 crawlerul colectează 5.000–25.000 lei, ca un laptop din interval să fie urmărit și când e redus cu până
la 50% sau se scumpește cu până la 25%. Rularea din 6.11 seara (22:39) a regăsit 139 dintre cele 152 de produse
dispărute (121 sub 10.000 lei, cu o reducere mediană de 18% față de 5.11 și maximum 58%). A găsit și produse din
studiu în afara vechiului interval: 28 sub 7.000 lei (reduse cu 32–69% față de mediana lor) și 36 peste 20.000 lei
(eMAG 27, evoMAG 7, CEL 2). La evoMAG și CEL schimbarea nu adaugă nicio cerere, fiindcă crawlerul parcurgea deja
toată categoria; la eMAG lista are mai multe pagini (64 la 5.000–25.000 lei, față de 22 la 10.000–20.000).
- Datele zilnice de până pe 5.11.2026 au 10.000–20.000 lei, iar cele din 6.11.2026 au 5.000–25.000. Intervalul fiecărei
  rulări e în `price_range`, în `<magazin>_log.json`. Pentru 6.11.2026 sunt salvate datele rulării de la 22:39.
  Rulările anterioare din aceeași zi au rămas doar în copii locale, `data/debug/backup_2026-11-06_<ora>/`: 13:05
  (10.000–20.000 lei), 20:18 (7.000–20.000) și 21:16 (7.000–25.000).
- În `products.csv`, `in_price_range` = `True` marchează populația studiului: produsele cu prețul median între
  10.000 și 20.000 lei. Produsele cu prețul median în afara intervalului (`False`) se lasă deoparte la modelul de preț
  sau se analizează separat. Mediana se recalculează la fiecare rulare a `build_dataset.py`, deci un produs văzut doar
  câteva zile își poate schimba încadrarea pe măsură ce se adună zilele.
- Ca să compari numărul de produse între zile, numără doar prețurile între 10.000 și 20.000 lei: altfel apare un
  salt fals pe 6.11.2026.

**Prețurile de Black Friday epuizate (6.11.2026).** De Black Friday, eMAG a vândut unele laptopuri la un preț special,
în stoc limitat, și multe s-au epuizat în primele ore, înainte de rularea crawlerului. Lista arăta apoi oferta altui
vânzător sau prețul obișnuit. De exemplu, Lenovo Legion Pro 7 16AFR10H a costat 14.999,99 lei la deschidere, s-a
epuizat, iar la 13:05 lista arăta 18.130,99 lei la PC Garage. Pagina produsului păstrează prețul epuizat într-un
banner („Ne pare rău. Ai ajuns puțin prea târziu...”) și în datele paginii (`banners`, tipul `bf_out_of_stock`).
`black_friday.py` deschide o singură dată pagina fiecărui produs vândut de eMAG în listă și scrie
`data/raw/black_friday.csv`. Ordinea ține de șansa unui banner: întâi produsele vândute de eMAG înainte de Black
Friday și apoi trecute la alt vânzător, scoase din listă sau epuizate. Coloana `bf_price` e goală dacă pagina nu are
banner, iar `page_price`, `page_seller` și `page_stock` descriu oferta din pagină la verificare. Ofertele vânzătorilor
din marketplace nu au banner, deci prețurile lor de Black Friday epuizate înainte de colectare nu se pot recupera.
- Pe 6.11.2026 seara, 44 din cele 67 de produse cele mai probabile aveau bannerul. La 19 dintre ele prețul de Black
  Friday era deja în listă (oferta epuizată a eMAG rămăsese afișată), iar la 25 lista arăta un preț mai mare
  (reducere mediană de 11,8% față de 5.11). La 10 produse vândute de eMAG la același preț ca înainte nu a apărut
  niciun banner.
- În noaptea de 6–7.11 au fost verificate toate produsele vândute de eMAG (892 de pagini), mai puțin 80 deja reduse
  în listă. 81 aveau banner: la 55 prețul de Black Friday era deja în listă, iar 26 sunt recuperate doar din banner
  (reducere mediană de 11,9% față de 5.11). Bannerul a apărut la:
  - 49 din 62 de produse vândute de eMAG pe 5.11 și apoi epuizate, trecute la alt vânzător sau scoase din listă;
  - 30 din 532 de produse vândute de eMAG, dar noi în date;
  - 0 din 194 de produse cu același preț ca înainte;
  - 2 din 104 produse deja reduse în listă.
- Bannerul a dispărut pe 7.11.2026, la mentenanța eMAG de la ~03:00: rularea de noapte, până la 03:08, l-a mai
  găsit, iar dimineața produsul din exemplul de mai sus nu-l mai avea. Ultimele 109 pagini, verificate pe 7.11 după
  ora 08:00 (cele 80 rămase și 29 de produse noi), nu mai puteau arăta bannerul: la ele `bf_price` gol nu înseamnă
  că n-a existat o ofertă de Black Friday. În total, 1.001 produse vândute de eMAG verificate și 81 de prețuri găsite.
- `build_dataset.py` pune prețul în coloana `bf_price` din `products.csv`. CSV-urile zilnice rămân prețurile din
  listă. În analiza momentului de cumpărare, `bf_price` e prețul de Black Friday oferit de eMAG pentru acel produs,
  chiar dacă s-a epuizat înainte de colectare.

### Opțiunile crawlerului (`crawl.py`)

| Opțiune | Efect |
| --- | --- |
| `--shops emag cel` | Rulează doar magazinele alese (`emag`, `evomag`, `cel`). |
| `--min-price 8000 --max-price 22000` | Schimbă intervalul de preț (implicit 5000–25000 lei; până pe 5.11.2026, 10000–20000). |
| `--max-pages 5` | Limitează numărul de pagini per magazin. |
| `--delay 5 10` | Pauza dintre pagini, între 5 și 10 secunde. |
| `--no-csv` | Nu salvează CSV-ul zilnic în `data/raw/<data>/` (implicit se salvează). |
| `--max-part-numbers 200` | Câte pagini de produs deschide la final pentru part number (implicit 300; `0` = deloc). |
| `--db alt.db` | Scrie în alt fișier SQLite. |
| `--browser` | Folosește un Chromium automat. Util doar dacă un magazin încarcă produsele cu JavaScript (eMAG, evoMAG și CEL nu au nevoie). Cere `pip install playwright` și `playwright install chromium`. |
| `--show-browser` | Arată fereastra browserului (doar cu `--browser`). |

## Rulare automată în fiecare zi

Pentru un istoric de prețuri fără goluri, programează rularea.

**Windows** (Task Scheduler): rulează o singură dată în PowerShell, din folderul proiectului:

```powershell
$cmd = "/c .venv\Scripts\python.exe extraction\crawl.py >> data\crawl.log 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory (Get-Location).Path
$trigger = New-ScheduledTaskTrigger -Daily -At 9am
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable   # dacă la 9:00 era oprit, rulează la pornire
Register-ScheduledTask -TaskName "laptop-prices" -Action $action -Trigger $trigger -Settings $settings
```

**macOS sau Linux** (`cron`):

```bash
crontab -e    # deschide lista de sarcini programate
```

Adaugă linia (înlocuiește căile cu ale tale):

```
0 9 * * * cd /calea/spre/laptop-prices && .venv/bin/python extraction/crawl.py >> data/crawl.log 2>&1
```

Ambele variante rulează crawlerul în fiecare zi la 9:00 și scriu mesajele în `data/crawl.log`.
Cu `cron`, un calculator oprit sau în repaus la ora respectivă sare peste rulare. Verifică din când în când
tabelul `crawl_runs` (vezi mai jos) ca să vezi dacă există zile lipsă.

Programează rularea pe un singur calculator, ca să nu apară conflicte în `data/raw/`, și urcă periodic
pe GitHub CSV-urile noi (și `products.csv`, dacă l-ai refăcut cu `build_dataset.py`):

```bash
git add data/raw data/processed
git commit -m "Prețuri noi"
git push
```

## Baza de date

Fișierul local `data/laptops.db` are două tabele. Aceleași date se scriu zilnic și în `data/raw/<data>/`:
`<magazin>.csv.gz` (coloanele tabelului `prices`) și `<magazin>_log.json` (jurnalul rulării).
Pe GitHub se publică doar aceste fișiere.

**`prices`**: un rând pentru fiecare zi, magazin și produs. Cheia primară este `(date, shop, product_id)`.

| Coloană | Conținut |
| --- | --- |
| `date`, `scraped_at` | Ziua și ora colectării |
| `shop`, `product_id`, `title`, `url` | Magazinul, identificatorul, titlul și linkul produsului |
| `price` | Prețul curent, în lei |
| `old_price` | Prețul tăiat, dinaintea reducerii |
| `rrp` | Prețul recomandat de producător (PRP) |
| `lowest_30d` | Cel mai mic preț din ultimele 30 de zile (afișat de magazin la reduceri) |
| `rating`, `n_reviews` | Nota medie și numărul de recenzii |
| `stock_text`, `in_stock` | Textul despre stoc; 1 = în stoc, 0 = indisponibil, gol = necunoscut |
| `seller` | Vânzătorul ofertei afișate în listă: la eMAG `eMAG` sau vânzătorul din marketplace (din 31.10.2026); gol la evoMAG și CEL, care nu au marketplace |
| `page`, `rank`, `parse_method` | Pagina, poziția în listă și metoda de extragere (`selector` sau `generic`) |

**`crawl_runs`**: un rând pentru fiecare zi și magazin, cu paginile parcurse, produsele găsite și păstrate, și motivul opririi.

Reguli de scriere:
- A doua rulare în aceeași zi **înlocuiește** datele zilei pentru magazinul respectiv, dacă e completă.
- O rulare oprită la jumătate (blocare, eroare, Ctrl+C, limita de pagini) **nu înlocuiește** o rulare mai completă
  din aceeași zi, iar o rulare fără niciun produs nu șterge nimic.
- O redirecționare spre o pagină de verificare anti-bot (alt site, ca `captcha.distinct.ro` la evoMAG, sau
  „captcha” în adresă) e tratată ca blocare, nu ca sfârșit al listei.
- O rulare „completă” cu prea puține produse e tratată ca incompletă: de obicei e o pagină de blocare
  nerecunoscută. La eMAG se compară cu numărul de produse afișat de eMAG pe pagina 1 a listei („1 - 60 din 1290
  de produse”, păstrat în jurnal ca `listed`): sub 95% din el, rularea e incompletă. La evoMAG și CEL se compară
  cu mediana ultimelor 7 zile (același interval de preț; după o schimbare a intervalului, ca pe 6.11.2026,
  comparația începe a doua zi): sub jumătate, rularea e incompletă. Mediana nu mai e
  folosită la eMAG fiindcă lista se poate schimba cu adevărat: pe 31.10.2026 a scăzut de la ~4.850 la 1.290 de
  produse, iar mediana ar fi semnalat ca incomplete rulări corecte.
- CSV-urile și jurnalele se scriu printr-un fișier temporar, deci o oprire în timpul scrierii nu lasă un fișier
  stricat.

### Interogări utile

Se pot rula cu `sqlite3 data/laptops.db` sau dintr-un notebook cu `pandas.read_sql_query`.

```sql
-- câte produse și ce interval de preț are fiecare magazin
SELECT shop, COUNT(*) AS rânduri, COUNT(DISTINCT product_id) AS produse,
       MIN(price) AS minim, MAX(price) AS maxim
FROM prices GROUP BY shop;

-- cum a mers fiecare rulare
SELECT date, shop, pages, seen_total, products, stopped_because FROM crawl_runs ORDER BY date DESC;

-- evoluția prețului unui produs
SELECT date, price, old_price FROM prices
WHERE shop = 'emag' AND product_id = 'ID_PRODUS' ORDER BY date;

-- produsele cu cea mai mare scădere de preț între prima și ultima zi colectată
SELECT a.shop, a.product_id, a.title, a.price AS preț_inițial, b.price AS preț_curent,
       ROUND(100.0 * (b.price - a.price) / a.price, 1) AS variație_procent
FROM prices a
JOIN prices b ON a.shop = b.shop AND a.product_id = b.product_id
WHERE a.date = (SELECT MIN(date) FROM prices) AND b.date = (SELECT MAX(date) FROM prices)
ORDER BY variație_procent;
```

```python
import glob, sqlite3, pandas as pd
# din baza locală
df = pd.read_sql_query("SELECT * FROM prices", sqlite3.connect("data/laptops.db"))
# din CSV-urile publicate în depozit (de exemplu după git clone, fără baza locală);
# product_id ca text, pentru că la evoMAG e numeric, iar la eMAG și CEL conține litere
df = pd.concat(pd.read_csv(f, dtype={"product_id": str}) for f in sorted(glob.glob("data/raw/*/*.csv.gz")))
# part number-ul fiecărui produs: prețurile aceluiași laptop în toate magazinele și zilele
pn = pd.read_csv("data/raw/part_numbers.csv", dtype=str)
df = df.merge(pn[["shop", "product_id", "part_number"]], on=["shop", "product_id"], how="left")
```

## Cum funcționează

1. Pentru fiecare magazin se deschide pagina categoriei „laptopuri”, apoi paginile următoare.
2. Din fiecare pagină se extrag produsele. Mai întâi se încearcă selectorii CSS ai magazinului (`parse_method = selector`).
   Dacă aceștia nu găsesc niciun produs cu preț, se trece automat la metoda `generic`, care caută linkuri de
   produs după forma URL-ului și citește textul din jur. Astfel crawlerul rezistă și dacă un magazin își schimbă designul.
3. Se păstrează doar produsele cu prețul afișat în interval. La eMAG se cere direct lista filtrată după preț
   (`pret,intre-5000-si-25000`; până pe 5.11.2026 `pret,intre-10000-si-20000`), sortată de la cel mai scump la cel
   mai ieftin. La 10.000–20.000 lei lista a avut 78–89 de pagini până pe 30.10.2026 și 22 de atunci, iar la
   5.000–25.000 lei 64 de pagini (6.11.2026), apoi 180 după revenirea ANABLUE (7.11.2026). Din datele produselor din
   pagină se citește și vânzătorul fiecărei oferte.
   eMAG arată cel mult 100 de pagini dintr-o listă. Dacă pagina 1 arată mai multe produse decât încap în 100 de pagini,
   crawlerul cere imediat lista din nou ca două liste (jumătatea scumpă și cea ieftină a intervalului), fiecare
   parcursă până la capăt și împărțită la rândul ei dacă e nevoie. Pe 7.11.2026: 10.634 de produse, adică trei liste
   (15.000–25.000, 10.000–15.000 și 5.000–10.000 lei) și 180 de pagini în total. În consolă, fiecare parte a unei liste
   împărțite își numără paginile de la 1 și are intervalul în față (`[emag 15,000-25,000] pagina 1`).
   Filtrul eMAG folosește uneori alt preț decât cel afișat, mai mare sau mai mic, așa că câteva produse de la
   marginile intervalului pot lipsi sau apar cu prețul afișat în afara lui (sunt ignorate). Pe 6.11.2026 a fost un
   singur astfel de produs la marginea de jos și niciunul la cea de sus. La un „Pachet promo” (laptop și accesoriu)
   cardul arată prețul pachetului, dar se păstrează prețul laptopului, din datele cardului.
4. Rezultatul se scrie în CSV-urile zilnice din `data/raw/` și în baza SQLite locală.
5. Pentru produsele noi se deschide pagina produsului, o singură dată, și se citește part number-ul: la CEL din
   datele structurate ale paginii (`mpn`), la evoMAG din codul produsului, la eMAG din `mpn`. Codurile puse de
   vânzătorii din marketplace se păstrează doar dacă au forma part number-ului producătorului (de exemplu
   10 caractere la Lenovo: `83F50047BM`); codul brut rămâne în coloana `code` din `part_numbers.csv`. La Dell,
   magazinele folosesc codul configurației (`BTO108_PC16250_EMEA`). Part number-ul e păstrat fără semne
   (`BTO108PC16250EMEA`), ca să fie identic la CEL, evoMAG și eMAG.
   Cererile alternează între magazine, iar un magazin care cere captcha e sărit până la rularea următoare.
6. `build_dataset.py` extrage specificațiile din titluri, adaugă part number-ul și scorurile de performanță de
   pe Notebookcheck și face setul final `data/processed/products.csv`.

**Politețe:** o singură cerere odată, pauze aleatoare între pagini, respectarea `robots.txt`, oprire imediată
la `403`, `429` sau captcha. Nu încerca să ocolești blocajele.

## Dacă un magazin nu mai merge

Magazinele își schimbă din când în când structura paginilor. Configurația din `extraction/shops.py`
a fost verificată pe 18.10.2026. Dacă un magazin nu mai dă produse:

1. Rulează `python extraction/crawl.py --test --shops <magazin>` și deschide `data/debug/<magazin>_p1.html`.
2. Dacă primești 404, corectează `start_url`.
3. Dacă pagina se deschide, dar nu găsește produse, corectează `product_re` (forma linkului unui produs,
   cu ID-ul produsului în grupul 1) sau selectorii (`card_selector`, `title_selector`, `price_selector`).
4. Dacă pagina e goală (produsele se încarcă cu JavaScript), încearcă `--browser`. Dacă apare un captcha,
   oprește-te: magazinul nu permite accesul automat.
5. Repetă testul până când exemplele afișate au titlul și prețul de pe site.

## Date și etică

Datele sunt colectate de pe pagini publice, cu ritm redus, doar pentru un proiect academic. Pagina fiecărui
produs se deschide o singură dată (pentru part number), cu aceleași pauze și aceeași oprire la blocare.
Depozitul publică doar datele extrase de crawler (prețuri și informații publice despre produse);
paginile HTML ale magazinelor (`data/debug/`, `pagini_magazine/`) rămân locale.
Listele de benchmark de pe Notebookcheck (`data/external/`) rămân și ele locale: în `products.csv` intră doar
scorurile potrivite fiecărui laptop, iar sursa se citează în raport.
Verifică termenii de utilizare ai magazinelor.

## Pași următori

- comparațiile de preț între magazine pe baza `part_number` și împărțirea datelor la antrenare pe grupuri
  (același part number doar în setul de antrenare sau doar în cel de test)
- `analysis/`: modele de estimare a prețului (Ridge, Random Forest, CatBoost) și model pentru momentul cumpărării
- `docs/`: prezentarea pentru Faza 1 și raportul final
