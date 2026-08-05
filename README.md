# Pędzący Piotrek

Cyfrowa wersja fanowskiego rozszerzenia do *Pędzących Żółwi*. Projekt
niekomercyjny, tworzony po to, żeby wygodnie testować balans mechanik, a
docelowo grać ze znajomymi przez internet.

```bash
pip install -r requirements.txt
python run_game.py
```

Gra otwiera się na menu głównym: **Załóż grę**, **Dołącz do gry**, **Gra lokalna
(hot-seat)** albo **Wyjście**.

## Gra przez internet

Gra działa jak każda współczesna gra online: stoi **dedykowany serwer**, a
wszyscy gracze — łącznie z tym, kto zakłada pokój — łączą się do niego na
zewnątrz. Dzięki temu można grać z osobą w innym kraju, za innym routerem, na
innym operatorze, **bez przekierowania portów, bez Hamachi, bez VPN-a**.

1. Jedna osoba wybiera **Załóż grę** i dostaje **kod pokoju**, np. `K7M2QD`.
2. Podaje kod znajomym.
3. Reszta wybiera **Dołącz do gry** i wpisuje sam kod — bez adresów i portów.
4. Każdy wybiera **swoją** postać (zajęte znikają z listy), klika *Jestem
   gotowy*, a partię zaczyna host.

Adres serwera ustawia się raz w `pedzacy_piotrek/data/network.json` (albo
`--server`, albo zmienną `PIOTREK_SERVER_URL`) i rozdaje grę już ustawioną.

**Serwer trzeba gdzieś postawić.** To nie jest wybór projektu, tylko sposób
działania internetu w domach: komputer za NAT-em nie przyjmuje połączeń
przychodzących, więc żaden gracz nie może być serwerem dla kogoś z zewnątrz.
Uruchomienie to jedno polecenie, a darmowe plany hostingowe w zupełności
wystarczają — krok po kroku opisuje to **[SERVER_SETUP.md](SERVER_SETUP.md)**
(instrukcja od zera: Railway, adres, kod pokoju, co robić gdy nie działa).
Krótsze tło techniczne jest w [docs/SERWER.md](docs/SERWER.md).

```bash
python -m pedzacy_piotrek.server          # serwer (bez okna, bez pygame)
python run_game.py --serve                # to samo, z poziomu gry
```

Do gry w jednej sieci nie trzeba niczego wdrażać: w ekranie zakładania jest
opcja **„Uruchom serwer na tym komputerze”**. Przez internet to nie zadziała i
interfejs to mówi wprost.

### Co jest po której stronie

Serwer jest **autorytatywny**: to on ma jedyny prawdziwy stan gry i on decyduje.
Klient nigdy niczego nie zgaduje — wysyła propozycję akcji i czeka na
potwierdzenie.

| Serwer | Klient |
|---|---|
| kolejność tur, talie, plansza, modyfikatory | rysowanie i animacje |
| kto zajmuje które miejsce | własna ręka i własna postać |
| losowość (ziarno) | wysyłanie akcji |
| sprawdzanie i zatwierdzanie akcji | stosowanie tego, co serwer potwierdzi |

Synchronizowane są **akcje, nie stan**: serwer wysyła raz ustawienia razem z
ziarnem losowości, każdy buduje u siebie identyczną grę, a potem po sieci lecą
tylko zatwierdzone komendy — kilkadziesiąt bajtów na ruch. Do każdej serwer
dokłada **sumę kontrolną** swojego stanu; klient, któremu się nie zgadza, sam
prosi o pełną synchronizację. Cały stan leci tylko w dwóch sytuacjach: gdy ktoś
wraca po zerwaniu i gdy dołącza do trwającej partii.

### Zerwane połączenie

Gra to przewiduje, a nie przewraca się na tym:

* chwilowa utrata sieci — plansza zostaje na ekranie, na górze pojawia się
  „ŁĄCZĘ PONOWNIE Z SERWEREM…”, a gra wraca sama i dociąga zaległości;
* miejsce przy stole jest trzymane przez 3 minuty (do zmiany w konfiguracji);
* jeśli ktoś nie wróci, pozostali grają dalej — jego miejsce po prostu zostaje
  puste;
* zamknięcie okna, wyłączenie serwera, timeout sieci — każdy przypadek kończy
  się komunikatem po polsku, nigdy zawieszeniem.

Interfejs nigdy nie czeka na pakiet: łączenie i wysyłanie dzieje się w tle, a
pętla rysowania chodzi dalej.

```bash
python run_game.py --host                       # od razu ekran zakładania
python run_game.py --join                       # od razu ekran dołączania
python run_game.py --server wss://adres         # inny serwer na ten raz
python run_game.py --net-debug                  # panel diagnostyki sieci (F3)
python -m pedzacy_piotrek.server --rooms 20     # serwer na wiele pokoi
```

W ustawieniach hosta (i w grze lokalnej) jest opcja **Wersja testowa**, która
pozwala zacząć w dwie osoby. Służy wyłącznie do testowania — normalnie potrzeba
3 graczy, a dwuosobowa partia nie jest zbalansowana.

## Co się zmieniło

Prototyp był jednym plikiem `game.py` (2196 linii), w którym stan gry, reguły,
układ ekranu i rysowanie mieszały się w tych samych funkcjach. Kod działał, ale
każda zmiana wymagała czytania całości, a o sieci nie było mowy — nie istniało
miejsce, w którym dałoby się przechwycić „co gracz właśnie zrobił”.

Teraz jest to pakiet z rozdzielonymi warstwami. **Żadna mechanika nie została
usunięta ani uproszczona** — wszystko, co działało, działa dalej, łącznie z
kolejnością tur, dwoma slotami Modów Patusa, siatką „Kolory Piotrka”, zmianą
nazw graczy i swobodnym przeciąganiem pionków.

## Struktura

```
pedzacy_piotrek/
├── config/      settings.py (reguły, wymiary), theme.py (paleta, czcionki)
├── data/        cards.json, characters.json, board.json  ← treść gry
├── cards/       definicje kart, talie, ładowanie i walidacja JSON-a
├── players/     Player, Role
├── board/       path.py (krzywe), tiles.py, board.py (generator planszy)
├── engine/      commands.py, events.py, game_state.py, setup.py,
│                turn_order.py, animation.py      ← zero importów pygame
├── render/      renderer.py, camera.py, board_renderer.py,
│                card_renderer.py, particles.py
├── ui/          app.py, layout.py, widgets.py, board_view.py, hud.py,
│                game_screen.py, menu.py
├── net/         config.py, protocol.py, transport.py, websocket.py,
│                client.py, session.py, service.py, lobby.py   ← strona klienta
├── server/      hub.py, room.py, registry.py  ← autorytatywna logika, bez I/O
│                app.py, embedded.py           ← warstwa asyncio/WebSocket
└── assets/      obrazki, dźwięki, czcionki (opcjonalne — patrz assets/README.md)
```

Zasada, na której to stoi: **`engine/` nie wie, że istnieje pygame**, a
`render/` i `ui/` nie znają żadnej reguły gry. Można uruchomić całą partię bez
okna — robi to zestaw testów.

To samo w sieci: **`net/` nie importuje pygame, a `ui/` nie widzi gniazd**.
Logika serwera (`hub.py`, `room.py`, `registry.py`) jest w całości synchroniczna
i nie zna gniazd — całe multiplayer da się przetestować bez sieci, a warstwa
asyncio nad nią nie zawiera żadnej reguły gry. Serwer nie potrzebuje pygame i
uruchamia się bez niego.

## Sterowanie

| Akcja | Efekt |
|---|---|
| LPM na talii | dobierz kartę |
| LPM na karcie z ręki | zagraj kartę (jeśli ma efekt), inaczej odrzuć |
| przeciągnięcie karty na planszę | zagraj kartę, z podglądem trasy |
| PPM na karcie z ręki | oznacz do włożenia w Mody Patusa, potem LPM na slot |
| środkowy przycisk na karcie | odrzuć bez zagrywania |
| PPM na slocie modów | odrzuć mod |
| najechanie na wieżę pionków | rozsuwa stos, każdy pionek osobno klikalny |
| najechanie na zagraną kartę | powiększa ją nad całym interfejsem |
| przeciągnięcie pionka | ruch ręczny, z przyciąganiem do pola |
| przeciągnięcie pustego terenu | przesuwanie planszy |
| LPM na kafelku gracza | ustaw aktywnego gracza |
| LPM na ołówku | zmiana nazwy |
| LPM na kółku koloru | skreśl kolor (tylko łowcy) |
| Ctrl + kółko | zoom, środkowy przycisk — przesuwanie mapy |
| `S` | włącz/wyłącz przyciąganie do pól |
| `F` | dopasuj widok do planszy |
| `Tab` | następny gracz |
| przycisk „Zakończ turę" | kończy turę, dobiera karty, przekazuje ruch |
| `Esc` | anuluj / wyjdź |

## Układ ekranu

Plansza jest teraz środkiem aplikacji. Po lewej aktywne Mody Patusa i trzy
talie, w środku pasek kolejności tur, lista graczy i plansza, po prawej postać
z umiejętnością i siatka „Kolory Piotrka", na dole ręka w formie wachlarza.
Interfejs liczy się z rozmiaru okna — od 1280×760 po 4K — więc większy monitor
oznacza większą planszę, a nie większe piksele.

## Pola podwójne (12a / 12b)

Poszerzony odcinek drogi to **jedna pozycja z dwoma polami** — `12a` i `12b` są
tak samo daleko od startu, więc ruch liczy je raz. Wejście na taką pozycję
zatrzymuje ruch: oba pola pulsują, a partia czeka, aż gracz wskaże, na którym
staje pionek. Przejście *przez* poszerzony odcinek nie pyta o nic — pionek idzie
bliższą połową.

Wybór jedzie w komendzie razem z zagraniem, więc odtworzy się identycznie u
zdalnego gracza, a silnik go sprawdza — klient nie wyląduje gdzie indziej.
Częstotliwość pól podwójnych ustawia się w menu, w `board.json` albo z linii
poleceń (`--doubles 40`).

## Zagrywanie kart i umiejętności

Silnik efektów jest **generyczny**: karta albo umiejętność deklaruje efekt w
JSON-ie, zarejestrowany handler zamienia go w plan operacji, a wykonawca go
stosuje. Nigdzie nie ma rozgałęzień po tytułach kart — nowa karta to zwykle
sam wpis w JSON-ie.

Gdy efekt potrzebuje decyzji (który pionek? o ile pól? 12a czy 12b?), silnik
**pyta**, a interfejs rysuje to, co dostał. Przy wyborze pionka pokazuje same
pionki — kolorowe żetony, nie przyciski z nazwami — i podświetla je na planszy,
więc można kliknąć jedno albo drugie. Ten sam mechanizm obsługuje karty i
umiejętności. Odpowiedź jedzie razem z akcją, więc odtworzy się identycznie u
zdalnego gracza.

Grywalne jest 26 z 30 definicji kart ruchu. Cztery pozostałe (Troll, Stańczyk,
Spy, Plagiat!) czekają na mechaniki, których jeszcze nie ma.

Umiejętności postaci działają i mają licznik użyć wzięty z pola `uses` w
JSON-ie (opis dalej mówi „2x" po ludzku). Zaimplementowane: Big D Randy, Lubin,
Mitoman, Norbur, Dziad, Ondrej, Dziubdziuch, Atencjusz. Glockboy i Ice Block
czekają na mechanikę sprawdzania i mówią o tym wprost, nie zużywając ładunku.

Piotrek dostaje umiejętność na starcie. ChatGPT zabiera mu dwie karty ruchu i
jedno miejsce na Kartę Skrzyni, a w zamian pięć razy wydłuża zasięg następnej
karty ruchu o jedno pole.

## Tryb edycji

W ustawieniach jest przełącznik **Tryb edycji**. Włączony (domyślnie) pozwala
grać za wszystkich z jednego komputera — tak jak dotąd. Wyłączony zachowuje się
tak, jak będzie działać gra sieciowa: sterujesz tylko swoim miejscem, a silnik
odrzuca każdą komendę wydaną za kogoś innego. Zasada siedzi w silniku
(`may_control`), nie w interfejsie, bo to host ma o tym decydować.

## Oprawa graficzna

Interfejs jest zbudowany według dostarczonej koncepcji: prawie czarny stół z
lekko chłodnym rozmyciem, panele z ciemnego łupku obrzeżone przetartym mosiądzem
z narożnymi okuciami, pergaminowe karty w podwójnej ramce, wersaliki z rozstrzelonymi
literami na nagłówkach i jeden zielony akcent na to, co „twoje" i „teraz".

Wszystkie kolory siedzą w `config/theme.py` — poza nim nie ma ani jednej wartości
RGB (pilnuje tego test). Kolejna skórka to drugi obiekt `Theme`, nie przeszukiwanie
kodu. Wspólne elementy (`premium_panel`, `inset_well`, `section_heading`,
`spaced_text`, `circle_button`) są w rendererze, więc każdy panel w grze wygląda
jak z tej samej aplikacji.

## Ostrość i skalowanie

Nic, co zostało już narysowane, nie jest powiększane. Karta rosnąca pod kursorem
albo w podglądzie „Ostatnio zagrane" jest **rysowana od nowa w docelowym
rozmiarze** — tekst układa się ponownie, więc powiększona karta staje się
ostrzejsza, a nie bardziej rozmyta. Czcionki skalują się z wysokością okna i są
renderowane w tym rozmiarze, więc 1920×1200 czy 2560×1440 dostaje większe znaki,
a nie rozciągniętą bitmapę.

Szerokość paneli bocznych jest **mierzona z ich zawartości**: karty dobierają
rozmiar do dostępnej wysokości, kolumna jest dokładnie tak szeroka, żeby je
pomieścić, a cała reszta idzie do planszy (60–76% szerokości okna, zależnie od
rozdzielczości).

## Przebieg tury

Tura to „rozegraj jedną kartę ruchu". Po zagraniu (albo odrzuceniu — tak się
pasuje, gdy nic nie da się legalnie zagrać) ręka **sama** uzupełnia się do
właściwego rozmiaru, tura przechodzi dalej, a gdy skończą się miejsca w rundzie,
runda rośnie i wyznaczony łowca dostaje Kartę Skrzyni. Wszystko dzieje się w
jednej komendzie, więc u każdego gracza odtwarza się identycznie.

Każda maszyna pokazuje **swoją** rękę, niezależnie od tego, czyja jest tura.
Podglądanie cudzych kart jest możliwe tylko w trybie edycji albo w wersji
testowej — i nigdy nie oznacza możliwości grania nimi. Powrót do swojego gracza:
klawisz **Home** albo przycisk nad planszą.

## Stany gry

Zamrożenie, sklejenie pionków, dodatkowy ruch, bonus do zasięgu, ograniczenie
ruchu — wszystko to są **statusy** przypięte do pionka, gracza albo stołu,
wygasające po zadanej liczbie tur. Żadna mechanika nie dokłada własnej flagi,
więc wygasanie, wyświetlanie i zapis do migawki są napisane raz.

## Plansza

Zamiast siatki kwadratów plansza jest wijącą się, **poziomą** drogą generowaną
proceduralnie: krzywa Catmulla-Roma z parametryzacją po długości łuku (dzięki
temu pola nie zbijają się w zakrętach), rzeki z mostami dokładnie tam, gdzie
przecinają trakt, wzgórza, lasy, skały i wioski przy rozstajach.

Generator **gwarantuje**, że pola nigdy się nie nachodzą. Ograniczenie promienia
skrętu jest wyprowadzone z tego, o ile łuk ściska wewnętrzny pas, a gotowa trasa
jest jeszcze mierzona; gdy warunek nie jest spełniony, amplituda spada o 12% i
plansza powstaje ponownie. Sprawdzone na 120+ planszach — zero naruszeń.

Układ pól — pojedyncze i podwójne rzędy — jest przeniesiony 1:1 z prototypu,
razem z regułą, że meta zawsze jest polem pojedynczym.

Cała statyczna sceneria malowana jest **raz** do jednej powierzchni; co klatkę
przerysowywany jest tylko widoczny wycinek. Koszt rysowania zależy więc od
rozmiaru okna, a nie od tego, czy plansza ma 20 czy 200 pól.

## Dodawanie treści bez dotykania kodu

Nowa karta to jeden wpis w `data/cards.json` (tytuł, tekst, liczba kopii,
opcjonalna ścieżka do obrazka, opcjonalna odznaka). Nowa postać — wpis w
`data/characters.json`. Nowy motyw planszy — wpis w `data/board.json`.
Szczegóły i format obrazków: `assets/README.md`.

Pliki są walidowane przy starcie: literówka w nazwie pionka zatrzyma grę z
czytelnym komunikatem, zamiast wysypać się pół godziny później.

## Sieć

Warstwa `net/` jest gotowa i przetestowana, ale interfejsu „Host / Dołącz”
jeszcze nie ma — zgodnie z ustaleniem multiplayer to następny krok.

Model: **autorytatywny host + deterministyczny lockstep**. Klient wysyła
komendę, host ją waliduje i rozsyła; wszyscy stosują te same komendy w tej samej
kolejności. Wspólne ziarno losowości w `SessionConfig` sprawia, że plansza i
potasowane talie powstają identycznie u każdego, więc po sieci lecą dziesiątki
bajtów na akcję, a nie cały stan gry.

Działa to już dziś — `tests/test_session.py` uruchamia hosta i klienta na
transporcie w pamięci i sprawdza, że po serii akcji ich stany są identyczne, co
do karty. Do prawdziwej gry przez internet brakuje trzech rzeczy: transportu na
gniazdach TCP (interfejs `Transport` czeka), ekranu lobby i filtrowania
ukrytych informacji (`Player` już rozdziela `to_public_dict` / `to_private_dict`).

## Testy

```bash
pip install -r requirements-dev.txt
python -m pytest -q            # 422 testy, ok. 92 s
python -m pedzacy_piotrek --selftest    # kilka klatek bez okna
python tools/screenshot.py --cells 34   # podgląd planszy do PNG
python tools/inspect_frame.py --window 2560x1440   # kontrola układu ekranu
```

## Argumenty wiersza poleceń

```bash
python run_game.py --players 4 --cells 40 --chest 3   # pomiń menu
python run_game.py --seed 12345                       # powtarzalna partia
python run_game.py --size 1600x900 --fullscreen       # rozmiar okna
python run_game.py --doubles 40                       # co ile pola podwójne
```

## Dalsze kroki

0. Filtrowanie ukrytych informacji po stronie hosta — dziś klient symuluje pełny
   stan, więc zmodyfikowany klient mógłby podejrzeć cudzą rękę. Wśród znajomych
   na niezmienionym kliencie to nie problem, ale przed publicznym wydaniem tak.
1. Mechanika sprawdzania pionków — czeka na nią kilka umiejętności (Glockboy,
   Ice Block) i warunki zwycięstwa.
2. Efekty pozostałych kart ruchu, Modów Patusa i Skrzyni — w większości sam
   wpis w JSON-ie.
3. Transport TCP + ekran lobby (Host / Dołącz) + filtrowanie ukrytych informacji.
4. Efekty kart Skrzyni i Modów Patusa.
5. Grafiki i dźwięk — miejsca gotowe, kod korzysta z nich, gdy pliki się pojawią.
6. Budowa `.exe` przez PyInstaller.

## Pamięć projektu

`LLM_Instructions.txt` i `CHANGELOG_LLM.md` opisują projekt na tyle dokładnie,
żeby dowolny model mógł kontynuować pracę w nowej rozmowie, czytając tylko te
dwa pliki i kod. Po każdym etapie trzeba je zaktualizować.
