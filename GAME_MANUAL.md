# Pędzący Piotrek — Instrukcja gry

**Ostatnia aktualizacja:** stan gry po etapie 50.

---

## Zasada aktualności instrukcji

Ten dokument opisuje **aktualną wersję rozgrywki** — to, co gra naprawdę robi
dzisiaj, a nie to, co kiedyś planowano.

**Dla osób rozwijających grę (ludzi i modeli):** `GAME_MANUAL.md` jest
nadrzędnym dokumentem opisującym zasady dla gracza. Jeżeli jakakolwiek zmiana:

- dodaje kartę lub ją usuwa,
- zmienia działanie karty,
- zmienia umiejętność postaci,
- zmienia Mod Patusa,
- zmienia głosowanie,
- zmienia mechanikę skrzyni,
- zmienia kolejność tur,
- zmienia ruch,
- zmienia warunki zwycięstwa,
- zmienia sprawdzanie tożsamości,
- dodaje lub usuwa jakąkolwiek mechanikę widoczną dla gracza,

to **ten plik musi zostać przejrzany i zaktualizowany w ramach tej samej
zmiany**. Nie jest to dokumentacja opcjonalna. Zaktualizuj też datę na górze.

Miejsca oznaczone **⚠ DO POTWIERDZENIA** to zasady, których gra celowo jeszcze
nie rozstrzyga — nie zmyślaj ich, tylko ustal je przy stole.

---

## 1. Wprowadzenie

**Pędzący Piotrek** to gra o ukrytej tożsamości połączona z wyścigiem pionków po
planszy.

Na planszy stoi **sześć pionków** w kolorach: czerwony, zielony, niebieski,
żółty, różowy i pomarańczowy. Pionki **nie należą do graczy** — każdy gracz może
poruszyć dowolny pionek, jeśli tylko ma odpowiednią kartę.

Jeden z graczy jest **Piotrkiem**. Na początku gry Piotrek w tajemnicy wybiera
sobie **jeden kolor pionka** — to jest jego ukryta tożsamość. Wszyscy pozostali
gracze są **Oprawcami** (Hunterami).

- **Piotrek** chce doprowadzić swój pionek do mety.
- **Oprawcy** chcą zgadnąć, który pionek jest Piotrkiem, i go złapać.

Ponieważ nikt oprócz Piotrka nie wie, który pionek jest „jego", każdy ruch jest
informacją. Popychanie jednego pionka do przodu może być pomocą dla Piotrka albo
pułapką zastawioną przez Oprawcę.

### Podstawowe pojęcia

| Pojęcie | Znaczenie |
|---|---|
| **Pionek** | Jeden z sześciu kolorowych pionków na planszy. Niczyj. |
| **Pole / pozycja** | Jeden krok na planszy. Meta ma numer równy liczbie pól. |
| **Wieża** | Kilka pionków stojących na jednym polu, jeden na drugim. |
| **Karta Ruchu** | Podstawowa karta z ręki — nią gra się swoją turę. |
| **Karta Skrzyni** | Rzadka, mocna karta rozdawana od ustalonej rundy. |
| **Mod Patusa** | Zasada zmieniająca całą grę, aktywna dopóki leży na stojaku. |
| **Umiejętność** | Zdolność twojej postaci, o ograniczonej liczbie użyć. |
| **Check (sprawdzenie)** | Próba wykrycia, który pionek należy do Piotrka. |

---

## 2. Cel gry

### Piotrek wygrywa, gdy

- jego **ukryty pionek dojdzie do mety** (ostatniego pola planszy).

W ustawieniach można włączyć **wariant 2 zwycięstwa**, w którym Piotrkowi
wystarczy, że **dowolny pionek** dojdzie do mety. Nawet wtedy ujawniony przy
zwycięstwie kolor to prawdziwy kolor Piotrka.

| Wariant | Nazwa w ustawieniach | Zasada |
|---|---|---|
| 1 (domyślny) | wygrywa pionek Piotrka | Do mety musi dojść ukryty pionek Piotrka |
| 2 | wygrywa dowolny pionek | Do mety wystarczy, że dojdzie którykolwiek pionek |

### Oprawcy wygrywają, gdy

- **wszystkie pionki stoją na jednym polu**, a **na samym dole tej wieży** stoi
  pionek Piotrka.

To jest sedno gry Oprawców: nie wystarczy zbudować wieżę — musi ona mieć na
spodzie właściwy kolor. Kolejność pionków w wieży ma więc znaczenie zasadnicze.

Oprawcy mogą też wygrać natychmiast, jeśli **Glockboy** trafnie wskaże pionek
Piotrka swoją umiejętnością (patrz rozdział 12).

### Co się dzieje przy nietrafionej próbie

Jeżeli wszystkie pionki staną na jednym polu, a na spodzie **nie** ma Piotrka,
to ten kolor zostaje **skreślony** — u wszystkich Oprawców naraz, w notatniku
„Kolory Piotrka". Gra toczy się dalej.

**Każdy kolor sprawdzany jest tylko raz.** Ta sama wieża może stać w nieskończoność
i nic więcej się nie wydarzy — żeby dowiedzieć się czegoś nowego, Oprawcy muszą
przestawić pionki.

---

## 3. Przygotowanie gry

### Liczba graczy

**3–6 graczy.** (Tryb testowy pozwala na 2, ale nie jest to normalna rozgrywka.)

### Rozdanie postaci

Każdy gracz dostaje **kartę postaci**. Gra gwarantuje, że **dokładnie jeden
gracz dostanie kartę Piotrka**. Jeżeli w menu wszyscy wybrali postacie ręcznie i
nikt nie wybrał Piotrka, gra odmówi startu i powie o tym na czerwono.

### Umiejętność Piotrka

Gracz z kartą Piotrka **dobiera dodatkowo jedną z trzech Umiejętności Piotrka**
(ChatGPT, Ice Block, Dług u Tomasza) z osobnej talii. Dostaje ją na samym
początku, bo niektóre z nich zmieniają liczbę kart startowych.

### Tajna tożsamość

Piotrek — i tylko on — wybiera prywatnie **kolor pionka**, którym będzie.
Do czasu wybrania koloru nikt nie może wykonać ruchu.

- Ten kolor **nigdy nie jest pokazywany innym graczom** aż do końca gry.
- Piotrek widzi przypomnienie o swoim kolorze w prawym panelu.
- **To, kto jest Piotrkiem, jest jawne** (widać go w pasku kolejności tur).
  Tajny jest wyłącznie **kolor**.

### Karty startowe

| Gracz | Karty Ruchu na start |
|---|---|
| Piotrek | **5** |
| Piotrek z umiejętnością ChatGPT | **3** (ChatGPT odbiera dwie karty) |
| Każdy Oprawca | **3** |

Maksymalna wielkość ręki Kart Ruchu to **8**.

Karty **Troll** i **Stańczyk** nigdy nie trafiają do ręki startowej — wracają do
talii, która jest po rozdaniu przetasowana.

### Plansza

Plansza jest generowana proceduralnie: droga, rzeki z mostkami, wioski, las,
obóz startowy i baner mety.

- Domyślna długość: **24 pola**. Liczba ustawiona w menu to **numer, na którym
  stoi meta** — zawsze.
- Część rzędów jest **poszerzona** i mieści dwa pola o tym samym numerze:
  **12a** i **12b**. Oba są dwanaście kroków od startu.
- Częstotliwość poszerzonych rzędów jest ustawialna. Zmienia ona liczbę *pól*,
  nigdy długości planszy.
- Ostatni rząd nigdy nie jest podwójny — meta to zawsze jedno pole.

Wszystkie sześć pionków zaczyna w **obozie startowym** przed polem 1.

### Talie

| Talia | Liczba kart (domyślnie) |
|---|---|
| Karty Ruchu | 72 |
| Karty Skrzyni | 18 |
| Mody Patusa | 13 |
| Karty Postaci | 10 |
| Umiejętności Piotrka | 3 |

Talia wyczerpana jest przetasowywana ze stosu odrzuconych.

### Ustawienia wpływające na rozgrywkę

Wszystkie ustawia się przed grą (a skład talii także w Bibliotece Kart w trakcie):

| Ustawienie | Domyślnie | Znaczenie |
|---|---|---|
| Liczba pól planszy | 24 | Numer pola mety |
| Pola podwójne | 30% | Ile rzędów jest poszerzonych |
| Runda otwarcia skrzyni | 6 | Od której rundy rozdawane są Karty Skrzyni |
| Pierwsza runda Modów | 3 | Kiedy pierwszy wybór Modów Patusa |
| Co ile rund Mody | 2 | Odstęp między wyborami Modów |
| Czas na blokadę ruchu | 7 s | Okno decyzji dla „Nie masz Rosji" |
| Czas na Ice Block | 10 s | Okno decyzji Piotrka przy sprawdzeniu |
| Wariant sprawdzania | 1 | Czy nieudany check rozbija wieżę |
| Wariant zwycięstwa | 1 | Czy Piotrek musi dojść własnym pionkiem |
| Skład talii | jak wydrukowano | Liczba kopii każdej karty |
| Ładunki umiejętności | jak wydrukowano | Liczba użyć każdej umiejętności |
| Warianty kart | wariant 1 | Które brzmienie kart obowiązuje |

---

## 4. Role i tożsamości

### Piotrek

- Jest **jeden**.
- Wie, który kolor pionka jest jego.
- Gra **co trzecią turę** (patrz rozdział 5) — ma więc znacznie więcej ruchów
  niż pojedynczy Oprawca.
- Trzyma **2 Karty Skrzyni** (Oprawca tylko 1); umiejętność ChatGPT obniża to
  do 1.
- Posiada dodatkowo **Umiejętność Piotrka** — kartę, której nie ma nikt inny.

### Oprawcy

- Wszyscy pozostali gracze.
- **Nie wiedzą**, który pionek jest Piotrkiem — muszą to wywnioskować.
- Każdy ma własną **umiejętność postaci** o ograniczonej liczbie użyć.
- Wspólny notatnik „Kolory Piotrka" pokazuje **skreślone kolory**. Notatnik
  wypełnia się sam po nieudanym sprawdzeniu i jest identyczny u wszystkich
  Oprawców — nie da się w nim niczego zaznaczyć ręcznie.

### Co jest jawne, a co tajne

| Informacja | Jawna? |
|---|---|
| Kto jest Piotrkiem | **Tak** |
| Który kolor jest Piotrkiem | **Nie** — wyłącznie on |
| Skreślone kolory | Tak, dla wszystkich |
| Pozycje wszystkich pionków | Tak |
| Aktywne Mody Patusa | Tak |
| Czyja jest tura, ile kto ma kart | Tak |
| Zawartość ręki (konkretne karty) | Nie |
| Karty Skrzyni w rękach graczy | Nie — chyba że działa **Paczka** |
| Umiejętność Piotrka, którą wylosował | Nie |

---

## 5. Przebieg gry i kolejność tur

### To nie jest zwykła kolejność „w kółko"

**Piotrek gra co trzecią turę.** Oprawcy krążą w ustalonej kolejności między
jego turami:

```
Piotrek → Oprawca 1 → Oprawca 2 → Piotrek → Oprawca 3 → Oprawca 1 → Piotrek → ...
```

**Runda kończy się w chwili, gdy każdy Oprawca zagrał w niej przynajmniej raz.**

Z tego wynikają dwie rzeczy, które łatwo przeoczyć:

- **Rundy nie są równej długości.**
- **Ten sam Oprawca może zagrać dwa razy w jednej rundzie**, zanim inny zagra
  raz.

Pierwszą turę **całej gry** zaczyna **Piotrek**.

**Kolejne rundy nie muszą zaczynać się od Piotrka.** Rytm „co trzecia tura" biegnie
nieprzerwanie przez całą grę i nie zeruje się na granicy rundy — runda po prostu
kończy się tam, gdzie wszyscy Oprawcy już zagrali. Przy trzech Oprawcach wygląda
to tak:

```
Runda 1:  Piotrek → Oprawca 1 → Oprawca 2 → Piotrek → Oprawca 3
Runda 2:  Oprawca 1 → Piotrek → Oprawca 2 → Oprawca 3
```

Pasek na górze ekranu pokazuje pełną kolejność bieżącej rundy z numerami slotów —
zawsze warto na niego spojrzeć zamiast liczyć w pamięci.

### Przebieg pojedynczej tury

1. **Efekty na starcie tury.** Jeżeli twoja tura jest pominięta (Stańczyk,
   Jazdy) albo przejęta (Troll) — dzieje się to teraz, automatycznie.
2. **Zagraj albo odrzuć jedną Kartę Ruchu.** To jest twoja normalna akcja.
3. **Pytania karty.** Jeżeli karta czegoś wymaga (który pionek? w którą stronę?
   które pole podwójne?), gra pyta w ustalonej kolejności.
4. **Okno blokady.** Jeżeli ktoś ma aktywną kartę „Nie masz Rosji" albo
   umiejętność Dziubdziucha, może teraz zablokować twój ruch.
5. **Ruch się wykonuje.**
6. **Ręka się uzupełnia** do właściwego rozmiaru.
7. **Tura przechodzi dalej** automatycznie.

Karty Skrzyni i umiejętności postaci można zagrywać **w swojej turze**, poza
normalną akcją ruchu.

Jest też przycisk **„Zakończ turę"**, gdy chcesz oddać turę bez zagrywania.

### Cofnięcie ruchu

Po zagraniu karty, **dopóki następny gracz nie zagra swojej**, dostępny jest
przycisk **„Cofnij ruch"**. Cofa on pozycje pionków, wieże, ręce, stosy, ładunki
umiejętności i statusy. Zagrana karta wraca do ręki, a dobrana karta na wierzch
talii — więc powtórzona tura dobierze dokładnie tę samą kartę.

Cofnąć **nie można** zmiany ról (Kingmaker) ani niczego w trakcie wybierania
tożsamości.

### Kiedy gra się zatrzymuje

Gra pauzuje całkowicie (nikt nie może nic zrobić) w czterech sytuacjach:

- Piotrek wybiera swój kolor na początku,
- trwa **wybór Modów Patusa**,
- otwarte jest **okno decyzji** (blokada ruchu albo Ice Block),
- trwa **zamiana tożsamości** (Alter Ego / Kingmaker).

---

## 6. Ruch i plansza

### Podstawy

- Ruch liczony jest w **pozycjach** (numerach pól), nie w polach fizycznych.
- Ruch do przodu zatrzymuje się na mecie; ruch do tyłu zatrzymuje się na
  początku planszy. Pionek nie „odbija się".

### Pola podwójne (12a / 12b)

Poszerzony rząd to **jedna pozycja z dwoma polami**. Oba są tak samo daleko od
startu.

- Jeśli pionek **kończy ruch** na takim rzędzie — **gracz wybiera**, na którym
  z dwóch pól stanie.
- Jeśli tylko **przez niego przechodzi** — wybierana jest bliższa połowa,
  automatycznie, bez pytania. (Wyjątkiem jest **Dzieckorolka**, gdzie każde
  mijane pole podwójne jest osobnym pytaniem, bo decyduje o tym, kogo się
  zgarnie.)

### Wieże i przenoszenie

Kilka pionków może stać na jednym polu, tworząc **wieżę**.

**Ruszając pionek, zabierasz ze sobą wszystko, co stoi na nim.** Pionki pod
spodem zostają. To jest podstawowa zasada „żółwiej wieży" i ma ogromne
konsekwencje — pionek na dole wieży jest zablokowany, dopóki ktoś nie ruszy jego
samego.

Kolejność w wieży jest istotna, bo **Oprawcy wygrywają dzięki pionkowi na samym
dole**.

Wyjątki od zasady przenoszenia:
- **Balbinka** nie przenosi nikogo — każdy pionek rusza się sam.
- **AKO wariant 2** wyciąga tylko jednego sąsiada, bez tego, co na nim stoi.

### Obóz startowy

Wszystkie pionki zaczynają w obozie przed polem 1.

**Dopóki choć jeden pionek stoi na starcie, nie można używać żadnych
umiejętności postaci.** Przycisk umiejętności jest wtedy wyszarzony i pisze
„PIONKI NA STARCIE".

Pionek zabrany z planszy przez **Obóz Harcerski** nie liczy się jako stojący na
starcie.

### Ręczne przestawianie pionków

Pionki można przeciągać myszą (ustawianie przy stole). **Ręczne przestawienie
pionka kasuje obietnice, które go dotyczyły** — zamrożenie, sklejenie Radarem i
zakaz sąsiedztwa Długu u Tomasza. Nie da się utrzymać obietnicy o tym, gdzie
pionek stoi, jeśli ktoś właśnie postawił go gdzie indziej ręką.

---

## 7. Karty Ruchu

**Karta Ruchu to twoja normalna akcja w turze.** Zagrywasz jedną (albo ją
odrzucasz), ręka się uzupełnia, tura przechodzi dalej.

Większość kart po prostu przesuwa pionki. Kilka robi coś zupełnie innego.

Dwie karty — **Troll** i **Stańczyk** — są **zablokowane**: nie można ich zagrać
ani odrzucić ręcznie. Działają **w momencie dobrania** i same się rozliczają.

### Karty przesuwające konkretny kolor

Te 18 kart działa identycznie i różni się tylko kolorem oraz dystansem.
**Nazwany kolor jest nazwanym kolorem** — jeśli ten pionek jest zamrożony albo
schowany, karta nie podstawia innego, po prostu nic nie robi.

| Karta | Kopii | Działanie |
|---|---|---|
| **Zerówka - czerwony** | 5 | Porusz czerwony pionek **1 pole do przodu** |
| **Zerówka - zielony** | 5 | Porusz zielony pionek **1 pole do przodu** |
| **Zerówka - żółty** | 5 | Porusz żółty pionek **1 pole do przodu** |
| **Zerówka - niebieski** | 5 | Porusz niebieski pionek **1 pole do przodu** |
| **Zerówka - różowy** | 5 | Porusz różowy pionek **1 pole do przodu** |
| **Zerówka - pomarańczowy** | 5 | Porusz pomarańczowy pionek **1 pole do przodu** |
| **Fillerski przedmiot - czerwony** | 1 | Porusz czerwony pionek **2 pola do przodu** |
| **Fillerski przedmiot - zielony** | 1 | Porusz zielony pionek **2 pola do przodu** |
| **Fillerski przedmiot - żółty** | 1 | Porusz żółty pionek **2 pola do przodu** |
| **Fillerski przedmiot - niebieski** | 1 | Porusz niebieski pionek **2 pola do przodu** |
| **Fillerski przedmiot - różowy** | 1 | Porusz różowy pionek **2 pola do przodu** |
| **Fillerski przedmiot - pomarańczowy** | 1 | Porusz pomarańczowy pionek **2 pola do przodu** |
| **Wejściówka - czerwony** | 2 | Porusz czerwony pionek **1 pole do tyłu** |
| **Wejściówka - zielony** | 2 | Porusz zielony pionek **1 pole do tyłu** |
| **Wejściówka - żółty** | 2 | Porusz żółty pionek **1 pole do tyłu** |
| **Wejściówka - niebieski** | 2 | Porusz niebieski pionek **1 pole do tyłu** |
| **Wejściówka - różowy** | 2 | Porusz różowy pionek **1 pole do tyłu** |
| **Wejściówka - pomarańczowy** | 2 | Porusz pomarańczowy pionek **1 pole do tyłu** |

### Przepis

**Działanie:** Porusz pionek **najbardziej z tyłu** o 1 pole do przodu.
**Cel:** Wybierany automatycznie — nie ty decydujesz.
**Ograniczenia:** Pionek zamrożony jest pomijany; karta działa wtedy na
następnego od tyłu.
**Kopii:** 5

### Obniżenie progu

**Działanie:** Porusz pionek **najbardziej z tyłu** o 2 pola do przodu.
**Cel:** Jak wyżej — automatycznie, z pomijaniem zamrożonych.
**Kopii:** 2

### Kolos z paki

**Działanie:** Porusz **wybrany** pionek 1 pole do przodu.
**Cel:** Ty wybierasz dowolny pionek.
**Kopii:** 3

### Astral 2019

**Działanie:** Porusz **wybrany** pionek 2 pola do przodu.
**Kopii:** 2

### Astral 2022

**Działanie:** Porusz **wybrany** pionek 2 pola do tyłu.
**Kopii:** 1

### Plagiat!

**Działanie:** Cofnij **dwa wybrane** pionki o 1 pole każdy.
**Cel:** Wybierasz dokładnie dwa pionki; potwierdzenie odblokowuje się dopiero
przy dwóch zaznaczonych.
**Ważne:** Drugi pionek rusza się z planszy **już zmienionej** przez pierwszy
ruch. Jeśli pierwszy pionek przeniósł go ze sobą w wieży, drugi ruch liczy się
od nowego miejsca.
**Kopii:** 2

### Janek

**Działanie:** Przenieś **wybrany** pionek prosto **na pionek różowy** — pionek
ląduje na jego polu, na szczycie wieży.
**Cel:** Ty wybierasz, który pionek podróżuje.
**Ograniczenia:** To nie jest wędrówka po polach — pionek jest po prostu
przestawiony, więc nic po drodze się nie dzieje.
**Kopii:** 1

### Spy

**Działanie:** Przejrzyj Karty Ruchu przeciwnika i **zabierz jedną**.
**Kto kogo:** Oprawca zawsze okrada **Piotrka**. Piotrek wybiera, którego
Oprawcę okraść.
**Ograniczenia:** Widzisz **wyłącznie Karty Ruchu** — Karty Skrzyni pozostają
zakryte.
**Ważne:** Okradziony gracz dobiera kartę w miejsce zabranej.
**Kopii:** 1

### Thunderfuck

**Działanie:** Dobierz nowy Mod Patusa i **wstaw go do stojaka**. Nowa karta
idzie na **lewe** miejsce, dotychczasowa lewa przesuwa się na **prawe**, a
dotychczasowa prawa zostaje odrzucona.
**Ograniczenia:** Jeśli **stojak jest pusty**, karta **nie robi nic** — zostaje
zagrana i odrzucona normalnie. Thunderfuck *wymienia* to, co jest w grze; przed
pierwszym wyborem nie ma czego wymieniać.
**Kiedy:** Działa w turze dowolnego gracza.
**Kopii:** 3

### Seks z pedałami

**Działanie:** Karta zapowiada się, a następnie **odkrywa i zagrywa losową Kartę
Ruchu**.
**Ważne:** Tekst na karcie („Daj Piotrkowi Kontnemu buziaka") to żart —
mechanicznie karta losuje i wykonuje inną kartę ruchu.
**Ograniczenia:** Losowana jest tylko taka karta, która potrafi rozwiązać się
sama, bez zadawania pytań.
**Kopii:** 1

### Troll

**Działanie:** Karta działa **w chwili dobrania**. W **następnej twojej turze**
musisz zagrać **losową Kartę Skrzyni z ręki**; jeśli żadnej nie masz — losową
Kartę Ruchu. Dobierasz też od razu jedną Kartę Ruchu w zamian.
**Kiedy:** Automatycznie, przy dobraniu. Nie da się jej zagrać ani odrzucić.
**Ograniczenia:** Nigdy nie trafia do ręki startowej. Wymuszona karta, która
wymagałaby decyzji, po prostu zostaje zagrana i odrzucona bez efektu — wymuszone
zagranie nigdy nie może zatrzymać gry.
**Ważne:** Trolle mogą się kaskadować (dobrana karta zastępcza też może być
Trollem).
**Kopii:** 2

### Stańczyk

**Działanie:** Karta działa **w chwili dobrania**: twoja **następna tura zostaje
pominięta**, a karta jest wtedy odrzucana.
**Kiedy:** Automatycznie, przy dobraniu. Nie da się jej zagrać ani odrzucić.
**Ograniczenia:** Nigdy nie trafia do ręki startowej.
**Kopii:** 1

---

## 8. Karty Skrzyni

Karty Skrzyni to rzadkie, mocne karty. **Nie są twoją normalną akcją** — możesz
zagrać Kartę Skrzyni w swojej turze obok zwykłego ruchu.

### Jak się je zdobywa

Patrz rozdział 11 — **Otwieranie skrzyni**.

### Limit ręki

| Gracz | Limit Kart Skrzyni |
|---|---|
| Oprawca | **1** |
| Piotrek | **2** |
| Piotrek z ChatGPT | **1** |

Jeżeli po rozdaniu masz ich za dużo, pojawia się okno: **zdecyduj, które
zatrzymujesz**, reszta idzie na stos odrzuconych.

### Dzieckorolka

**Działanie:** Porusz **wybrany** pionek 3 pola do przodu. Z **każdego mijanego
pola** zgarniasz **pionek stojący na samej górze** (jeden na pole).
**Cel:** Ty wybierasz pionek, który jedzie.
**Wybory:** Każde mijane pole podwójne to **osobne pytanie**, bo to, którą
połową przejdziesz, decyduje o tym, kogo zabierzesz. Pole docelowe też wybierasz.
**Kolejność w wieży na końcu** (od dołu):
1. ci, którzy już stali na polu docelowym,
2. zgarnięci pionki w **odwrotnej** kolejności zbierania (ostatni zebrany
   najniżej),
3. pionek, który jechał,
4. to, co jechało na nim od początku.
**Ograniczenia:** Zgarniany jest zawsze pionek z góry, więc wieża nigdy nie
zostaje rozerwana. Pole, którego górny pionek jest **zamrożony**, nie oddaje
nikogo.
**Ważne:** Kolejność w wieży decyduje o zwycięstwie Oprawców — ta karta potrafi
ustawić kogoś na spodzie.
**Kopii:** 2

### Rage Quit

**Działanie:** Wymień **wszystkie aktywne Mody Patusa** na nowe, losowe z talii.
Każde zajęte miejsce w stojaku dostaje nową kartę **na to samo miejsce**.
**Ograniczenia:** **Pusty stojak — nic się nie dzieje.** Puste miejsce zostaje
puste; stojak nigdy nie zapełnia się Modem, którego nikt nie wybrał.
**Ważne:** Nowe karty są dobierane **zanim** stare wrócą na stos, więc nie da się
wylosować z powrotem tego, czego się właśnie pozbyło. Mody odchodzące
natychmiast przestają działać, a nowe zaczynają.
**Kopii:** 2

### Balbinka

**Działanie:** Porusz **wszystkie pionki** o 2 pola — **do przodu albo do
tyłu**.
**Cel:** Ty wybierasz kierunek dla całej planszy.
**Ograniczenia:** **Nikt nikogo nie przenosi.** Każdy pionek pokonuje własne dwa
pola, nawet jeśli stoi w wieży.
**Ważne:** Ruch jest wykonywany w kolejności zapobiegającej wpadaniu na siebie:
przy ruchu do przodu pierwszy rusza pionek najdalszy, przy ruchu do tyłu —
najbardziej z tyłu. W obrębie jednego pola pierwszy rusza pionek z dołu, więc
wieża przyjeżdża w tej samej kolejności, w jakiej wyjechała.
Jeśli pionek kończy na polu podwójnym, **połowa jest losowana**.
**Kopii:** 2

### Nie masz Rosji

**Działanie:** Zyskujesz **jednorazową możliwość zablokowania jednego ruchu
przeciwnika**.
**Czas trwania:**

| Wariant | Zasada |
|---|---|
| 1 (domyślny) | Przez **dwie pełne rundy** od zagrania |
| 2 | Przez **jedną pełną rundę** od zagrania |

**„Pełna runda" oznacza: dopóki tura nie wróci do ciebie.**
**Kogo można blokować:** Tylko **przeciwnika** — Piotrek blokuje Oprawców,
Oprawcy blokują Piotrka.
**Co można zablokować:** Zagraną **Kartę Ruchu lub Kartę Skrzyni, która
faktycznie porusza pionek**. Umiejętności postaci, Modów i ręcznego
przestawiania pionków zablokować nie można.
**Jak to wygląda:** Ruch zostaje **wstrzymany, zanim się wydarzy** — pojawia się
okno z odliczaniem (domyślnie 7 s). Jeśli zablokujesz, ruch **nigdy się nie
wykonał**: karta jest odrzucana, plansza nietknięta. Jeśli przepuścisz (albo
skończy się czas), ruch odbywa się normalnie i **nic cię to nie kosztuje**.
**Ważne:** Jeśli blokujących jest kilku, ruch wykonuje się dopiero, gdy
**wszyscy** przepuszczą. Jeżeli gra widzi, że to **ostatnia okazja**, by ta karta
kiedykolwiek coś zablokowała, blokada odpala się **automatycznie**, bez okna.
**Kopii:** 2

### Gambit Patusa

**Działanie:** **Odwróć kierunek wszystkich Kart Ruchu przez całą następną
rundę.** Karty „do przodu" cofają, karty „do tyłu" popychają.
**Kiedy:** Działa w **następnej** rundzie, nie w bieżącej.
**Ograniczenia:** Dotyczy **tylko Kart Ruchu** — nie umiejętności i nie Kart
Skrzyni.
**Ważne:** Dwa zagrane Gambity **nie kasują się** — to obietnice o dwóch różnych
rundach. Jeśli działa też Speedrun, to Gambit rozstrzyga się pierwszy, a
Speedrun pyta o **już odwrócony** kierunek.
**Kopii:** 3

### Gejtos

**Działanie:** Wybierasz jedną z dwóch opcji:

- **Mężczyzna** — przyciągnij pionki z **obu sąsiadujących pól** na pole
  wybranego pionka.
- **Kobieta** — odsuń pionki z **obu sąsiadujących pól** o jedno pole dalej.

**Kolejność wyborów:** Najpierw **opcja**, potem pionek.
**Ograniczenia:** **Wybrany pionek sam się nie rusza** — jest punktem
odniesienia. **Kobieta zostanie odrzucona**, jeśli któryś pionek musiałby wyjść
przed pole 1 (karta nie wykona się „w połowie"). Przy końcu planszy ruch
normalnie się zatrzymuje na mecie.
**Ważne:** Pionki są **przenoszone całymi wieżami**, a nie idą po polach — więc
nic ich po drodze nie spotyka i żaden Mod ich nie skraca. Zamrożony pionek nie
jest ruszany.
**Kopii:** 3

### Gamechanger → Alter Ego / Kingmaker

**Działanie:** Karta **zmienia się w drodze do ręki**, zależnie od tego, kto ją
dostaje:

- trafia do **Piotrka** → staje się **Alter Ego**,
- trafia do **Oprawcy** → staje się **Kingmaker**.

**Alter Ego (u Piotrka):**
1. Tożsamość Piotrka **zostaje ujawniona** wszystkim.
2. Notatnik „Kolory Piotrka" **zostaje wyczyszczony** — wszystkie dotychczasowe
   skreślenia przestają obowiązywać, bo dotyczyły tożsamości, która już nie
   istnieje.
3. Piotrek **wybiera nowy kolor**. Nie może wybrać tego, który właśnie oddał.
4. Gra rusza dalej z nową tajemnicą.

**Kingmaker (u Oprawcy):**
1. **Rola Piotrka przechodzi na gracza, który zagrał kartę.** Dotychczasowy
   Piotrek staje się Oprawcą.
2. Tożsamość starego Piotrka **zostaje ujawniona**.
3. **Nowy** Piotrek wybiera swój własny nowy kolor (nie ten ujawniony).

**Co przechodzi razem z rolą:** karta postaci i **Umiejętność Piotrka** (razem z
liczbą zużytych ładunków). **Nie przechodzą** prywatne notatki gracza.
**Ważne:** Wraz z rolą przechodzi **wszystko**, co od niej zależy: miejsce w
kolejności tur (co trzecia tura!), limit Kart Skrzyni, strona w wyborze Modów
Patusa i warunki zwycięstwa.
**Ograniczenia:** Tej karty **nie da się cofnąć**.
**Kopii:** 1

### Herold

**Działanie:** **Kopiuje i wykonuje umiejętność dowolnej postaci w grze.**
**Cel:** Wybierasz z listy postać, której umiejętność chcesz wykonać. Skopiowana
umiejętność zadaje swoje własne pytania normalnie.
**Ograniczenia:**
- **Nie możesz skopiować własnej postaci.**
- Nie działa, dopóki jakikolwiek pionek stoi na starcie.
- W ustawieniach można wskazać umiejętności, których skopiowanie **zużywa
  ładunek ich właścicielowi**.
**Kopii:** 1

### Shady

**Działanie:** ⚠ **DO POTWIERDZENIA.** Karta ma tekst „Odbierz innemu graczowi
specjalną kartę", ale **nie ma jeszcze zaimplementowanej mechaniki**. Zagrana
karta pokazuje swój tekst, trafia na stos odrzuconych i **nie robi nic
automatycznie**. Rozstrzygnijcie ją przy stole.

Nie mylić z **Modem Patusa „Obóz Harcerski"**, który wcześniej nosił nazwę
Shady — to zupełnie inna karta.
**Kopii:** 2

---

## 9. Mody Patusa

**Mod Patusa to zasada zmieniająca całą grę**, obowiązująca tak długo, jak długo
karta leży na stojaku.

Stojak ma **dwa miejsca**:

- **lewe** należy do **Piotrka**,
- **prawe** należy do **Oprawców**,

i tak zostaje do końca gry.

### Kiedy wybiera się Mody

Domyślnie w rundzie **3**, a potem **co 2 rundy** (3, 5, 7, 9…). Oba te numery
ustawia się przed grą.

W rundzie wyboru **gra się zatrzymuje** — nikt nie może wykonać ruchu, dopóki
obie strony nie wybiorą.

### Jak przebiega wybór

Każda ze stron dostaje **trzy własne, różne karty** do wyboru.

**Piotrek:** ogląda swoje trzy karty (tylko on je widzi) i **wybiera jedną**.
Trafia na **lewe** miejsce, pozostałe dwie są odrzucane.

**Oprawcy — głosowanie:**
- **Głosuje każdy Oprawca**, jeden głos na osobę.
- **Głos można zmienić**, dopóki nie zagłosuje ostatni Oprawca.
- **Wszyscy Oprawcy widzą wszystkie głosy** — liczenie jest jawne.
- Gdy zagłosują wszyscy, **wygrywa karta z największą liczbą głosów**.
- **Remis: wygrywa karta stojąca najbardziej po lewej.**

Zwycięska karta trafia na **prawe** miejsce, pozostałe dwie są odrzucane.

### Jak długo działa Mod

**Dopóki leży na stojaku.** Zejść z niego może tylko przez:
- **Thunderfuck** (nowa karta wpycha stare w prawo, skrajnie prawa wypada),
- **Rage Quit** (wymiana wszystkich aktywnych),
- kolejny wybór Modów.

Mod, który schodzi ze stojaka, **nie zostawia po sobie nic** — Obóz Harcerski
natychmiast oddaje ukrytego pionka, a Squid Game przywraca normalne sprawdzanie.

### Gdy działają dwa Mody naraz

Obowiązują **oba**. Gdyby kiedykolwiek się wykluczały, **rozstrzyga lewe
miejsce**. Dwie kopie tego samego Modu **nie kumulują** efektu.

**Mody dotyczą Kart Ruchu**, chyba że napisano inaczej. Umiejętności postaci i
Karty Skrzyni **nie** są przez nie skracane ani ograniczane.

---

### Speedrun

**Działanie:** Każdą Kartę Ruchu, która **cofa** pionki, można **odwrócić** i
zagrać jako ruch do przodu.
**Kiedy:** Gra pyta o to **jako pierwsze**, zanim wybierzesz pionek.
**Ograniczenia:** Pytanie pojawia się tylko wtedy, gdy **faktyczny** kierunek
ruchu jest wsteczny. Karty, które i tak pozwalają wybrać kierunek (np. Balbinka),
nie pytają dwa razy. Odwrócenie jest **propozycją** — zawsze można odmówić.
**Kopii w talii:** 2

### Masa solna

**Działanie:** **Wszystkie Karty Ruchu poruszają tylko o jedno pole.**
**Ograniczenia:** Dotyczy **wyłącznie Kart Ruchu**. Umiejętność Dziada i Karty
Skrzyni działają na pełnym dystansie.
**Kopii:** 2

### AKO

**Działanie:** Każda **Karta Ruchu** zabiera ze sobą **jednego sąsiadującego
pionka**, który wykonuje **dokładnie ten sam ruch**.
**Który pionek:** Ten, który **wchodzi w miejsce zwolnione przez ruszający się
pionek** — przy ruchu do przodu sąsiad z tyłu, przy ruchu do tyłu sąsiad z
przodu.
**Warianty:**

| Wariant | Zasada |
|---|---|
| 1 (domyślny) | Sąsiad jedzie **razem z tym, co na nim stoi** (normalna zasada wieży) |
| 2 | Z wieży wyciągany jest **tylko ten jeden pionek**, reszta zostaje |

**Wybory:** Jeśli kandydatów jest kilku (wieża, oba pola podwójnego rzędu),
wybierasz. Jeśli jest jeden — jedzie bez pytania.
**Ograniczenia:** Dotyczy **tylko Kart Ruchu**. Pionek zamrożony lub schowany
nie jest zabierany. Jeśli sąsiad nie ma dokąd pojechać, **po prostu nie jedzie**
— karta i tak działa. Dwie kopie AKO zabierają **jednego** sąsiada.
**Ważne:** Sąsiad pokonuje ten sam dystans **po wszystkich modyfikatorach** —
Masa solna skraca też jego, a ChatGPT wydłuża.
**Kopii:** 1

### Halloween

**Działanie:** **Pionek, który nie ma sąsiada, nie może się ruszyć.**
**Co znaczy „sąsiad":** Pole **bezpośrednio przed** lub **bezpośrednio za**
pionkiem jest zajęte. **Stanie na tym samym polu to nie sąsiedztwo** — wieża to
jedno pole.
**Ważne:** **Pionki czekające w obozie startowym są swoimi sąsiadami.**
**Ograniczenia:** Karta wskazująca zablokowany pionek zostaje **zagrana i
odrzucona**, ręka się uzupełnia, tura przechodzi dalej — po prostu ruch się nie
odbywa. Przy kartach ruszających wiele pionków każdy oceniany jest wobec planszy
w chwili **jego** ruchu.
**Kopii:** 1

### Sesja na PG

**Działanie:** **Umiejętności postaci nie mogą być używane.**
**Warianty:**

| Wariant | Zasada |
|---|---|
| 1 (domyślny) | Umiejętności są zablokowane |
| 2 | Dodatkowo **wszystkie działające efekty umiejętności zostają anulowane** |

**Ważne:** **Ładunki nie są tracone.** Przycisk jest wyszarzony i pisze „SESJA NA
PG" — to nie to samo co „ZUŻYTE".
**Kopii:** 2

### Paczka

**Działanie:** **Karty Skrzyni wszystkich graczy zostają odkryte.** Każdy
dostaje okno z listą: kto co trzyma.
**Kiedy:** W chwili, gdy Mod trafia na stojak. Jeśli akurat trwa wybór Modów,
okno czeka do jego zakończenia.
**Ważne:** To jedyna sytuacja, w której zawartość Kart Skrzyni staje się jawna.
Okno zamyka każdy u siebie.
**Kopii:** 2

### Squid Game

**Działanie:** **Pionek najbardziej z przodu jest sprawdzany raz na rundę.
Innego sposobu na sprawdzenie nie ma.**
**Kiedy:** Na początku każdej rundy, automatycznie.
**Ograniczenia:**
- **Zwykłe sprawdzanie przez zbudowanie wieży przestaje działać** — dopóki ten
  Mod leży, zebranie wszystkich pionków na jednym polu nic nie daje.
- Jeśli **kilka pionków dzieli prowadzenie**, runda jest **pominięta** (i gra o
  tym mówi) — nie ma dogrywki.
- Kolor już skreślony nie jest sprawdzany drugi raz.
**Ważne:** **Zwycięstwo Piotrka przez dojście do mety działa normalnie** — ten
Mod zastępuje wyłącznie sprawdzanie.
**Kopii:** 1

### Obóz Harcerski

**Działanie:** **Pionek najbardziej z przodu znika z mapy na pełną rundę.** Po
rundzie wraca **na pionek najbardziej z tyłu** (na szczyt tej wieży).
**Który pionek:** Ten z **dołu** najdalszego zajętego pola.
**Ograniczenia:** Schowany pionek jest **niewidoczny dla wszystkiego**: nie da
się go kliknąć, nie liczy się jako najdalszy ani najbliższy, nie jest niczyim
sąsiadem, karta która go wskazuje po prostu nic nie robi.
**Ważne:**
- **Nie wraca tam, skąd zniknął** — to jest sens tej karty.
- Przy sprawdzaniu liczą się **pionki obecne na planszy**, więc w trakcie
  działania tego Modu wieża potrzebuje o jeden pionek mniej.
- Działa **raz**, przy wejściu na stojak. Druga kopia to nowe wejście i chowa
  kolejny pionek.
**Kopii:** 2

---

## 10. Umiejętności postaci

Każdy gracz ma **kartę postaci** z własną umiejętnością o ograniczonej liczbie
użyć. Umiejętność uruchamia się przyciskiem w prawym panelu.

**Nazwa postaci to nie nazwa umiejętności.** Na przykład postać **Big D Randy**
posiada umiejętność o nazwie **Granny Costume**. W panelu i w Bibliotece Kart
zobaczysz nazwę **umiejętności**.

### Zasada nadrzędna

> **Dopóki choć jeden pionek stoi na starcie, nie można użyć żadnej
> umiejętności.**

Dotyczy to wszystkich postaci, a także umiejętności skopiowanej **Heroldem**.

Liczbę ładunków każdej umiejętności można zmienić przed grą (0 oznacza, że
postać jest w grze, ale bez swojej mocy).

---

### Big D Randy — *Granny Costume*

**Działanie:** **Zamraża wybrany pionek na pełną rundę.** Zamrożony pionek nie
może się ruszyć.
**Cel:** Pionek, który **stoi sam** — nie w wieży.
**Czas trwania:** Pełna runda, czyli **dopóki tura nie wróci do Big D Randy'ego**.
**Ograniczenia:** Karta wskazująca zamrożony pionek **zostaje w ręce** (zagranie
jest odrzucane). Ręczne przeciągnięcie pionka kasuje zamrożenie.
**Użyć:** 1

### Lubin — *Jazdy*

**Działanie:** **Piotrek traci następną turę.**
**Kiedy:** Zużywa **pierwszą** turę Piotrka, jaka nadejdzie — nie da się wskazać
której.
**Ograniczenia:** Nie można użyć w trakcie tury Piotrka. Ponieważ Piotrek gra co
trzecią turę, traci **jeden z trzech** swoich slotów w rundzie.
**Użyć:** 1

### Mitoman — *PAA*

**Działanie:** **Przenosi pionek najbardziej z przodu na pionek najbardziej z
tyłu** (ląduje na szczycie tej wieży).
**Cel:** Automatyczny — nie wybierasz.
**Ograniczenia:** Pionki zamrożone są pomijane przy wyznaczaniu „najdalszego" i
„najbliższego".
**Użyć:** 1

### Glockboy — *Hunt for Marcus*

**Działanie:** **Wskazujesz jeden pionek i twierdzisz, że to Piotrek.**
- **Trafienie → Oprawcy natychmiast wygrywają grę.**
- **Pudło → Glockboy odpada z gry**, a wskazany kolor zostaje skreślony.
**Cel:** Dowolny pionek, **poza kolorami już skreślonymi**.
**Ograniczenia:** **Wymaga, by wcześniej sprawdzono już co najmniej 3 pionki.**
Wcześniej umiejętność odmówi działania i nie zużyje ładunku.
**Ważne:** Gracz, który odpadł, **zostaje przy stole jako obserwator** — jego
tury są pomijane, nie może grać kart ani używać umiejętności, ale zachowuje imię
i może głosować nad Modami Patusa. Piotrek może odmówić tego sprawdzenia kartą
**Ice Block**.
**Użyć:** 1

### Norbur — *Plac*

**Działanie:** ⚠ **DO POTWIERDZENIA.** Tekst karty brzmi: *„Pionki mogą poruszać
się tylko pomiędzy najdalszym i najbliższym pionkiem na jedną pełną rundę.
Minimum 3 pola różnicy"*.

**Ta zasada nie jest jeszcze zaimplementowana.** Umiejętność da się użyć,
**zużywa swój ładunek** i wyświetla swój tekst na pasku stanu — ale gra sama
niczego nie ogranicza. **Rozstrzygnijcie ją przy stole.**
**Użyć:** 1

### Dziad — *Skrypt*

**Działanie:** Przesuwa **wybrany** pionek o **1 lub 2 pola**, **do przodu albo
do tyłu**.
**Cel:** Ty wybierasz pionek, dystans i kierunek.
**Ograniczenia:** Pionki zamrożone **nie są w ogóle pokazywane** na liście do
wyboru. Mody ograniczające ruch (Masa solna) **nie** dotyczą tej umiejętności.
**Użyć:** 2

### Ondrej — *Radar*

**Działanie:** **Skleja 2 pionki ze sobą na pełną rundę** — poruszają się razem
jako jedna całość.
**Cel:** Wybierasz dwa pionki (w kolejności).
**Warianty:**

| Wariant | Przy sprawdzeniu |
|---|---|
| 1 (domyślny) | **Sprawdzane są oba** sklejone pionki |
| 2 | Sprawdzany jest **tylko ten** pionek, którego dotyczy check |

**Czas trwania:** Pełna runda.
**Ograniczenia:** Ręczne przeciągnięcie pionka kasuje sklejenie.
**Użyć:** 1

### Dziubdziuch — *Przerwanie Systemowe*

**Działanie:** **Blokuje ruch Piotrka.** Działa jak „Nie masz Rosji", ale
skierowane wyłącznie w Piotrka.
**Jak się tego używa:** To **dwa kroki**. Najpierw **w swojej turze** aktywujesz
umiejętność — zyskujesz wtedy prawo do jednej blokady. Samo **zablokowanie**
następuje później, w oknie decyzji, gdy Piotrek zagra kartę poruszającą pionek.
**Warianty:**

| Wariant | Kiedy | Co blokuje |
|---|---|---|
| 1 (domyślny) | W dowolnym momencie gry | Karty Ruchu **i** Karty Skrzyni |
| 2 | W dowolnym momencie gry | Tylko Karty Ruchu |
| 3 | Przez jedną pełną rundę | Karty Ruchu **i** Karty Skrzyni |
| 4 | Przez jedną pełną rundę | Tylko Karty Ruchu |

**Ważne:** Zablokowany ruch **nigdy się nie odbywa** — karta jest odrzucana, a
plansza pozostaje nietknięta. Umiejętność „spala się" po jednym użyciu.
**Użyć:** 1

### Atencjusz — *Liskowy Konkurs*

**Działanie:** **Przyznaje sobie dodatkowy ruch.** Zachowuje się **inaczej** w
zależności od tego, kiedy go użyjesz:

- **Przed swoim ruchem** (masz jeszcze turę) → dostajesz **dodatkową kartę od
  razu** i **drugie zagranie** w tej turze.
- **Po swoim ruchu**, zanim następny gracz zagra → **tura wraca do ciebie w
  całości**. Zagrana wcześniej karta zostaje zagrana, dobrana zostaje w ręce —
  **to nie jest cofnięcie ruchu**.

**Kiedy:** Okno na drugi wariant to **to samo okno, w którym działa przycisk
„Cofnij ruch"** — zamyka się, gdy następny gracz zagra kartę.
**Ograniczenia:** Tekst karty mówi o ruchu „dowolnej osoby", ale gra przyznaje
dodatkową turę **wyłącznie w oknie po twoim własnym ruchu**. Po cudzym ruchu
umiejętność odmówi działania („Okno na Liskowy Konkurs już minęło").
**Użyć:** 1

> ⚠ **DO POTWIERDZENIA:** w grze sieciowej z pilnowaniem kolejności tur drugi
> wariant („tura wraca do ciebie") jest w praktyce nieosiągalny — umiejętności
> można używać tylko we własnej turze, a to okno otwiera się dopiero wtedy, gdy
> tura już przeszła dalej. Wariant „przed swoim ruchem" działa normalnie. Przy
> jednym komputerze (tryb edycji) działają oba.

### Piotrek (karta roli)

Karta postaci Piotrka **nie ma własnej umiejętności**. Zamiast tego gracz z tą
kartą losuje jedną **Umiejętność Piotrka** (poniżej).

---

### Umiejętności Piotrka

Piotrek dostaje **jedną, losową** z trzech.

#### ChatGPT

**Działanie (aktywne):** **Zwiększa zasięg następnej Karty Ruchu o jedno pole.**
**Działanie (stałe):** Piotrek zaczyna grę z **dwiema Kartami Ruchu mniej** (3
zamiast 5) i może trzymać tylko **1 Kartę Skrzyni** zamiast 2.
**Użyć:** 5

#### Ice Block

**Działanie:** **Może odmówić sprawdzenia.**
**Kiedy:** **Reaktywnie** — nie naciskasz przycisku. Gdy ktokolwiek próbuje
sprawdzić pionek, Piotrkowi otwiera się okno decyzji (domyślnie 10 sekund).
**Efekt odmowy:** Sprawdzenie **zostaje anulowane, a nie rozstrzygnięte**. Żaden
kolor nie zostaje skreślony, żadna tożsamość nie jest porównywana, **nic nie jest
ujawniane** — pytanie po prostu nie padło. Zużywa jeden ładunek.
**Efekt zgody (lub upływu czasu):** Sprawdzenie odbywa się normalnie i **nic nie
kosztuje**.
**Ograniczenia:** Po odmowie **pionki muszą zostać rozdzielone**, zanim będzie
można sprawdzać ponownie — inaczej ta sama wieża byłaby sprawdzana w kółko.
**Ważne:** Chroni przed **wszystkimi trzema** sposobami sprawdzania: wieżą,
automatycznym checkiem Squid Game i umiejętnością Glockboya.
**Użyć:** 1

#### Dług u Tomasza

**Działanie:** Wybierasz **dwa pionki, które przez pełną rundę nie mogą ze sobą
sąsiadować.**
**Cel:** Ty wybierasz parę.
**Ograniczenia:** Jeżeli w chwili użycia pionki są już za blisko, **rozdziela je
natychmiast**: pionek stojący dalej przesuwa się do przodu, aż zrobi się wolne
pole; drugi nie rusza się wcale. Ruch, który złamałby zakaz, jest
**anulowany w całości** (nie skracany).
**Ważne:** Sąsiedztwo liczy się w **pozycjach**, więc 3a i 3b to to samo miejsce.
Ręczne przeciągnięcie pionka kasuje zakaz.
**Użyć:** 1

---

## 11. Otwieranie skrzyni

„Otwarcie skrzyni" nie jest czynnością żadnego gracza — to **moment w grze**, od
którego zaczynają być rozdawane Karty Skrzyni.

### Krok po kroku

1. **Skrzynia jest zamknięta** do rundy ustalonej przed grą (domyślnie **runda
   6**). Panel na górze ekranu pokazuje „SKRZYNIA — OD RUNDY 6".
2. **W rundzie otwarcia skrzynia rozdaje zawsze**, niezależnie od liczby graczy.
3. **W rundzie rozdania kartę dostają dwie osoby:**
   - **Piotrek — za każdym razem.** Nie jest w rotacji.
   - **Jeden Oprawca** — ten, na którym stoi rotacja.
4. **Rotacja przesuwa się o jedną osobę na każde rozdanie** (nie na rundę).
5. Jeżeli po otrzymaniu karty przekraczasz swój limit Kart Skrzyni, pojawia się
   **okno wyboru: które karty zatrzymujesz**. Reszta idzie na stos odrzuconych.
   Do czasu odpowiedzi nie możesz wykonać ruchu.
6. Karty Skrzyni **zagrywa się później**, w swojej turze, obok zwykłego ruchu.

### Jak często rozdaje skrzynia

| Liczba graczy | Częstotliwość |
|---|---|
| **5 lub 6** | Karta w **każdej** rundzie od otwarcia |
| **4 lub mniej** | Karta co **drugą** rundę |

Przy małym stole rotacja wraca do tej samej osoby zbyt szybko, dlatego rozdania
są rzadsze.

### Znacznik na pasku kolejności

Pod portretem w pasku tur pojawia się kropka:

- **wypełniona** — w tej rundzie ta osoba dostaje kartę,
- **pusta** — w tej rundzie nikt nie dostaje karty (skrzynia jeszcze zamknięta
  albo runda pominięta).

**Znacznik przesuwa się także w rundach pominiętych** — zmienia się tylko jego
wypełnienie.

### Przykład (3 graczy, skrzynia od rundy 3)

| Runda | Znacznik | Kto dostaje |
|---|---|---|
| 3 | wypełniony (Norbur) | Piotrek + Norbur |
| 4 | pusty (Lubin) | nikt |
| 5 | wypełniony (Lubin) | Piotrek + Lubin |
| 6 | pusty (Norbur) | nikt |
| 7 | wypełniony (Norbur) | Piotrek + Norbur |

---

## 12. Sprawdzanie tożsamości

**Sprawdzenie (check)** to próba ustalenia, czy dany pionek należy do Piotrka.

### Trzy drogi do sprawdzenia

| Sposób | Kiedy | Kto uruchamia |
|---|---|---|
| **Zbudowanie wieży** | Gdy wszystkie obecne pionki staną na jednym polu | Nikt — dzieje się samo |
| **Squid Game** | Na początku każdej rundy, gdy ten Mod jest aktywny | Nikt — dzieje się samo |
| **Hunt for Marcus** | Kiedy Glockboy użyje umiejętności | Glockboy |

### Sprawdzenie przez wieżę — najważniejsza droga

1. Oprawcy doprowadzają do tego, że **wszystkie pionki obecne na planszy stoją
   na jednym polu**.
2. Sprawdzany jest **pionek na samym dole** tej wieży.
3. Jeśli to Piotrek → **Oprawcy wygrywają**.
4. Jeśli nie → **ten kolor zostaje skreślony** u wszystkich, a gra toczy się
   dalej.

**Liczą się pionki obecne na planszy** — jeśli Obóz Harcerski trzyma jeden poza
mapą, wieża potrzebuje o jeden pionek mniej.

**Każdy kolor sprawdzany jest tylko raz.** Wieża z już skreślonym kolorem na
spodzie nie robi nic — trzeba przestawić pionki.

### Okno Ice Block

Zanim **jakiekolwiek** sprawdzenie się rozstrzygnie, Piotrek — jeśli ma
umiejętność **Ice Block** z ładunkiem — dostaje okno decyzji (domyślnie 10 s).

- **Odmawia** → sprawdzenie zostaje **anulowane**. Nic nie jest ujawniane, żaden
  kolor nie zostaje skreślony. Kosztuje ładunek. **Zanim będzie można sprawdzać
  ponownie, pionki muszą zostać rozdzielone.**
- **Zgadza się albo nie zdąży** → sprawdzenie odbywa się normalnie, **za darmo**.

### Co się dzieje po nieudanym sprawdzeniu

Zależy od wariantu wybranego przed grą:

| Wariant | Nazwa | Skutek |
|---|---|---|
| 1 (domyślny) | nieudany check nic nie zmienia | Wieża stoi dalej, kolor skreślony |
| 2 | nieudany check rozbija wieżę | Wieża **rozpada się** |

**Wariant 2 — rozbicie wieży:** po krótkiej chwili wieża dzieli się na **pary**
według kolejności w wieży i pary te są odstawiane na pola **za** wieżą. Grupa od
dołu ląduje najbliżej, grupa z góry najdalej. Przy nieparzystej liczbie pionków
ostatnia „para" jest pojedyncza.

Jeżeli najdalej cofana grupa trafia na **pole podwójne**, to **Piotrek** wybiera,
na którą połowę ją postawić (nie gracz, który zbudował wieżę). Brak odpowiedzi w
czasie oznacza pierwsze pole.

> ⚠ **DO POTWIERDZENIA:** kolejność grup przy rozbiciu wieży. Gra ustawia grupę
> z **dołu** wieży najbliżej, a grupę z **góry** najdalej — zgodnie z zasadą
> zapisaną w projekcie. Przykład w oryginalnym opisie sugeruje odwrotną kolejność
> dwóch pierwszych grup. Wymaga decyzji właściciela gry.

### Czego sprawdzenie **nie** ujawnia

Sprawdzenie mówi wyłącznie: **„ten kolor to nie Piotrek"** albo **koniec gry**.
Nie ujawnia niczego o pozostałych kolorach ani o tym, co Piotrek trzyma w ręce.

---

## 13. Specjalne mechaniki i statusy

### Statusy nakładane na pionki i graczy

| Status | Skąd | Co robi |
|---|---|---|
| **Zamrożenie** | Granny Costume | Pionek nie może się ruszyć przez pełną rundę |
| **Pominięcie tury** | Stańczyk, Jazdy | Gracz traci najbliższą turę |
| **Przejęcie tury** | Troll | Tura jest wykorzystana na wymuszone zagranie |
| **Sklejenie** | Radar | Dwa pionki poruszają się razem |
| **Dodatkowa tura** | Liskowy Konkurs | Gracz dostaje ruch poza kolejnością |
| **Bonus zasięgu** | ChatGPT | Następna Karta Ruchu sięga o pole dalej |
| **Zakaz sąsiedztwa** | Dług u Tomasza | Dwa pionki nie mogą stać obok siebie |
| **Odwrócenie ruchu** | Gambit Patusa | Karty Ruchu działają w drugą stronę |
| **Prawo blokady** | Nie masz Rosji, Przerwanie Systemowe | Jednorazowa blokada cudzego ruchu |
| **Ukrycie** | Obóz Harcerski | Pionek zniknął z planszy na rundę |
| **Odmowa sprawdzenia** | Ice Block | Sprawdzenie anulowane; pionki muszą się rozdzielić |

**„Pełna runda" prawie zawsze znaczy: dopóki tura nie wróci do właściciela
efektu** — a nie „dopóki licznik rund nie skoczy".

### Przejęcia tury i wymuszone zagrania

- **Pominięcie** zabiera turę w całości.
- **Przejęcie** (Troll) zużywa turę na losowe zagranie.

Obie sytuacje **kończą turę normalnie**: ręka się uzupełnia, gra idzie dalej.

Wymuszone zagranie, które wymagałoby decyzji, zostaje **zagrane i odrzucone bez
efektu**. Wymuszenie nigdy nie może zablokować gry.

### Wybory wielokrotne i wieloetapowe

Niektóre karty pytają o kilka rzeczy naraz. Wtedy:

- przy wyborze kilku pionków przycisk potwierdzenia odblokowuje się **dokładnie**
  przy właściwej liczbie,
- gdy kolejność ma znaczenie, wybory są **numerowane** ①②; ponowne kliknięcie
  usuwa pionek z listy i przenumerowuje resztę.

**Stała kolejność pytań przy ruchu:**

1. Gambit Patusa (cicho — to fakt o rundzie),
2. Speedrun: czy odwrócić kierunek?
3. Który pionek?
4. Która połowa pola podwójnego?
5. AKO: który sąsiad jedzie z nim?

### Odpadnięcie z gry

Tylko **Glockboy** może odpaść (nietrafiony *Hunt for Marcus*). Odpadnięty gracz:

- **zostaje przy stole** i widzi wszystko,
- **traci swoje tury** (są pomijane),
- **nie może** grać kart ani używać umiejętności,
- **może** nadal głosować nad Modami Patusa i zmieniać swoje imię.

### Biblioteka Kart

Ikona książki w prawym dolnym rogu planszy otwiera **encyklopedię wszystkich
kart** w grze — z podziałem na Karty Ruchu, Mody Patusa, Karty Skrzyni i
Umiejętności. Widać w niej liczbę kopii każdej karty w bieżącej rozgrywce i
pozostałe ładunki umiejętności.

---

## 14. Interakcje między kartami i umiejętnościami

### Mody nie dotyczą wszystkiego

**Masa solna, Halloween, AKO, Speedrun i Gambit Patusa dotyczą wyłącznie Kart
Ruchu.** Umiejętność Dziada (*Skrypt*), Dzieckorolka, Balbinka i Gejtos działają
na pełnym dystansie i bez ograniczeń tych Modów.

### Gambit Patusa + Speedrun

Gambit rozstrzyga się **pierwszy**. Speedrun pyta o **już odwrócony** kierunek —
więc karta „cofająca" pod Gambitem jedzie do przodu i Speedrun **nie zaproponuje**
jej odwrócenia (bo to cofnęłoby Gambit, nie kartę).

### AKO + wszystko inne

AKO dołącza się **na końcu**, gdy ruch jest już w pełni ustalony. Dlatego:
- **Masa solna** skraca również ruch sąsiada,
- **ChatGPT** wydłuża również ruch sąsiada,
- **Gambit** i **Speedrun** decydują też o jego kierunku.

### Zamrożony pionek a karty

- Karta wskazująca **konkretny kolor** (Zerówka, Wejściówka, Fillerski
  przedmiot), który jest zamrożony — **nie podstawia innego pionka**.
- Karty działające na „pionek najbardziej z tyłu/przodu" (Przepis, Obniżenie
  progu, PAA) **pomijają** zamrożone i biorą następnego.
- **Umiejętności** w ogóle nie pokazują zamrożonych pionków na liście wyboru;
  **karty** je pokazują, ale wybranie ich nic nie da.

> ⚠ **DO POTWIERDZENIA:** gdy karta wskaże zamrożony pionek, **zostaje w ręce**
> (zagranie jest odrzucane). Gdy pinuje ją Halloween albo wskaże ukrytego pionka,
> karta **zostaje zagrana i odrzucona bez efektu**. Ta niespójność jest znana i
> czeka na decyzję właściciela gry.

### Wieże a kolejność zwycięstwa

Ponieważ Oprawcy wygrywają **pionkiem na dole wieży**, wszystko, co zmienia
kolejność w wieży, jest bronią:

- **Dzieckorolka** wkłada zgarnięte pionki **pod** jadącego,
- **Janek**, **PAA** i **Obóz Harcerski** kładą pionek **na szczycie**,
- **Balbinka** rozbija wieżę, bo nikt nikogo nie przenosi.

### Blokady a rzeczy, których zablokować nie można

„Nie masz Rosji" i „Przerwanie Systemowe" zatrzymują **zagraną kartę, która
porusza pionek**. Nie zatrzymają:
- umiejętności postaci,
- Modu Patusa,
- ręcznego przestawienia pionka.

### Sesja na PG wariant 2 a trwające efekty

Wariant 2 **kasuje efekty umiejętności już działające** na stole (np. zamrożenie
z Granny Costume, sklejenie Radarem). Wariant 1 tylko blokuje nowe użycia.

### Squid Game wyłącza wieżę

Dopóki Squid Game leży na stojaku, **budowanie wieży nie daje nic**. Jedynym
sprawdzeniem jest automatyczny check prowadzącego pionka raz na rundę. Piotrek
nadal wygrywa dojściem do mety.

### Kingmaker zmienia wszystko naraz

Wraz z rolą Piotrka przechodzą: miejsce w kolejności tur (co trzecia tura!),
limit Kart Skrzyni, strona przy wyborze Modów, Umiejętność Piotrka i warunki
zwycięstwa. **Nie przechodzą** prywatne notatki gracza.

### Alter Ego kasuje wiedzę Oprawców

Po Alter Ego **wszystkie dotychczasowe skreślenia przestają obowiązywać** —
Piotrek może wybrać kolor, który Oprawcy dawno „wykluczyli". Jedyny kolor, którego
wybrać nie może, to ten, który właśnie oddał.

---

## 15. Zwycięstwo i koniec gry

### Piotrek wygrywa, gdy

- jego ukryty pionek **stanie na mecie** (wariant 1),
- albo **którykolwiek** pionek stanie na mecie (wariant 2, jeśli włączony).

### Oprawcy wygrywają, gdy

- **wszystkie obecne pionki stoją na jednym polu**, a na **dole** wieży jest
  pionek Piotrka,
- albo **Squid Game** automatycznie sprawdzi prowadzący pionek i trafi,
- albo **Glockboy** trafnie wskaże pionek Piotrka.

### Co się dzieje na koniec

Tożsamość Piotrka **zostaje ujawniona** i gra się zatrzymuje. Nie da się już nic
zagrać. Do wyboru są: powrót do poczekalni (przy grze sieciowej), menu główne
albo wyjście z gry.

**Rewanż zaczyna się od zera:** nowa plansza, nowe talie, nowe ręce, nowe role i
nowa tajemnica. Zostają tylko: gracze, ich imiona, wybrane postacie i ustawienia
stołu.

### Sytuacje szczególne

- **Remisu nie ma.** Wygrywa ten warunek, który spełni się pierwszy.
- **W trakcie zamiany tożsamości** (Alter Ego / Kingmaker) nikt nie może wygrać
  — przez chwilę nie istnieje żadna ukryta tożsamość, więc sprawdzenie nie ma
  czego porównać.
- **Odpadnięcie Glockboya nie kończy gry** — Oprawcy grają dalej w mniejszym
  składzie.

---

## 16. Przydatne zasady i częste nieporozumienia

**Kolejność tur nie jest „w kółko".**
Piotrek gra co trzecią turę. Runda kończy się, gdy **każdy Oprawca** zagrał raz
— więc rundy mają różną długość, a ten sam Oprawca może zagrać dwa razy zanim
inny zagra raz. **Nie każda runda zaczyna się od Piotrka** — rytm biegnie przez
granicę rundy bez zerowania.

**Pionki są niczyje.**
Możesz ruszyć dowolny pionek, na który masz kartę. To, że ruszasz jakiś pionek,
niczego o tobie nie dowodzi — i o to właśnie chodzi.

**To, kto jest Piotrkiem, jest jawne. Tajny jest kolor.**
Widzisz Piotrka w pasku kolejności. Nie wiesz tylko, którym pionkiem jest.

**Wygrywa pionek na DOLE wieży, nie na górze.**
Zbudowanie wieży to dopiero połowa roboty — musi mieć właściwy kolor na spodzie.

**„Pełna runda" to nie „licznik rund".**
Prawie wszystkie efekty na „pełną rundę" kończą się, gdy **tura wróci do
właściciela efektu**.

**Ruch liczymy w numerach pól, nie w polach.**
12a i 12b to jedna pozycja. Przejście przez poszerzony rząd to **jeden** krok.

**Wybór połowy pola podwójnego jest tylko na końcu ruchu.**
Pola mijane po drodze rozstrzygają się same — z jednym wyjątkiem
(**Dzieckorolka**), gdzie każda połowa jest osobnym pytaniem.

**Karta nazywająca kolor to znaczy ten kolor.**
„Zerówka — czerwony" rusza czerwony pionek. Jeśli nie może, karta nic nie robi —
nie podstawia się innego pionka.

**Thunderfuck i Rage Quit na pustym stojaku nie robią nic.**
Obie karty *wymieniają* aktywne Mody. Zanim odbędzie się pierwszy wybór, nie ma
czego wymieniać.

**Przy remisie w głosowaniu wygrywa karta z lewej.**
Nie ma dogrywki.

**Odmowa sprawdzenia niczego nie ujawnia.**
Ice Block **anuluje** pytanie, a nie odpowiada na nie. Żaden kolor nie zostaje
skreślony.

**Umiejętności są zablokowane, dopóki ktokolwiek stoi na starcie.**
To zaskakuje w pierwszej rundzie. Przycisk pisze wtedy „PIONKI NA STARCIE".

**Sesja na PG nie zabiera ładunków.**
Blokada to nie zużycie. Przycisk pisze „SESJA NA PG", a nie „ZUŻYTE".

**Zablokowany ruch po prostu się nie odbył.**
Nie jest cofany — nigdy się nie wykonał. Nie mógł więc nic zbudować, sprawdzić
ani wygrać.

**Nazwa postaci to nie nazwa umiejętności.**
Big D Randy ma umiejętność *Granny Costume*; Lubin ma *Jazdy*. W panelu widzisz
nazwę **umiejętności**.

**Cofnąć ruch można tylko do momentu, aż następny gracz zagra.**
Kingmakera i zamiany tożsamości nie da się cofnąć w ogóle.

---

## Zasady wymagające potwierdzenia przy stole

Gra celowo **nie rozstrzyga** poniższych — nie zostały wymyślone ani
zaimplementowane na siłę:

| Element | Stan |
|---|---|
| **Karta Skrzyni „Shady"** | Brak mechaniki. Karta jest zagrywana i odrzucana, pokazuje swój tekst. |
| **Umiejętność Norbura „Plac"** | Brak mechaniki. Umiejętność zużywa ładunek i pokazuje tekst; ograniczenie ruchu ustalcie sami. |
| **Kolejność grup przy rozbiciu wieży** (wariant 2 sprawdzania) | Zaimplementowane: dolna grupa najbliżej, górna najdalej. Wymaga potwierdzenia. |
| **Karta na zamrożony pionek** | Zostaje w ręce, zamiast zostać odrzucona bez efektu jak przy Halloween. Niespójność znana, czeka na decyzję. |
| **Herold a własna postać** | Nie można skopiować własnej umiejętności. Zasada odziedziczona po wcześniejszej wersji karty; wymaga potwierdzenia. |
| **Liskowy Konkurs po cudzym ruchu** | Tekst mówi „po ruchu dowolnej osoby", gra pozwala tylko po ruchu własnym — a przy pilnowaniu kolejności tur w ogóle. Wymaga decyzji. |
