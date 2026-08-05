# Architektura

Dokument opisuje *dlaczego* projekt wygląda tak, jak wygląda. Jeśli za pół roku
któraś decyzja wyda się dziwna, odpowiedź powinna być tutaj.

## 1. Warstwy

```
    ui/  ──────────►  engine/commands  ──────────►  engine/game_state
     ▲                                                     │
     │                                                     ▼
     └──────────────  engine/events  ◄───────────────────  ┘
```

Ruch informacji jest jednokierunkowy:

* interfejs **nie zmienia** stanu — tworzy komendę i oddaje ją sesji,
* stan gry stosuje komendę i zwraca listę zdarzeń,
* interfejs reaguje na zdarzenia (animacja, dźwięk, komunikat).

Konsekwencja praktyczna: `engine/` nie importuje pygame w żadnym pliku. Reguły
da się testować w milisekundach, a przejście na sieć nie wymaga ich ruszania.

**Dlaczego komendy, a nie wywołania metod?** Bo komenda jest obiektem: da się ją
zserializować, wysłać, zapisać, powtórzyć i cofnąć. Prototyp zmieniał stan
bezpośrednio w obsłudze kliknięcia, przez co „co się właśnie stało” istniało
tylko przez jedną instrukcję. Teraz historia partii to lista komend — stąd za
darmo dostajemy synchronizację po zerwaniu połączenia (serwer odtwarza log
wracającemu graczowi) i podstawę pod zapis/powtórki.

## 2. Ziarno losowości zamiast synchronizacji stanu

`SessionConfig` zawiera `seed`. Z niego powstaje plansza, tasowanie każdej talii
i rozdanie postaci. Dwie maszyny z tym samym ziarnem zbudują identyczną partię,
zanim padnie jakikolwiek pakiet.

Dlatego przez sieć wystarczy przesyłać komendy, a nie stan. Alternatywa —
rozsyłanie pełnych migawek — działałaby, ale wymagałaby ~100 kB na akcję zamiast
kilkudziesięciu bajtów i dużo trudniej byłoby debugować rozjazdy.

Serwer dokłada do każdej zatwierdzonej komendy **ośmioznakową sumę kontrolną**
swojego stanu. Klient liczy swoją po zastosowaniu i porównuje: rozjazd wychodzi
na akcji, która go spowodowała, a nie dwadzieścia minut później. Reakcja jest
jedna i tania — poproś o pełną synchronizację i odbuduj partię z ziarna i logu.

Każda talia dostaje **własny** generator (`random.Random(seed + n)`). Gdyby
wszystkie korzystały ze wspólnego, dodanie jednej karty ruchu zmieniłoby
tasowanie skrzyni — a tego przy testowaniu balansu nikt nie chce.

## 3. Plansza generowana, nie zapisana

`BoardModel.generate(cell_count, seed)` buduje wszystko: krzywą trasy, pola,
rzeki, mosty, wzgórza, drzewa, skały i wioski. Plansza serializuje się do
czterech liczb.

Kluczowy szczegół: **parametryzacja po długości łuku**. Naiwne rozstawienie pól
po parametrze krzywej ściska je w zakrętach i rozciąga na prostych. `Path2D`
buduje tablicę długości i interpoluje po odległości, więc odstęp między polami
jest stały niezależnie od tego, jak kręta jest droga.

Długość trasy jest dopasowywana iteracyjnie tak, żeby liczba pól *dokładnie*
zgadzała się z wybraną w menu — plansza nie „mniej więcej” ma 40 pól.

Sceneria rozstawiana jest metodą odrzucania z siatką przestrzenną
(`ProximityGrid`), która utrzymuje odstęp od drogi, wody, obozu i banera mety.
Bez siatki byłoby to O(n²) i przy 400 obiektach zauważalnie zwalniałoby start.

### 3a. Gwarancja odstępów między polami

Pierwsza wersja generatora potrafiła zbić sąsiednie pola niemal na siebie. Nie
była to wina „za dużej losowości" — nic nie ograniczało krzywizny drogi.

Ograniczenie jest wyprowadzone, nie dobrane ręcznie. Na łuku o promieniu `R`
wewnętrzny pas jest ściskany w stosunku `(R - lane_offset)/R`; żądanie, by
ściśnięty odstęp nie spadł poniżej `2·tile_radius + min_tile_gap`, daje wprost
minimalny promień skrętu. Dla sinusoidy o amplitudzie `A` najciaśniejszy promień
to `L²/(4π²A)`, więc długość fali dobierana jest **z** amplitudy, a nie odwrotnie.

Ponieważ krzywa Catmulla-Roma poprowadzona przez punkty kontrolne sinusoidy
potrafi lekko przestrzelić ideał, gotowa trasa jest jeszcze **mierzona**:
promień krzywizny z okręgu opisanego na trójkach próbek oraz najmniejsze
zbliżenie trasy do samej siebie. Przy naruszeniu amplituda spada o 12% i plansza
powstaje ponownie; amplituda zero to prosta droga, więc pętla zawsze się kończy.

`BoardModel.verify_spacing()` zwraca `None` albo opis najgorszej pary pól. Testy
wołają je dla dziesiątek kombinacji rozmiaru i ziarna.

### 3b. Plansza pozioma

Generator pracuje we współrzędnych *wzdłuż/w poprzek*, a `_to_world()` mapuje je
na płótno. Powrót do planszy pionowej to jedna wartość w
`BoardLayout.orientation`, a nie przepisanie generatora. Rzeki płyną w poprzek
traktu, dzięki czemu mosty zawsze wypadają prostopadle do drogi.

## 4. Rysowanie

* **Statyczna sceneria malowana raz.** `BoardRenderer.build()` tworzy jedną
  powierzchnię wielkości świata; co klatkę kopiowany jest tylko widoczny
  fragment. Koszt zależy od okna, nie od rozmiaru planszy.
* **Rozdzielczość natywna.** Gra rysuje bezpośrednio w rozmiarze okna, a
  `Layout` przelicza się przy każdej zmianie rozmiaru. Wcześniejsze skalowanie
  stałego płótna zachowywało proporcje, ale na dużym monitorze dawało rozmytą
  powiększoną kliszę i nie dokładało planszy ani piksela powierzchni.
* **Karty malowane w docelowym rozmiarze** i keszowane po (treść, rozmiar,
  stan). Małe karty w panelach są więc ostre, a wachlarz obraca gotową
  powierzchnię jednym `rotozoom` zamiast składać tekst co klatkę.
* **Cache.** Teksty, cienie i poświaty są keszowane po kluczu — bez tego
  renderowanie tekstu co klatkę potrafi kosztować więcej niż cała reszta.
* **Kamera** trzyma osobno wartość bieżącą i docelową; zoom i przesuwanie
  dochodzą do celu wykładniczo, niezależnie od liczby klatek.

Cały układ ekranu jest w `ui/layout.py`. W prototypie ~100 stałych modułowych
opisywało prostokąty, a ta sama geometria bywała liczona dwa razy w różnych
miejscach — wystarczyło poprawić jedno, żeby przycisk przestał odpowiadać
swojemu obrazkowi. Teraz prostokąt ma jedno źródło prawdy, a testy interfejsu
klikają dokładnie tam, gdzie rysuje panel.

## 5. Ukryte informacje

`Player` od początku ma `to_public_dict()` i `to_private_dict()`. Dziś, przy
grze na jednym komputerze, nie ma to znaczenia — ale cała gra opiera się na tym,
że tylko Piotrek zna swój kolor. Gdyby ten podział dokładać później, trzeba by
przejrzeć każde miejsce wysyłające dane. Teraz wystarczy, żeby host wybrał
właściwą metodę.

Uczciwe zastrzeżenie, które **nadal obowiązuje**: klient odtwarza pełny stan z
ziarna i logu komend, więc technicznie mógłby podejrzeć cudzą rękę. Serwer
sprawdza *uprawnienia* (czyje miejsce, czyja tura) i to działa, ale nie filtruje
jeszcze tego, co rozsyła. Dopóki grają znajomi na niezmodyfikowanym kliencie, to
nie problem; przed publicznym wydaniem serwer musi zacząć wysyłać ukryte
informacje tylko ich właścicielom — `to_public_dict()` / `to_private_dict()` są
po to gotowe.

## 6. Treść w JSON-ie

Karty, postacie i motywy plansz są danymi, nie kodem. Powód jest wprost z
założeń projektu: gra powstaje po to, żeby *testować balans*, a to znaczy setki
drobnych zmian w kartach. Każda z nich wymagająca edycji Pythona to zmiana,
której się nie zrobi.

Odznaki na kartach (kolorowy pionek, strzałka, znak +/−) też są danymi.
Prototyp wnioskował je z tytułu karty przez dopasowywanie tekstu — działało,
dopóki ktoś nie nazwał karty inaczej.

## 7. Czego celowo nie ma

* **Automatycznego wykonywania efektów kart.** Karty opisują, co robią; ruch
  wykonuje gracz. Tak było w prototypie i tak ma zostać, dopóki nie ustabilizuje
  się balans — inaczej każda zmiana treści karty byłaby zmianą kodu.
* **Warunków zwycięstwa.** Reguły są jeszcze testowane; wymuszony koniec partii
  przeszkadzałby w testowaniu.
* **Kont i matchmakingu.** Pokój znajduje się po sześcioznakowym kodzie. Konta
  wymagałyby bazy, haseł i polityki prywatności, żeby rozwiązać problem, którego
  grupa znajomych nie ma.
* **Głosu i czatu.** Ludzie i tak rozmawiają na Discordzie.
* **Przewidywania po stronie klienta.** Opisane w §10.


## 8. Klient i serwer

Serwer jest **dedykowanym procesem**, nie jednym z graczy. To jedyna
architektura, która spełnia postawiony wymóg — gra z osobą w innym kraju bez
konfiguracji routera. Komputer za NAT-em nie przyjmuje połączeń przychodzących,
więc gracz nie może być serwerem dla kogoś z zewnątrz; wszyscy muszą łączyć się
**na zewnątrz** do maszyny z publicznym adresem. Żadna sztuczka po stronie
klienta tego nie obchodzi.

```
   ui/  ──►  net/service  ──►  net/client  ──►  net/transport
                                                      │
                                                   WebSocket
                                                      │
   server/app (asyncio)  ──►  server/hub  ──►  server/room  ──►  engine/
```

Podział, na którym to stoi: **`server/hub.py`, `room.py` i `registry.py` nie
znają gniazd ani asyncio**. Serwer to funkcja z (połączenie, wiadomość) na listę
(połączenie, wiadomość). Kod asynchroniczny jest najtrudniejszy do testowania,
więc ma go być jak najmniej i ma nie zawierać żadnej reguły — `server/app.py` to
dwieście linijek przenoszenia bajtów. Dzięki temu całe multiplayer (miejsca,
kolejność, rozłączenia, okresy karencji, synchronizacja) testuje się w
milisekundach, bez sieci.

**Dwie tożsamości, i to nie to samo:** *połączenie* to jedno gniazdo i umiera
razem z WiFi; *gracz* przeżywa rozłączenie. Łączy je **token wznowienia**
wydawany przy pierwszym powitaniu. Bez tego rozdziału „powrót do gry” może
znaczyć tylko „dołączenie od nowa jako ktoś obcy” — tak działała poprzednia
wersja i dlatego rozłączony gracz tracił rękę.

**Dlaczego WebSockety.** Skoro i tak potrzebny jest publiczny serwer, liczy się
transport, który przeżyje drogę: jedno długie połączenie TCP z ramkowaniem
wiadomości, rozumiane przez każdą platformę hostingową i każde firmowe proxy,
szyfrowane zmianą jednej litery w adresie (`ws://` → `wss://`) i nieblokowane
przez reguły przepuszczające tylko ruch HTTP — w przeciwieństwie do surowego
gniazda na porcie 51337.

**Dlaczego wątek.** `websockets` jest asynchroniczne, a pętla pygame nie. Zamiast
zarażać grę `async`/`await`, pętla zdarzeń chodzi w wątku demona i rozmawia z
grą przez dwie kolejki. `poll()` nigdy nie czeka, więc interfejs nie zamarza,
gdy zamarza sieć — czyli dokładnie wtedy, kiedy gracz najbardziej chce, żeby
rysował dalej.

## 9. Zagrywanie kart

Efekt karty jest **czystą funkcją** stanu: `engine/effects.py` zamienia kartę w
`Plan` (trasy pionków) albo w `Refusal` z powodem. Interfejs woła `preview()`
podczas przeciągania, a silnik `resolve()` przy zagrywaniu — to ta sama funkcja,
więc podświetlona trasa nie może obiecać czegoś innego niż to, co się stanie.

Komenda `PlayCard` niesie identyfikator karty, nie wynik. Cel wyznacza silnik.
Gdy dojdzie sieć, klient będzie mógł powiedzieć *którą* kartę zagrał, ale nie
*co ona robi*.

Ruch jest opisany listą pól, przez które pionek przechodzi (`TokenWalked`), a
nie parą „skąd–dokąd". Dzięki temu widok przechodzi pole po polu z podskokiem na
każdym, zamiast przesuwać pionek po skosie — i dokładnie ta sama lista wystarczy,
by odtworzyć ruch u zdalnego gracza.

## 10. Wachlarz kart

Karty leżą na okręgu, którego środek jest daleko pod ekranem — stąd płaski,
czytelny łuk zamiast koła kart. Trafienie w kartę sprawdzane jest po obróceniu
kursora do układu karty, więc obszar klikalny podąża za pochyleniem; przy
nachodzących się rogach ma to realne znaczenie.

Animacja to wykładnicze dążenie do celu (`approach`), niezależne od liczby
klatek: karta ma pozycję bieżącą i docelową, a nie skrypt. Dzięki temu zmiana
ręki w trakcie ruchu niczego nie psuje — cele się po prostu przeliczają.


## 11. Pola i pozycje

To rozróżnienie jest w kodzie wszędzie, więc warto je zapamiętać: **pole** to
kółko na planszy, **pozycja** to krok wzdłuż drogi. Poszerzony odcinek ma dwa
pola na jednej pozycji (`12a`, `12b`) i ruch liczy go raz.

Dlaczego to nie jest tylko etykieta: na którym polu stoi pionek decyduje, kto
stanie na kim w wieży, a wieże są sposobem, w jaki łowcy wygrywają. Silnik
odmawia więc wykonania ruchu, dopóki nie dostanie odpowiedzi, zamiast wybrać
połowę za gracza. Pozycje pośrednie są wybierane automatycznie (bliższa połowa),
bo od nich nic nie zależy.

Odpowiedź podróżuje w polu `target_tile` tej samej komendy `PlayCard`, więc
decyzja jest częścią akcji i odtworzy się identycznie u każdego gracza. Silnik
sprawdza ją względem pól pozycji docelowej — klient może powiedzieć *którą*
połowę wybiera, ale nie może wskazać pola po drugiej stronie planszy.

## 12. Tryb modalny w interfejsie

Oczekiwanie na wybór 12a/12b to pierwszy stan modalny w grze i wzorzec dla
następnych (karty wymagające wskazania pionka pójdą tak samo): stan wchodzi z
**zdarzenia silnika**, a nie z kliknięcia, które je wywołało. Dzięki temu ten sam
mechanizm zadziała, gdy zdarzenie przyjdzie od hosta, a nie od lokalnej myszy.

Modalność jest wybiórcza: kliknięcia odpowiadają na pytanie, kółko i przeciąganie
środkowym przyciskiem nadal działają (trzeba móc rozejrzeć się po planszy), a
reszta interfejsu jest zablokowana. Esc i prawy przycisk anulują zagranie i karta
wraca na rękę.
