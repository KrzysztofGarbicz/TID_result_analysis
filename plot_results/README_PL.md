# radiation_plot - Instrukcja użytkownika

Skrypt do generowania wykresów PNG z wyników testów radiacyjnych. Konfiguracja oparta jest na plikach YAML, co umożliwia łatwe definiowanie wielu wykresów bez potrzeby modyfikacji kodu.

> **GUI do klikania konfiguracji**: `python plot_builder.py` otwiera interaktywne okno z formularzem (wszystkie pola), podglądem na żywo i przyciskami "Generate YAML" / "Copy to clipboard". Patrz sekcja **[Tryb GUI](#tryb-gui---plot_builderpy)** poniżej.

## Wymagania

```
python >= 3.10
pandas
numpy
matplotlib
pyyaml
```

Instalacja w środowisku wirtualnym:

```bash
pip install pandas numpy matplotlib pyyaml
```

## Szybki start

### 1. Przygotowanie danych

Uruchom skrypt `make_flat_files.py` aby wygenerować pliki `*_flat.csv` dla każdego urządzenia.

### 2. Konfiguracja mapowania dawek

Edytuj plik `examples/dose_map.yaml` aby zmapować numery seryjne (SN) na dawki promieniowania:

```yaml
reference_sns:
  - SN001
  - SN002

lots:
  A:
    - SN001
    - SN002
  B:
    - SN003
    - SN004

bias_groups:
  bias:
    - SN001
    - SN003
  unbias:
    - SN002
    - SN004

dose_map:
  SN001:
    dose: 0
    lot: A
    bias: bias
  SN002:
    dose: 10
    lot: A
    bias: unbias
  # ... itp.
```

### 3. Konfiguracja wykresów

Edytuj plik `examples/plot_config.yaml`:
- Ustaw `data.flat_files_dir` na katalog z plikami `*_flat.csv` z kroku 1
- Dodaj/modyfikuj wpisy w sekcji `plots` aby zdefiniować wykresy do wygenerowania

### 4. Generowanie wykresów

```bash
python plot_radiation.py --config examples/plot_config.yaml
```

## Opcje wiersza poleceń

### Podstawowe użycie
```bash
python plot_radiation.py --config <ścieżka_do_pliku_konfigu>
```

### Dostępne flagi

| Flaga | Opis |
|-------|------|
| `--config PATH` | **Wymagane** - ścieżka do pliku `plot_config.yaml` |
| `--dry-run` | Walidacja konfiguracji i wyświetlenie liczby wierszy dla każdego wykresu bez renderowania |
| `--only NAME[,...]` | Renderuj tylko wskazane wykresy (przecinkami oddzielone nazwy). Przydatne przy testowaniu |
| `--verbose` | Włącz debug-level logging |

### Przykłady

Renderuj tylko jeden wykres:
```bash
python plot_radiation.py --config examples/plot_config.yaml --only TPS2553_iq_absolute_stats
```

Waliduj konfigurację bez generowania:
```bash
python plot_radiation.py --config examples/plot_config.yaml --dry-run
```

Renderuj wiele wybranych wykresów z logowaniem:
```bash
python plot_radiation.py --config examples/plot_config.yaml --only plot1,plot2 --verbose
```

## Struktura pliku plot_config.yaml

Plik konfiguracyjny zawiera 3 główne sekcje:

### 1. Sekcja `data` - Ścieżki wejścia/wyjścia

```yaml
data:
  flat_files_dir: "ścieżka/do/plików/flat_csv"  # Katalog z plikami *_flat.csv
  dose_map: "./dose_map.yaml"                    # Plik mapowania dawek
  output_dir: "./plots"                          # Katalog dla generowanych PNG
```

**Notatka:** Ścieżki można definiować jako względne - będą względne do lokalizacji pliku konfiguracyjnego.

### 2. Sekcja `defaults` - Domyślne ustawienia dla wszystkich wykresów

Każdy wykres dziedziczy te ustawienia i może je przesłonić:

#### Rozmiar i format

```yaml
defaults:
  figsize: [10, 6]      # Szerokość i wysokość wykresu [cale]
  dpi: 150              # Rozdzielczość (dots per inch)
  format: png           # Format wyjściowy (png, jpg, pdf, itp.)
```

#### Wyświetlanie danych

```yaml
  show_points: true     # Wyświetlaj punkty danych
  show_lines:           # Linie statystyczne do wyświetlenia
    - min               # Minimum
    - max               # Maksimum
    - mean              # Średnia
  marker_size: 40       # Rozmiar markerów (punktów)
  alpha_points: 0.65    # Przezroczystość punktów (0.0-1.0)
  line_width: 1.6       # Grubość linii
```

#### Wygląd wykresu

```yaml
  grid: true            # Wyświetlaj siatkę
  legend: true          # Wyświetlaj legendę
  x_label: "Dose [kRad]"  # Etykieta osi X
```

#### Mapowanie etykiet etapów pomiarów

```yaml
  stage_labels:         # Jak wyświetlane są etapy w legendzie
    before_irradiate: "Before"
    after_irradiate: "After"
    annealing_24h_25c: "24h @ 25 C"
    annealing_168h_25c: "168h @ 25 C"
    annealing_168h_100c: "168h @ 100 C"
```

#### Style kolorów i markerów dla etapów

```yaml
  stage_styles:         # Kolor i marker dla każdego etapu
    before_irradiate:    { color: "#1f77b4", marker: "o" }
    after_irradiate:     { color: "#d62728", marker: "^" }
    annealing_24h_25c:   { color: "#2ca02c", marker: "s" }
    annealing_168h_25c:  { color: "#9467bd", marker: "D" }
    annealing_168h_100c: { color: "#ff7f0e", marker: "v" }
```

**Dostępne markery:** `o` (koło), `s` (kwadrat), `^` (trójkąt w górę), `v` (trójkąt w dół), `D` (diament), `*` (gwiazda), `+` (plus), `x` (krzyż), itp.

### 3. Sekcja `plots` - Definicje wykresów

Każdy wpis to jeden wykres do wygenerowania. Wzór:

```yaml
plots:
  - name: "nazwa_wykresu"              # Wymagane - używane w nazwie pliku PNG
    type: "absolute"                   # Wymagane - typ wykresu (absolute/delta/annealing)
    title: "Tytuł wykresu"             # Wyświetlany na górze wykresu
    lcl_name: "TPS2553"                # Wymagane - nazwa urządzenia
    measurement_type: "iquiescent"     # Wymagane - typ pomiaru
    metric: "iq_ua"                    # Wymagane - nazwa metryki
    # ... dodatkowe opcje ...
```

## Opcje dla wszystkich typów wykresów

| Opcja | Typ | Opis |
|-------|-----|------|
| `name` | string | **Wymagane** - identyfikator wykresu, używany w nazwie PNG |
| `type` | string | **Wymagane** - `absolute`, `delta` lub `annealing` |
| `title` | string | Tytuł wykresu (wyświetlony na górze) |
| `lcl_name` | string | **Wymagane** - nazwa urządzenia/czipa (np. TPS2553) |
| `measurement_type` | string | **Wymagane** - typ pomiaru (np. iquiescent, rdson) |
| `metric` | string | **Wymagane** - nazwa metryki (np. iq_ua, rdson_mohm) |
| `context_key` | string | Filtr kontekstu - **wymagane jeśli metryka istnieje w kilku wariantach** (np. iload_a=1.0) |
| `y_label` | string | Etykieta osi Y |
| `x_lim` | [min, max] | Limity osi X (np. [0, 50]) |
| `y_lim` | [min, max] | Limity osi Y (np. [0, 100]) |
| `x_scale` | string | Skala osi X: `linear` (domyślnie), `log` lub `symlog` |
| `y_scale` | string | Skala osi Y: `linear` (domyślnie), `log` lub `symlog` |

## Filtry dostępne dla wszystkich typów

| Opcja | Opis |
|-------|------|
| `exclude_sn` | Lista numerów seryjnych do wyłączenia (np. [SN001, SN002]) |
| `include_doses` | Zachowaj tylko te dawki w kRad (np. [10, 20, 30]) |
| `exclude_doses` | Wyłącz te dawki (np. [0, 5]) |
| `lot` | Zachowaj próbki tylko z wskazanego lotu (np. A lub B) |
| `bias` | Zachowaj próbki: `bias` lub `unbias` |
| `split_by` | Wymiary podziału na **osobne pliki PNG** (`lot`, `bias`, `[lot, bias]`) |
| `series_by` | Wymiary podziału na **wiele serii w jednym PNG** (`lot`, `bias`, `[lot, bias]`) |
| `subplots` | Lista paneli — każdy panel to osobne osie w jednym PNG (tylko `absolute`/`delta`) |
| `subplot_layout` | `rows` (domyślnie), `cols`, `grid` |
| `share_x` / `share_y` | Czy panele dzielą osie X/Y |
| `show_before_at_zero` | (tylko `absolute`) rysuj wartości `before_irradiate` przy x=0 kRad |

## Typy wykresów

### Typ: `absolute`

Surowa wartość pomiaru vs dawka promieniowania. Dla każdego etapu (`stages`) osobna seria kolorów.

**Wymagane opcje:**
```yaml
- name: "nazwa"
  type: absolute
  lcl_name: "TPS2553"
  measurement_type: "iquiescent"
  metric: "iq_ua"
  stages: [before_irradiate, after_irradiate]  # Etapy do wykreślenia
```

**Specjalnie dla absolute:**
- Próbki referencyjne są automatycznie wyłączane
- Każdy etap ma swój kolor i marker zdefiniowany w `stage_styles`

**Przykład:**
```yaml
- name: "TPS2553_iq_absolute_stats"
  type: absolute
  title: "TPS2553 - Prąd spoczynkowy vs dawka"
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  stages: [before_irradiate, after_irradiate]
  y_label: "I_Q [uA]"
  y_scale: log
```

### Typ: `delta`

Zmiana wartości między dwoma etapami (`delta_from` i `delta_to`), wykreślona vs dawka. **Zawsze renderowana jako względna zmiana procentowa**.

**Wymagane opcje:**
```yaml
- name: "nazwa"
  type: delta
  lcl_name: "TPS2553"
  measurement_type: "iquiescent"
  metric: "iq_ua"
  delta_from: before_irradiate    # Etap początkowy
  delta_to: after_irradiate       # Etap końcowy
```

**Szczególności:**
- Tylko próbki mające pomiary w OBU etapach są uwzględniane
- Automatycznie rysowana jest linia zerowa (brak zmiany)
- Próbki referencyjne są wyłączane
- Pole `delta_mode` jest ignorowane - zawsze stosowana jest zmiana procentowa

**Przykład:**
```yaml
- name: "TPS2553_iq_delta"
  type: delta
  title: "TPS2553 - Delta I_Q (po - przed) [%]"
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  delta_from: before_irradiate
  delta_to: after_irradiate
  y_label: "Zmiana [%]"
```

### Typ: `annealing`

Trend zmian przez uporządkowaną listę etapów, osobna linia dla każdej grupy dawek. Zawiera panel z próbkami referencyjnymi (jeśli są zdefiniowane).

**Wymagane opcje:**
```yaml
- name: "nazwa"
  type: annealing
  lcl_name: "TPS25963"
  measurement_type: "iquiescent"
  metric: "iq_ua"
  stages_order:  # Kolejność etapów na osi X (kategorie, nie liczby)
    - before_irradiate
    - after_irradiate
    - annealing_24h_25c
    - annealing_168h_25c
    - annealing_168h_100c
```

**Szczególności:**
- Oś X jest kategoryczna (nazwy etapów), dlatego `x_scale` jest ignorowana
- `y_scale` pracuje normalnie
- Prawa panel pokazuje próbki referencyjne na szaro (jeśli zdefiniowane)
- Przydatne do obserwacji regeneracji parametrów podczas nagrzewania

**Przykład:**
```yaml
- name: "TPS25963_iq_annealing"
  type: annealing
  title: "TPS25963 - Regeneracja I_Q podczas wyżarzania"
  lcl_name: TPS25963
  measurement_type: iquiescent
  metric: iq_ua
  stages_order:
    - before_irradiate
    - after_irradiate
    - annealing_24h_25c
    - annealing_168h_25c
    - annealing_168h_100c
  y_label: "I_Q [uA]"
```

## Warianty (variants)

Aby wygenerować kilka PNG ze tego samego wpisu (np. jeden ze statystykami, jeden tylko z punktami), użyj `variants`:

```yaml
- name: "TPS2553_iq_views"
  type: absolute
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  stages: [before_irradiate, after_irradiate]
  y_label: "I_Q [uA]"
  variants:
    - suffix: "_stats_only"           # Sufiks dodany do nazwy pliku
      show_points: false              # Schowaj punkty
      show_lines: [min, max, mean]   # Pokaż linie statystyczne
    - suffix: "_points_only"
      show_points: true               # Pokaż punkty
      show_lines: []                  # Bez linii
```

Wynikowe pliki PNG:
- `TPS2553_iq_views_stats_only.png`
- `TPS2553_iq_views_points_only.png`

**Każdy wariant dziedziczy ustawienia z głównego wpisu, a następnie je przesławia.**

## Podział na panele (split_by)

Aby wygenerować **osobne PNG** dla każdej grupy wzdłuż wymiaru (lot, bias), użyj `split_by`:

```yaml
- name: "TPS2553_iq_delta_by_lot_bias"
  type: delta
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  delta_from: before_irradiate
  delta_to: after_irradiate
  split_by: [lot, bias]  # Wygeneruj jeden PNG dla każdej kombinacji lot+bias
```

Przyjęte wartości:
- `lot` - podział po lotach (grupy zdefiniowane w `dose_map.yaml`)
- `bias` - podział po nastawieniu bias/unbias
- `[lot, bias]` - kombinacja obu

Rzeczywiste nazwy grup pochodzą z pliku `dose_map.yaml`. Jeśli dla danego wymiaru brak grup, wymiar jest pomijany.

Przykład wyniku z `split_by: [lot, bias]`:
- `TPS2553_iq_delta_by_lot_bias_lotA_bias.png`  (title: `… - LOT A, bias`)
- `TPS2553_iq_delta_by_lot_bias_lotA_unbias.png` (title: `… - LOT A, unbias`)
- `TPS2553_iq_delta_by_lot_bias_lotB_bias.png`
- `TPS2553_iq_delta_by_lot_bias_lotB_unbias.png`

**Nazwa grupy automatycznie dopisywana do `title`** — jeśli `title: "Enable hysteresis"` i `split_by: lot`, to PNG dla LOT A dostanie tytuł `"Enable hysteresis - LOT A"`.

**`split_by` i `variants` się komponują** - wykres z 3 wariantami i `split_by: lot` da `3 × liczba_lotów` plików PNG.

## Wiele serii na jednym wykresie (series_by)

W przeciwieństwie do `split_by`, opcja `series_by` zachowuje **jeden plik PNG**, ale rysuje **wiele serii** w obrębie tego samego wykresu — po jednej serii dla każdej wartości w danym wymiarze.

```yaml
- name: "TPS2553_hyst_series"
  type: absolute
  lcl_name: TPS2553
  measurement_type: enable_input_hysteresis
  metric: [measured_falling_v, measured_rising_v]  # 2 metryki
  stages: [after_irradiate]
  series_by: lot                                   # + 2 loty
  # = 4 serie na jednym wykresie:
  #   measured_falling_v - After - LOT A
  #   measured_falling_v - After - LOT B
  #   measured_rising_v  - After - LOT A
  #   measured_rising_v  - After - LOT B
```

Przyjęte wartości: `lot`, `bias` lub `[lot, bias]`.

**Jak różnią się od `split_by`?**

| `split_by` | `series_by` |
|------------|-------------|
| Tworzy **osobne pliki PNG** | Tworzy **jedną serię na grupę** w jednym pliku |
| Każdy plik pokazuje jedną grupę | Wszystkie grupy widoczne obok siebie |
| Tytuł: `"Tytuł - LOT A"` | Tytuł: `"Tytuł - by LOT"` |

Obsługiwane na wykresach `absolute` i `delta`. Wykresy `annealing` już używają osi serii (dawka), więc `series_by` jest tam ignorowane — na nich należy używać `split_by`.

## Subploty — wiele paneli w jednym pliku (`subplots`)

Aby porównać kilka grup w **jednym pliku PNG**, gdzie każda grupa zajmuje osobny panel z własnymi limitami/seriami/filtrami, użyj `subplots:`. W przeciwieństwie do `series_by` (jedne osie z wieloma seriami) i `split_by` (osobne pliki), `subplots` rysuje wiele osi w obrębie jednego rysunku.

Każdy panel dziedziczy wszystkie pola z rodzica **oprócz `title`** (tytuł rodzica jest umieszczany jako tytuł całej figury — `suptitle`). Można nadpisać dowolne pole panel-by-panel: `lot`, `bias`, `y_lim`, `x_lim`, `y_scale`, `series_by`, `include_doses`, `exclude_sn`, `title`, `show_lines`, `show_points`, itp.

```yaml
- name: "TPS2553_iq_compare_lots"
  type: absolute
  title: "TPS2553 - Quiescent current vs dose"
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  stages: [after_irradiate]
  x_scale: log
  y_label: "I_Q [uA]"
  figsize: [10, 10]      # Większa figura, dwa panele jeden pod drugim
  subplots:
    - title: "LOT A"
      lot: A
      y_lim: [0, 1000]   # Limity tylko dla tego panelu
    - title: "LOT B"
      lot: B
      y_lim: [0, 1000]
  subplot_layout: rows   # rows (domyślnie) | cols | grid
  share_x: true          # domyślnie true - wspólna oś X
  share_y: true          # domyślnie false
```

| Opcja | Opis |
|-------|------|
| `subplots` | Lista paneli. Każdy to mapping dziedziczący po rodzicu (oprócz `title`). |
| `subplot_layout` | `rows` (pionowo, domyślnie), `cols` (poziomo), `grid` (prostokątna siatka). |
| `share_x` | Czy panele dzielą oś X (domyślnie `true`). |
| `share_y` | Czy panele dzielą oś Y (domyślnie `false`; włącz dla porównań w tej samej skali). |
| `figsize` | Ustaw ręcznie — domyślnie skalowany do liczby paneli. |

Obsługa: typy `absolute` i `delta`. Dla typu `annealing` użyj `split_by` (annealing już używa prawego panelu na próbki referencyjne).

`subplots` można łączyć z:
- `variants:` — każdy wariant może mieć inną konfigurację `subplots`.
- `split_by:` — rodzic dzielony na pliki PNG (per lot/bias), w każdym te same subploty.
- `series_by:` w obrębie panelu — np. panel "LOT A" może mieć serie per `bias`.

## Linie referencyjne / limity (`reference_lines`)

Linie poziome (`y:`) lub pionowe (`x:`) z podpisem w legendzie — typowo do oznaczenia limitów specyfikacji (np. **±8 % dokładności I_lim**) lub innych punktów odniesienia.

```yaml
- name: "TPS2553_ilim_accuracy"
  type: delta
  title: "TPS2553 - I_lim accuracy"
  ...
  y_lim: [-15, 15]
  reference_lines:
    - y: 8.0
      label: "+8 %"
    - y: -8.0
      label: "-8 %"
    - y: 0.0
      label: "target"
      color: black
      linestyle: ":"
```

Każdy wpis ma:

| Pole | Opis |
|------|------|
| `y` lub `x` | Pozycja linii (poziomej `axhline(y)` lub pionowej `axvline(x)`). Dokładnie jedno z tych pól musi być obecne. |
| `label` | Wpis w legendzie (opcjonalny). |
| `color` | Domyślnie `red`. Dowolna nazwa matplotlib lub hex. |
| `linestyle` | Domyślnie `"--"`. Inne: `":"`, `"-."`, `"-"`. |
| `linewidth` | Domyślnie `1.2`. |
| `alpha` | Domyślnie `0.8`. |

Linie rysowane są pod danymi (`zorder=1.5`), więc nie zasłaniają punktów. Działają na wszystkich trzech typach wykresów (`absolute`, `delta`, `annealing`).

### W GUI

Zakładka **Lines** ma dynamiczny edytor wierszy — kolumny `axis | value | label | color | style | ✕`. Trzy przyciski:

- **`+ Add line`** — pusty wiersz.
- **`Add ±8 % band`** — dorzuca od razu 2 wiersze: `y=8` z labelem `"+8 %"` i `y=-8` z labelem `"-8 %"`.
- **`Add ±5 % band`** — to samo dla ±5 %.

Pusty `value` → wiersz pomijany w YAML. Linie pojawiają się na żywo w podglądzie.

## Punkty bazowe w 0 kRad (`show_before_at_zero`)

Dla wykresów `absolute`: jeśli `before_irradiate` znajduje się w `stages`, w okolicy `x = 0 kRad` zostaną dodatkowo narysowane punkty z wartościami **before** każdego SN, który bierze udział w wykresie (z poszanowaniem filtrów `lot`, `bias`, `exclude_sn` oraz wyłączeniem próbek referencyjnych). Pozwala to zobaczyć rozkład wartości startowych obok wartości po napromieniowaniu.

Domyślne zachowanie:
- `show_before_at_zero: true` — gdy `before_irradiate` jest w `stages`,
- `show_before_at_zero: false` — gdy `before_irradiate` nie jest w `stages`.

Można wymusić wyłączenie:

```yaml
- name: "no_baseline_at_zero"
  type: absolute
  ...
  stages: [before_irradiate, after_irradiate]
  show_before_at_zero: false
```

> **Uwaga:** punkty w `x = 0` nie pojawią się na osi logarytmicznej (`x_scale: log`), ponieważ log(0) nie istnieje. Użyj `symlog` jeśli musisz pokazać 0 na osi logarytmicznej.

## Próbki referencyjne (control samples)

Numery seryjne wymienione w `dose_map.yaml` w sekcji `reference_sns` są traktowane jako próbki kontrolne:

- **Wyłączane** z wykresów `absolute` i `delta`
- **Wyświetlane na osobnym panelu** po prawej stronie każdego wykresu `annealing` (szary kolor, dla porównania)

Jeśli `reference_sns` nie jest określone, automatycznie każdy SN z `dose == 0` traktowany jest jako referencja.

## Wyjście

Katalog wyjściowy zawiera:

- **PNG pliki** - jeden dla każdego wykresu/wariantu/podziału (nazwane: `{name}{suffix?}{lot_bias_suffix?}.png`)
- **`_plot_index.csv`** - podsumowanie wszystkich prób renderowania z:
  - `output_name` - nazwa pliku PNG
  - `type` - typ wykresu
  - `lcl_name`, `measurement_type`, `metric` - parametry
  - `n_points` - liczba punktów danych
  - `n_series` - liczba serii (linii)
  - `skipped` - czy pominięty (true/false)
  - `reason` - przyczyna pominięcia (jeśli dotyczy)

## Przykłady konfiguracji

### Przykład 1: Prosty wykres absolutny

```yaml
- name: "simple_plot"
  type: absolute
  title: "Mój pierwszy wykres"
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  stages: [before_irradiate, after_irradiate]
  y_label: "Prąd [uA]"
```

### Przykład 2a: Wiele serii (LOT A / LOT B) na jednym wykresie

```yaml
- name: "TPS2553_hyst_lots_side_by_side"
  type: absolute
  title: "TPS2553 - Enable hysteresis"
  lcl_name: TPS2553
  measurement_type: enable_input_hysteresis
  metric: [measured_falling_v, measured_rising_v]
  stages: [after_irradiate]
  series_by: lot   # LOT A i LOT B jako dwie serie -> łącznie 4 serie
  # Tytuł finalny: "TPS2553 - Enable hysteresis - by LOT"
```

### Przykład 2b: Subploty — LOT A nad LOT B w jednym PNG

```yaml
- name: "TPS2553_iq_compare_lots"
  type: absolute
  title: "TPS2553 - Quiescent current vs dose"
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  stages: [after_irradiate]
  x_scale: log
  y_label: "I_Q [uA]"
  figsize: [10, 10]
  share_y: true
  subplots:
    - title: "LOT A"
      lot: A
    - title: "LOT B"
      lot: B
      # Panel LOT B z dodatkowym podziałem serii per bias
      series_by: bias
      y_lim: [0, 500]   # Tylko ten panel ma ścięty Y
```

### Przykład 2: Delta z podziałem i wariantami

```yaml
- name: "delta_detailed"
  type: delta
  title: "Zmiana z podziałem na lot/bias"
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  delta_from: before_irradiate
  delta_to: after_irradiate
  split_by: [lot, bias]
  variants:
    - suffix: "_percent"
      y_label: "Zmiana [%]"
    - suffix: "_log_scale"
      y_scale: symlog
      y_label: "Zmiana [%] (skala log)"
```

### Przykład 3: Annealing z filtrami

```yaml
- name: "annealing_filtered"
  type: annealing
  title: "Regeneracja - wysoke dawki"
  lcl_name: TPS25963
  measurement_type: iquiescent
  metric: iq_ua
  stages_order:
    - before_irradiate
    - after_irradiate
    - annealing_24h_25c
    - annealing_168h_25c
    - annealing_168h_100c
  include_doses: [15, 25]  # Tylko wysokie dawki
  exclude_sn: [SN_BROKEN]  # Wyłącz uszkodzony chip
  y_label: "I_Q [uA]"
  y_scale: log
```

## Tryb GUI - `plot_builder.py`

Zamiast pisać konfigurację YAML ręcznie, można skorzystać z interaktywnego GUI:

```bash
python plot_builder.py
# albo
python plot_builder.py --config sciezka/do/plot_config.yaml
```

Tool wczytuje `plot_config.yaml` żeby ustalić ścieżki (`flat_files_dir`, `dose_map`) i sekcję `defaults`, a następnie buduje master DataFrame, dzięki czemu wszystkie dropdowny pokazują tylko realnie obecne wartości (lcl_name, measurement_type, metric, context_key, stages, doses, SN-y).

### Co da się ustawić w GUI

Wszystkie pola wspierane przez YAML, podzielone na cztery zakładki:

| Zakładka | Pola |
|----------|------|
| **Data** | `name`, `type`, `title`, `note`, `lcl_name`, `measurement_type`, `metric` (multi), `context_key`, `stages` / `stages_order` (multi), `delta_from`, `delta_to` |
| **Filters** | `include_doses` (multi), `exclude_doses` (multi), `exclude_sn` (multi), `lot`, `bias`, `show_before_at_zero` |
| **Appearance** | `y_label`, `x_label`, `x_lim`, `y_lim`, `x_scale`, `y_scale`, `figsize`, `dpi`, `format`, `grid`, `legend`, `show_points`, `show_lines` (multi), `marker_size`, `alpha_points` |
| **Lines** | `reference_lines` - dynamiczny edytor wierszy (`y`/`x`, value, label, color, linestyle); skróty `+ Add line` / `Add ±8 % band` / `Add ±5 % band` |
| **Grouping** | `split_by` (checkboxy), `series_by` (checkboxy) |
| **Advanced** | dowolny dodatkowy YAML wmergowany do wpisu - dla `variants:` / `subplots:` (póki nie mają własnego edytora w GUI) |

### Auto-fill: `name`, `title`, `y_label`, `x_label`

Obok każdego z tych czterech pól jest checkbox **`auto`** (domyślnie włączony). Gdy włączony:

- pole jest read-only,
- jego wartość regeneruje się automatycznie po każdej zmianie selektorów (lcl_name / measurement_type / metric / type / context_key / delta_from / delta_to / split_by / series_by).

Wyłącz checkbox jeśli chcesz wpisać własną wartość — pole stanie się edytowalne i nie będzie nadpisywane.

Wzorce (przy auto):

| Pole | Wzorzec |
|------|---------|
| `name` | `<lcl_name>_<measurement_type>_<metric>_<type>` + ewentualne `_by_lot`, `_series_bias` itp. Slug: tylko `[A-Za-z0-9_]`. |
| `title` | absolute: `LCL - Metric vs dose` &nbsp;·&nbsp; delta: `LCL - Delta Metric (from → to) [%]` &nbsp;·&nbsp; annealing: `LCL - Metric recovery during annealing`. Gdy `context_key` jest ustawiony, dopisywane jest ` @ <context>` (np. ` @ I_load=1.0 A` dla `iload_a=1.0`). |
| `y_label` | `<Pretty Metric> [<unit>]` (jednostka czytana z `unit` w master DataFrame). Dla delta: `Δ <Metric> [%]`. Dla multi-metric: nazwa `measurement_type`. |
| `x_label` | `Dose [kRad]` dla absolute/delta; `Stage` dla annealing. |

"Pretty" metric — krótki słownik znanych metryk (`iq_ua` → `I_Q`, `rdson_mohm` → `R_DS(ON)`, `measured_falling_v` → `V_EN falling` itd.). Dla nieznanych: surowa nazwa metryki.

### Cascade dropdowns

Wybór w `lcl_name` filtruje `measurement_type`. Wybór w `measurement_type` filtruje listę `metric`. Wybór `metric` filtruje `context_key` i `stages`. Pola `delta_from` / `delta_to` dostają tę samą listę co `stages`. Wszystko aktualizuje się natychmiast.

### Live preview

Prawa strona okna pokazuje wykres rysowany dokładnie tym samym kodem co `plot_radiation.py`. Po każdej zmianie pola (z opóźnieniem ~500 ms) podgląd jest odświeżany. Działa dla wszystkich trzech typów (`absolute`, `delta`, `annealing`). Jeśli konfiguracja jest niekompletna (np. brak wybranej metryki), na podglądzie pojawia się czytelny komunikat zamiast crashu.

### Przyciski

- **Refresh preview** - wymuś przerysowanie (jeśli auto-debounce został zgubiony).
- **Generate YAML** - zapis pliku `_generated_plots/<name>.yaml` obok `plot_config.yaml`.
- **Copy YAML to clipboard** - ten sam tekst do schowka systemowego (gotowe do wklejenia).
- **Reset form** - wyczyść wszystko (relaunch okna).

### Co trafia do YAML

Tylko pola które **różnią się od `defaults:`** z aktywnego `plot_config.yaml`. Dzięki temu wygenerowany snippet jest krótki i wkleja się czysto:

```yaml
- name: my_new_plot
  type: absolute
  title: TPS2553 - moj nowy wykres
  lcl_name: TPS2553
  measurement_type: iquiescent
  metric: iq_ua
  stages:
    - after_irradiate
  x_scale: log
  series_by:
    - lot
```

Pola jak `figsize: [10, 6]`, `dpi: 150` czy `show_lines: [min, max, mean]` nie pojawią się jeśli pokrywają się z `defaults`.

### Ograniczenia v1

- `variants:` i `subplots:` - brak dedykowanego klikalnego edytora. Wpisz je w zakładce **Advanced** jako surowy YAML, np.:
  ```yaml
  subplots:
    - title: LOT A
      lot: A
    - title: LOT B
      lot: B
  subplot_layout: rows
  ```
  Treść tej zakładki zostanie wmergowana do wpisu i widoczna w podglądzie.
- Edycja `stage_styles` / `stage_labels` - rzadko ruszane per-plot, edytuj w `defaults:` ręcznie.

## Rozwiązywanie problemów

### Błąd: "Plot config error"
- Sprawdź składnię YAML (wcięcia, dwukropki)
- Upewnij się, że wszystkie wymagane pola są obecne
- Uruchom z `--verbose` aby zobaczyć szczegóły

### Błąd: "No plots to render"
- Sprawdź czy `--only` filter pasuje do rzeczywistych nazw
- Uruchom `--dry-run` aby zobaczyć dostępne nazwy

### Wykres pominięty: "EMPTY - would be skipped"
- Filtry `include_doses`, `exclude_sn`, `lot`, `bias` mogą być za restrykcyjne
- Sprawdź wartości w `dose_map.yaml`
- Uruchom `--dry-run` aby zobaczyć liczby wierszy

### Brak punktów na wykresie
- Sprawdź czy `context_key` jest poprawny (szczególnie dla rdson, iload variations)
- Czy `metric` rzeczywiście istnieje w danych?
- Uruchom `--verbose` aby zobaczyć detale

### Próbki referencyjne nie pojawiają się
- Na wykresach `absolute` i `delta` są celowo wyłączane
- Pojawiają się jako panel na prawym panelu wykresów `annealing`
- Sprawdź czy `reference_sns` jest zdefiniowany w `dose_map.yaml`

## Obsługiwane formaty markerów

| Symbol | Marker |
|--------|--------|
| `o` | Koło |
| `s` | Kwadrat |
| `^` | Trójkąt w górę |
| `v` | Trójkąt w dół |
| `D` | Diament |
| `*` | Gwiazda |
| `+` | Plus |
| `x` | Krzyż |
| `d` | Diament cienki |

## Obsługiwane skale

| Skala | Opis |
|-------|------|
| `linear` | Liniowa (domyślna) |
| `log` | Logarytmiczna (log10) |
| `symlog` | Symetryczna logarytmiczna (dla danych z ujemnymi wartościami) |

## Notatki

- Ścieżki względne w YAML są względne do lokalizacji pliku konfiguracyjnego
- Nazwy etapów (`before_irradiate`, `after_irradiate`, itd.) muszą dokładnie pasować do danych
- Próbki referencyjne są zawsze wyłączane z wykresów `absolute` i `delta` (na `absolute` dotyczy to także punktów `show_before_at_zero`)
- Delta plots zawsze pokazują względną zmianę procentową
- Oś X na annealing plots to kategorie (etapy), nie liczby
- `split_by` i `series_by` mogą używać tych samych wymiarów (`lot`, `bias`), ale różnią się tym, że `split_by` produkuje **osobne pliki PNG** a `series_by` rysuje **wiele serii w jednym pliku**. Można ich używać razem (np. `split_by: bias` + `series_by: lot` da po jednym PNG na każdy bias, a w każdym dwie serie LOT A / LOT B)
