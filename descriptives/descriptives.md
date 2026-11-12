
## Tabelele (`descriptives/tables/`)

### T1: `t1_overview.csv`, cifrele generale

**Ce este:** dimensiunea setului de date.

| Rând                            | Valoare | Înseamnă                                                          |
| -------------------------------- | ------- | ------------------------------------------------------------------- |
| zile colectate                   | 18      | câte zile diferite au date                                         |
| prețuri zilnice (rânduri)      | 93.672  | toate observațiile: un produs văzut într-o zi = un rând         |
| produse (magazin, produs)        | 14.033  | produse unice                                                       |
| în studiu                       | 8.086   | produsele cu prețul median între 10.000 și 20.000 lei            |
| din care ANABLUE                 | 5.149   | 64% din studiu, configurații modificate ale unui singur vânzător |
| cu part number                   | 2.222   | produse care pot fi recunoscute în alte magazine                   |
| part number-uri în ≥2 magazine | 505     | laptopuri care pot fi comparate direct între magazine              |
| cu `bf_price`                  | 55      | prețuri de Black Friday recuperate din pagina produsului           |

**De spus în prezentare:** volumul (~94.000 de observații), dar și că mai mult de jumătate din studiu e un singur vânzător.

### T2: `t2_price_by_shop.csv`, prețul pe magazin

**Ce este:** statistici ale prețului median, pe 4 grupuri.

**Coloanele:**

- `count`: numărul de produse;
- `mean`: media;
- `std`: abaterea standard, adică cât de împrăștiate sunt prețurile;
- `min`, `max`: extremele;
- `25%`, `50%`, `75%`: cuartilele. De exemplu, 25% din produse costă sub valoarea din coloana `25%`; `50%` e mediana.

**Ce arată:**

- **Medianele sunt foarte apropiate:** CEL 13.302, eMAG 13.089, evoMAG 13.000, ANABLUE 12.644 lei.
- **Împrăștierea e similară:** `std` între 2.600 și 3.000 lei.
- **Concluzia:** magazinele vând laptopuri cam din aceeași gamă de preț. ANABLUE e puțin mai ieftin, cu o diferență care se vede abia la cuartila de sus (14.883 vs ~15.700 lei).

### T3: `t3_spearman.csv`, ce specificații merg împreună cu prețul

**Ce este:** corelația Spearman dintre fiecare specificație numerică și preț, calculată fără ANABLUE.

**Cum se citește:**

- **Scala:** între −1 și 1.
- **0:** nicio legătură.
- **1:** „cu cât e mai mare, cu atât e mai scump”, perfect.
- **De ce Spearman:** compară ordinea valorilor, nu valorile în sine, deci nu presupune o relație liniară.

**Ce arată:**

- **Cele mai legate de preț,** cu corelații moderate (0,30–0,35): RAM-ul, numărul de nuclee și performanța procesorului.
- **Urmează** stocarea și performanța plăcii video (0,25–0,29).
- **Diagonala ecranului aproape nu contează** (0,05).

**De spus în prezentare:** nicio specificație nu explică singură prețul, deci e nevoie de un model care le combină. Ăsta e argumentul pentru Random Forest și CatBoost.

---

## Graficele (`descriptives/figures/`)

### F1: `f1_daily_counts.png`, câte produse s-au colectat în fiecare zi

**Ce este:** o linie pe grup (eMAG, eMAG ANABLUE, evoMAG, CEL). Pe axa X e ziua, pe Y numărul de produse între 10.000 și 20.000 lei. Linia punctată marchează Black Friday.

**La ce să te uiți:**

- **Linia ANABLUE:** cade brusc pe 31.10 și urcă la loc pe 7.11. E ruptura din date.
- **Celelalte linii:** sunt aproape plate. Colectarea a fost stabilă, iar ruptura vine doar de la ANABLUE.

**De ce contează:** arată că o medie zilnică pe tot eMAG ar fi înșelătoare și justifică de ce ANABLUE e tratat separat.

### F2: `f2_price_by_shop.png`, distribuția prețului pe magazin

**Ce este:** un boxplot pentru fiecare grup, cu numărul de produse sub nume. E varianta vizuală a tabelului T2.

**Cum se citește boxplotul:**

- **cutia:** jumătatea din mijloc a produselor, de la cuartila 25% la 75%;
- **linia din cutie:** mediana;
- **„mustățile”:** restul valorilor obișnuite;
- **punctele de dincolo de mustăți:** valori neobișnuite.

**Ce arată:** cutii aproape la aceeași înălțime, deci magazine cu prețuri similare în ansamblu.

### F3: `f3_completeness.png`, cât de complete sunt specificațiile

**Ce este:** câte o bară orizontală pentru fiecare specificație, cu procentul de produse la care s-a putut extrage din titlu.

**Ce arată:**

- **Specificațiile de bază** (brand, RAM, stocare, procesor) sunt aproape complete.
- **Altele lipsesc des:** tipul panoului (~55%) și frecvența ecranului (~12%).

**De ce contează:** e slide-ul despre calitatea datelor. Spune ce variabile se pot folosi direct în model și care au nevoie de o categorie „necunoscut” sau trebuie scoase.

### F4: `f4_price_vs_specs.png`, explică specificațiile prețul?

**Două grafice, fără ANABLUE:**

- **Stânga, prețul după RAM (8, 16, 24, 32, 64 GB):** un boxplot pentru fiecare valoare. Dacă mediana urcă de la stânga la dreapta, mai mult RAM înseamnă preț mai mare.
- **Dreapta, prețul după performanța procesorului:** fiecare punct e un laptop. Pe X e scorul Notebookcheck al procesorului, pe Y prețul. Laptopurile cu placă integrată și cele cu placă dedicată au culori diferite.

**La ce să te uiți:**

- **Tendința:** o urcare spre dreapta înseamnă că procesoarele mai puternice costă mai mult.
- **Împrăștierea:** multă împrăștiere înseamnă că procesorul singur nu explică prețul, la fel ca în T3.
- **Placa dedicată:** dacă punctele ei stau mai sus, placa video adaugă la preț independent de procesor.

### F5: `f5_discounts.png`, reducerile și Black Friday

**Două grafice, fără ANABLUE, doar produsele din listă pe 5.11:**

- **Stânga, procentul de produse reduse cu cel puțin 5% față de prețul lor obișnuit, pe zile.** „Prețul obișnuit” = mediana prețurilor produsului dinainte de 6.11. Pe 6.11 ar trebui să apară un vârf. Înălțimea lui spune câte produse au fost reduse de Black Friday, pe fiecare magazin.
- **Dreapta, histograma reducerilor de Black Friday.** Pentru fiecare produs redus: cu cât a scăzut prețul pe 6.11 față de 5.11. Se folosește cel mai mic dintre prețul din listă și `bf_price`, ca să fie incluse și ofertele epuizate. În titlu apare mediana reducerii.

**De ce contează:** e baza analizei „când merită să cumperi”. Arată cât de des scad prețurile și cât de mari sunt reducerile.

### F6: `f6_cross_shop.png`, același laptop în magazine diferite

**Două grafice, doar pentru cele 505 laptopuri găsite în cel puțin două magazine:**

- **Stânga, histograma diferenței de preț.** Pentru fiecare laptop: cu câte procente e mai scump în magazinul cel mai scump față de cel mai ieftin. Bara de lângă 0% înseamnă același preț peste tot. Valorile mari înseamnă că merită să compari magazinele.
- **Dreapta, de câte ori fiecare magazin e cel mai ieftin.**

**De ce contează:** răspunde direct la întrebarea „de unde să cumpăr”. Merge pe slide-ul cu analizele planificate.
