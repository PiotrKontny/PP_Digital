# SERWER_SETUP.md — jak uruchomić serwer „Pędzącego Piotrka”

Ten dokument zakłada, że **nigdy wcześniej nie uruchamiałeś serwera**. Nie
musisz znać Linuksa, Dockera ani sieci. Wszystko sprowadza się do: wgrać
projekt na Railway, kliknąć Deploy, skopiować adres i wkleić go do gry.

Czas: około 15 minut za pierwszym razem.

---

## Spis treści

1. [Dlaczego w ogóle potrzebny jest serwer](#1-dlaczego-w-ogóle-potrzebny-jest-serwer)
2. [Uruchomienie serwera na własnym komputerze](#2-uruchomienie-serwera-na-własnym-komputerze)
3. [Wrzucenie serwera na Railway](#3-wrzucenie-serwera-na-railway)
4. [Które pliki należą do serwera](#4-które-pliki-należą-do-serwera)
5. [Co trzeba zmienić w konfiguracji](#5-co-trzeba-zmienić-w-konfiguracji)
6. [Jak zakładam pokój](#6-jak-zakładam-pokój)
7. [Jak dołączają znajomi](#7-jak-dołączają-znajomi)
8. [Co zrobić, kiedy nie działa](#8-co-zrobić-kiedy-nie-działa)

---

## 1. Dlaczego w ogóle potrzebny jest serwer

Krótko, bo to wyjaśnia wszystkie późniejsze decyzje.

Twój komputer w domu siedzi za routerem. Router **nie przepuszcza połączeń
przychodzących** — to nie usterka, tylko sposób działania NAT-u. Dlatego
„jeden gracz hostuje, reszta się do niego łączy” może działać w jednej sieci
domowej, ale **nigdy** przez internet bez przekierowania portów, VPN-u albo
Hamachi. A tego właśnie nie chcesz.

Jedyne rozwiązanie, które spełnia warunek „Polska ↔ Wielka Brytania, bez
konfiguracji routera”, jest takie: **wszyscy łączą się na zewnątrz**, do
maszyny, która ma publiczny adres. Ta maszyna to serwer z tego dokumentu.

Ważne: **osoba, która zakłada pokój, nie uruchamia serwera.** Host to zwykły
gracz z dwoma dodatkowymi przyciskami (Start i ustawienia stołu). Serwer stoi
osobno i działa cały czas.

---

## 2. Uruchomienie serwera na własnym komputerze

Zrób to najpierw. Zajmuje dwie minuty i pozwala sprawdzić, że gra działa,
zanim zaczniesz cokolwiek wdrażać.

### 2.1. Zainstaluj zależności

```bash
pip install websockets
```

To wszystko. Serwer **nie potrzebuje pygame** — nie rysuje niczego.

### 2.2. Uruchom serwer

W katalogu projektu:

```bash
python -m pedzacy_piotrek.server
```

Powinieneś zobaczyć:

```
2026-08-05 12:00:00  INFO    Serwer nasłuchuje na 0.0.0.0:51337 (pokoje: 1)
```

Serwer działa, dopóki nie zamkniesz okna (albo nie wciśniesz `Ctrl+C`).

### 2.3. Sprawdź, że żyje

Otwórz w przeglądarce:

```
http://127.0.0.1:51337/
```

Zobaczysz stronę kontrolną:

```
Pędzący Piotrek — serwer gry działa.
Wersja protokołu: 2
Otwarte pokoje: 0 (brak)
Podłączeni gracze: 0
```

**Zapamiętaj tę stronę.** To jedyne narzędzie diagnostyczne, jakiego
potrzebujesz: jeśli się otwiera, serwer działa i problem jest gdzie indziej.

### 2.4. Zagraj lokalnie

Uruchom grę w drugim oknie (`python run_game.py`), wybierz **Załóż grę
online**, w polu „Serwer gry” zostaw `ws://127.0.0.1:51337` i kliknij **Utwórz
pokój**. Dostaniesz kod pokoju.

Na tym samym komputerze możesz uruchomić drugą kopię gry i dołączyć tym kodem.

> Opcja „Uruchom serwer na tym komputerze” w ekranie zakładania gry robi to samo
> automatycznie, ale **działa tylko w jednej sieci lokalnej**. Przez internet
> potrzebujesz kroku 3.

---

## 3. Wrzucenie serwera na Railway

Railway to usługa, która bierze kod z GitHuba i uruchamia go na swojej maszynie
z publicznym adresem. Ma darmowy pakiet startowy, w zupełności wystarczający
dla jednego stołu.

### 3.1. Wrzuć projekt na GitHub

Jeśli projektu jeszcze tam nie ma:

1. Załóż konto na <https://github.com>.
2. Kliknij **New repository**, nazwij je np. `pedzacy-piotrek`, wybierz
   **Private** (to gra dla znajomych, nie musi być publiczna).
3. W katalogu projektu:

```bash
git init
git add .
git commit -m "Pędzący Piotrek"
git branch -M main
git remote add origin https://github.com/TWOJA-NAZWA/pedzacy-piotrek.git
git push -u origin main
```

Wrzuć **cały projekt**, nie tylko serwer. Railway sam pominie to, czego nie
potrzebuje — mówi mu o tym plik `.railwayignore`.

### 3.2. Utwórz projekt na Railway

1. Wejdź na <https://railway.app> i zaloguj się przez GitHuba.
2. **New Project** → **Deploy from GitHub repo**.
3. Wybierz swoje repozytorium.
4. Railway zacznie budować od razu. Poczekaj, aż status zmieni się na
   **Success** (zwykle 1–3 minuty).

Nie musisz nic konfigurować. W projekcie są już gotowe pliki, które mówią
Railwayowi wszystko, czego potrzebuje:

| Plik | Co robi |
|---|---|
| `railway.json` | komenda startowa, ścieżka health-checku, polityka restartów |
| `requirements-server.txt` | instaluje **tylko** `websockets`, bez pygame |
| `.railwayignore` | pomija `ui/`, `render/`, `assets/`, testy i dokumentację |
| `Procfile` | zapasowa komenda startowa dla platform, które czytają ten format |

### 3.3. Włącz publiczny adres

Railway domyślnie nie wystawia usługi na świat.

1. Wejdź w swój serwis → zakładka **Settings**.
2. Sekcja **Networking** → **Generate Domain**.
3. Dostaniesz adres w rodzaju:

```
pedzacy-piotrek-production.up.railway.app
```

### 3.4. Sprawdź, że działa

Otwórz ten adres w przeglądarce, z `https://` na początku:

```
https://pedzacy-piotrek-production.up.railway.app/
```

Musisz zobaczyć tę samą stronę kontrolną co w punkcie 2.3. Jeśli ją widzisz —
**serwer działa i masz wszystko, czego potrzebujesz.**

### 3.5. Czego NIE musisz robić

- ❌ Nie ustawiasz portu. Railway podaje go sam w zmiennej `PORT`, a serwer ją
  czyta. Ignorowanie tej zmiennej to najczęstszy powód, dla którego wdrożenie
  „nic nie odpowiada”.
- ❌ Nie konfigurujesz certyfikatów. Railway sam obsługuje szyfrowanie
  i rozmawia ze światem po `wss://`.
- ❌ Nie potrzebujesz Dockera. `Dockerfile` w projekcie zostaje na wypadek
  innej platformy, ale Railway go nie używa.
- ❌ Nie przekierowujesz portów na routerze. O to właśnie chodziło.

---

## 4. Które pliki należą do serwera

Projekt ma trzy warstwy. Nazwy katalogów są historyczne (wszystko żyje
w pakiecie `pedzacy_piotrek/`), ale podział jest ścisły i **sprawdzany
testem** — `test_the_server_needs_neither_pygame_nor_the_client_packages`
uruchamia serwer z zablokowanym pygame i ukrytym `ui/`.

### Tylko serwer

| Katalog / plik | Rola |
|---|---|
| `pedzacy_piotrek/server/app.py` | proces asyncio, gniazda, strona kontrolna |
| `pedzacy_piotrek/server/hub.py` | routing, tożsamości, tokeny powrotu |
| `pedzacy_piotrek/server/room.py` | autorytatywny stan gry, miejsca, log komend |
| `pedzacy_piotrek/server/registry.py` | rejestr pokoi |
| `pedzacy_piotrek/server/embedded.py` | serwer w tle gry + wersja do testów |
| `railway.json`, `Procfile`, `Dockerfile` | wdrożenie |
| `requirements-server.txt` | zależności serwera |

### Wspólne (potrzebne po obu stronach)

Serwer jest autorytatywny — sam buduje prawdziwą partię, więc potrzebuje
zasad gry:

```
pedzacy_piotrek/engine/     zasady, komendy, zdarzenia, efekty
pedzacy_piotrek/cards/      definicje i talie kart
pedzacy_piotrek/board/      generowanie planszy
pedzacy_piotrek/players/    gracze i role
pedzacy_piotrek/config/     ustawienia i zasady
pedzacy_piotrek/data/       cards.json, characters.json, board.json, network.json
pedzacy_piotrek/net/        protokół, konfiguracja sieci, poczekalnia, komunikaty
```

### Tylko klient (gra)

```
pedzacy_piotrek/ui/         ekrany, widgety, poczekalnia
pedzacy_piotrek/render/     rysowanie
pedzacy_piotrek/assets/     grafika, dźwięki, czcionki
run_game.py                 uruchomienie gry
```

Tych trzech katalogów serwer **nigdy** nie importuje. Dlatego `.railwayignore`
je pomija i dlatego obraz na Railwayu jest mały.

---

## 5. Co trzeba zmienić w konfiguracji

**Jedna wartość.** Plik:

```
pedzacy_piotrek/data/network.json
```

Zmieniasz tylko `server_url`:

```json
{
  "server_url": "wss://pedzacy-piotrek-production.up.railway.app",
  ...
}
```

Zasada, którą warto zapamiętać:

| Gdzie gracie | Co wpisać |
|---|---|
| Serwer na tym komputerze | `ws://127.0.0.1:51337` |
| Serwer u kogoś w tej samej sieci | `ws://192.168.0.14:51337` |
| Serwer na Railwayu | `wss://twoj-adres.up.railway.app` |

Zwróć uwagę na **dwa `s`** w `wss://` przy Railwayu — to wersja szyfrowana,
tak jak `https://`. Adres Railwaya bez portu.

> Gra jest tolerancyjna: możesz wkleić `https://twoj-adres.up.railway.app`
> albo samo `twoj-adres.up.railway.app`, a i tak zostanie zamienione na
> `wss://twoj-adres.up.railway.app`. Sam adres IP albo `localhost` bez
> schematu dostanie `ws://` i port gry — bo w sieci domowej nie ma
> certyfikatów.

Ten sam adres można też wpisać **wprost w grze**, w polu „Serwer gry” na
ekranie zakładania albo dołączania — wtedy nie musisz w ogóle ruszać pliku.
Zmiana w `network.json` sprawia tylko, że jest on domyślnie podpowiadany.

**Adres zapamiętuje się sam.** Kiedy uda ci się założyć albo znaleźć pokój, gra
zapisuje ten adres u siebie i następnym razem wpisze go za ciebie. Nie wraca do
`localhost` — chyba że sam wyczyścisz pole. Zapis trafia do:

| System | Ścieżka |
|---|---|
| Windows | `%APPDATA%\pedzacy-piotrek\preferences.json` |
| macOS | `~/Library/Application Support/pedzacy-piotrek/preferences.json` |
| Linux | `~/.config/pedzacy-piotrek/preferences.json` |

Możesz ten plik skasować w każdej chwili — gra wróci wtedy do adresu
z `network.json`.

Obok pola „Serwer gry” jest przycisk **Kopiuj**, a w poczekalni **Kopiuj kod
pokoju**. Oba wrzucają wartość do schowka, więc możesz ją od razu wkleić
znajomym na czacie.

Dla porządku: wszystkie pozostałe ustawienia sieciowe (limity czasu,
heartbeat, ponawianie połączenia, okres na powrót po rozłączeniu) też są w tym
pliku i **nie musisz ich ruszać**. Wartości domyślne są dobrane pod grę
planszową.

---

## 6. Jak zakładam pokój

1. Upewnij się, że serwer działa (punkt 2.3 albo 3.4 — strona kontrolna).
2. Uruchom grę.
3. **Załóż grę online**.
4. Wpisz swój nick.
5. W polu „Serwer gry” wpisz adres serwera (albo zostaw ten z konfiguracji).
6. Ustaw stół: liczbę pól planszy, rundę otwarcia skrzyni, procent pól
   podwójnych.
7. Jeśli chcecie zagrać we dwie osoby, zaznacz **„Wersja testowa — gra od
   2 graczy”**. Normalnie gra wymaga trzech.
8. **Utwórz pokój**.

Zobaczysz poczekalnię z dużym napisem:

```
KOD POKOJU: K7M2QD
```

Ten kod podajesz znajomym — przez Discorda, telefon, jakkolwiek. Alfabet jest
tak dobrany, żeby dało się go **przeczytać na głos**: nie ma w nim zera ani
litery O, ani jedynki, I i L.

Grę rozpoczyna wyłącznie osoba, która założyła pokój — przycisk **Start**
odblokowuje się, kiedy wszyscy są gotowi.

---

## 7. Jak dołączają znajomi

Twój znajomy potrzebuje **trzech rzeczy** i niczego więcej:

1. gry,
2. adresu serwera,
3. kodu pokoju.

Kroki:

1. Uruchom grę.
2. **Dołącz do gry**.
3. Wpisz kod pokoju (np. `K7M2QD`).
4. Wpisz swój nick.
5. Sprawdź adres serwera — musi być **taki sam jak u ciebie**.
6. **Dołącz**.

Żadnego przekierowania portów, żadnego VPN-u, żadnego Hamachi. Znajomy
z Wielkiej Brytanii robi dokładnie to samo co znajomy z sąsiedniego pokoju.

W poczekalni każdy wybiera postać (albo zostawia „Losowa postać”) i klika
**Jestem gotowy**.

### Jeśli komuś zerwie połączenie

Nic nie robicie. Miejsce przy stole jest trzymane przez **3 minuty**, gra
sama łączy się ponownie i dogrywa to, co się wydarzyło. Reszta stołu widzi
komunikat, że czekacie.

---

## 8. Co zrobić, kiedy nie działa

Zacznij zawsze od strony kontrolnej w przeglądarce. Ona rozstrzyga, czy
problem jest po stronie serwera, czy adresu.

| Co widzisz w grze | Co to znaczy | Co zrobić |
|---|---|---|
| „Nie mogę połączyć się z serwerem gry” | Serwer nie odpowiada na tym adresie | Otwórz adres w przeglądarce. Pusto? Serwer nie działa albo nie ma wygenerowanej domeny (3.3) |
| „Nie znaleziono serwera pod tym adresem” | Literówka w adresie | Porównaj znak po znaku z adresem z Railwaya |
| „Serwer gry jest niedostępny” | Serwer się uruchamia albo restartuje | Poczekaj minutę i spróbuj ponownie; sprawdź logi na Railwayu |
| „Nie ma pokoju o kodzie XXXXXX” | Zły kod, albo pokój już zamknięty | Poproś o kod jeszcze raz. Pokój zamyka się, gdy wyjdą z niego wszyscy |
| „Stół jest pełny” | Sześć osób już siedzi | Tyle mieści gra |
| „Gra już się rozpoczęła” | Dołączasz do trwającej partii | Do trwającej partii wracają tylko ci, którzy w niej byli |
| „Utracono połączenie z serwerem” | Zerwało twój internet | Gra sama próbuje wrócić; miejsce czeka 3 minuty |
| „Trwa łączenie z serwerem…” | Uścisk dłoni jeszcze trwa | Powinno zniknąć samo w sekundę |
| Gra nie startuje, choć wszyscy są | Czegoś brakuje | Pod przyciskiem Start jest napisane czego — np. „Nie wszyscy są gotowi: Ola” |

### Panel diagnostyczny

W grze wciśnij **F3**. Zobaczysz adres serwera, kod pokoju, stan połączenia,
ping, liczniki wiadomości, liczbę ponownych połączeń i **odcisk stanu gry**.

Jeśli dwie osoby mają ten sam odcisk, ich partie są identyczne. Jeśli różny —
to błąd wart zgłoszenia.

### Logi serwera na Railwayu

Wejdź w serwis → zakładka **Deployments** → kliknij ostatnie wdrożenie →
**View Logs**. Zobaczysz każde połączenie i rozłączenie.

### Serwer zasypia / restartuje się

Darmowe pakiety usypiają nieużywane usługi. Pierwsze połączenie po przerwie
może trwać kilkanaście sekund — gra poczeka i spróbuje ponownie sama.

Jeśli serwer restartuje się w trakcie partii, wszyscy tracą pokój: stan gry
żyje w pamięci serwera i nie jest nigdzie zapisywany. To świadoma decyzja —
gra nie ma bazy danych ani zapisów w chmurze.

---

## Ściągawka

```bash
# lokalnie
pip install websockets
python -m pedzacy_piotrek.server
# → http://127.0.0.1:51337/  powinno pokazać stronę kontrolną

# na innym porcie
python -m pedzacy_piotrek.server --port 8080

# z pełnym logowaniem każdej wiadomości
python -m pedzacy_piotrek.server --verbose
```

| Rzecz | Wartość |
|---|---|
| Domyślny port | `51337` |
| Adres lokalny | `ws://127.0.0.1:51337` |
| Adres na Railwayu | `wss://twoj-adres.up.railway.app` |
| Plik konfiguracyjny | `pedzacy_piotrek/data/network.json` |
| Strona kontrolna | `/` albo `/health` |
| Komenda startowa | `python -m pedzacy_piotrek.server` |
| Zależność serwera | `websockets` (bez pygame) |
| Maksymalnie graczy | 6 (minimum 3, albo 2 w wersji testowej) |
| Czas na powrót po rozłączeniu | 3 minuty |
