# Serwer gry

Ten dokument opisuje jedną rzecz: jak sprawić, żeby dwie osoby z różnych krajów
mogły zagrać razem, nie dotykając routera.

## Dlaczego w ogóle jest serwer

Komputer w domu siedzi za routerem, który robi NAT. Router przepuszcza
połączenia **wychodzące** i odrzuca **przychodzące**, bo nie wie, do którego
komputera w sieci je skierować. Dlatego stara wersja gry — w której jeden gracz
„był serwerem” — działała tylko w jednej sieci albo po przekierowaniu portu.
Żadna sztuczka po stronie klienta tego nie obejdzie: to nie jest ograniczenie
gry, tylko tego, jak działa internet w domach.

Rozwiązanie jest takie, jakie stosują wszystkie komercyjne gry online: stoi
gdzieś maszyna z publicznym adresem, a **wszyscy gracze łączą się do niej na
zewnątrz**. Każdy router to przepuszcza, bo to zwykłe połączenie wychodzące —
takie samo jak otwarcie strony internetowej.

```
   Polska                                     Wielka Brytania
   ┌─────────┐                                   ┌─────────┐
   │ gracz A │──── wychodzące ──┐   ┌──────────  │ gracz B │
   └─────────┘                  ▼   ▼            └─────────┘
                            ┌──────────────┐
                            │    SERWER    │  ← publiczny adres
                            │  (ten kod)   │
                            └──────────────┘
```

Serwer to ten sam projekt, uruchomiony innym poleceniem. Nie trzeba niczego
osobno instalować ani pisać.

## Najszybsza wersja: sieć lokalna

Jeśli gracie w jednym mieszkaniu albo na jednym WiFi, nie trzeba niczego
wdrażać. W ekranie **Załóż grę** zaznacz *„Uruchom serwer na tym komputerze”*.
Gra wystartuje serwer w tle i sama się do niego podłączy; reszta wpisuje adres
tego komputera w sieci lokalnej (np. `192.168.0.12:51337`) w polu **Serwer gry**.

To **nie zadziała przez internet** — ten komputer wciąż jest za routerem.
Opcja jest podpisana w interfejsie właśnie po to, żeby nie wyglądała na
rozwiązanie problemu, którego nie rozwiązuje.

## Wersja właściwa: serwer w internecie

Potrzebujesz maszyny z publicznym adresem. Najtańsze sensowne opcje:

| Gdzie | Koszt | Uwagi |
|---|---|---|
| Railway / Render / Fly.io | darmowe plany wystarczają | najprościej, HTTPS/WSS z automatu |
| VPS (Hetzner, OVH, Mikr.us) | od kilku zł/mies. | pełna kontrola, trzeba samemu ustawić TLS |
| Komputer u znajomego z publicznym IP | za darmo | wymaga przekierowania portu **u tej jednej osoby** |

### Na platformie (Railway, Render, Fly.io)

1. Wrzuć projekt na GitHuba.
2. Utwórz nową usługę z tego repozytorium.
3. Komenda startowa:

   ```
   python -m pedzacy_piotrek.server
   ```

4. Nic więcej nie ustawiaj. Platforma sama poda port w zmiennej `PORT`, a
   serwer ją czyta — to jest ta jedna rzecz, której pominięcie sprawia, że
   wdrożenie „nie odpowiada”, mimo że proces działa.
5. Platforma da Ci adres w rodzaju `https://piotrek-abc123.up.railway.app`.
   W grze wpisz go jako **Serwer gry** — schemat `https://` gra sama zamieni na
   `wss://`.

Jest gotowy `Dockerfile`, gdyby platforma wolała obraz zamiast wykrywania
Pythona.

### Na własnym VPS-ie

```bash
git clone <adres-repo> && cd pedzacy-piotrek
pip install -r requirements.txt
python -m pedzacy_piotrek.server --host 0.0.0.0 --port 51337
```

Żeby nie umierało po wylogowaniu, usługa systemd:

```ini
# /etc/systemd/system/piotrek.service
[Unit]
Description=Serwer Pedzacego Piotrka
After=network.target

[Service]
User=piotrek
WorkingDirectory=/opt/pedzacy-piotrek
ExecStart=/usr/bin/python3 -m pedzacy_piotrek.server --port 51337
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now piotrek
```

Gracze wpisują wtedy `twoj-adres.pl:51337`.

### Szyfrowanie (TLS / `wss://`)

Serwer **nie obsługuje certyfikatów sam** i nie powinien. Standardowo stawia się
przed nim nginx albo Caddy, który zajmuje się certyfikatem i rozmawia z serwerem
zwykłym WebSocketem. Platformy z tabelki robią to za Ciebie.

Caddy, jeśli robisz to sam — dwie linijki i certyfikat z Let's Encrypt sam się
odnowi:

```
piotrek.twojadomena.pl {
    reverse_proxy 127.0.0.1:51337
}
```

Gracze wpisują wtedy `wss://piotrek.twojadomena.pl`.

Jeśli używasz certyfikatu z własnego CA albo samopodpisanego, ustaw w
`data/network.json` `tls.ca_file` (ścieżka do certyfikatu) albo — tylko na
własnej maszynie — `tls.verify: false`.

## Ustawienie adresu w grze

Żeby znajomi nie musieli za każdym razem wklejać adresu, wpisz go raz w
`pedzacy_piotrek/data/network.json` i rozdaj grę już ustawioną:

```json
{
  "server_url": "wss://piotrek.twojadomena.pl",
  "public_server_url": "wss://piotrek.twojadomena.pl"
}
```

Można też jednorazowo z linii poleceń:

```bash
python run_game.py --server wss://piotrek.twojadomena.pl
```

Albo zmienną środowiskową `PIOTREK_SERVER_URL`.

## Jak wtedy wygląda gra

1. Jedna osoba klika **Załóż grę** → dostaje **kod pokoju**, np. `K7M2QD`.
2. Podaje kod znajomym (głosem, na czacie — jak wygodnie).
3. Reszta klika **Dołącz do gry** i wpisuje sam kod. Bez adresów, bez portów.
4. Każdy wybiera postać, klika *Jestem gotowy*, host zaczyna.

## Wiele pokoi naraz

Domyślnie serwer prowadzi **jeden pokój** — tak było w wymaganiach. Kod od
początku obsługuje wiele; wystarczy podnieść limit:

```bash
python -m pedzacy_piotrek.server --rooms 20
```

albo `server.max_rooms` w `data/network.json`, albo `PIOTREK_MAX_ROOMS=20`.
Każdy pokój dostaje własny kod, własną grę i własny dziennik komend; nic poza
tą liczbą nie wymaga zmiany.

## Ustawienia sieciowe

Wszystkie w `pedzacy_piotrek/data/network.json`. Nie ma żadnych zaszytych
w kodzie.

| Klucz | Znaczenie | Domyślnie |
|---|---|---|
| `server_url` | dokąd łączy się gra | `ws://127.0.0.1:51337` |
| `connect_timeout` | ile czekać na połączenie | 8 s |
| `reconnect.enabled` | czy wracać po zerwaniu | `true` |
| `reconnect.initial_delay` / `max_delay` | odstępy między próbami | 0,5 s / 8 s |
| `reconnect.grace_period` | jak długo serwer trzyma miejsce nieobecnego | 180 s |
| `heartbeat.interval` | co ile wysyłać ping | 5 s |
| `heartbeat.timeout` | po jakiej ciszy uznać połączenie za martwe | 20 s |
| `tls.verify`, `tls.ca_file` | weryfikacja certyfikatu | `true`, brak |
| `server.host`, `server.port` | gdzie nasłuchuje serwer | `0.0.0.0`, 51337 |
| `server.room_idle_timeout` | po jakim czasie zamknąć pusty pokój | 900 s |
| `server.max_rooms` | ile pokoi naraz | 1 |

Zmienne środowiskowe (mają pierwszeństwo): `PORT`, `HOST`,
`PIOTREK_SERVER_URL`, `PIOTREK_SERVER_HOST`, `PIOTREK_SERVER_PORT`,
`PIOTREK_MAX_ROOMS`, `PIOTREK_SERVER_VERBOSE`.

## Kiedy coś nie działa

| Objaw | Prawdopodobna przyczyna |
|---|---|
| „Serwer … odrzucił połączenie” | serwer nie działa albo zły port |
| „Nie znaleziono serwera …” | literówka w adresie / nie ma takiej domeny |
| „Serwer przestał odpowiadać” | zerwana sieć — gra sama próbuje wrócić |
| „Nie ma pokoju o kodzie …” | kod z literówką albo pokój już zamknięty |
| „Niezgodna wersja gry” | gracze mają różne wersje — zaktualizujcie obie |
| Wdrożenie startuje, ale nikt się nie łączy | platforma podaje port w `PORT`, a serwer został zmuszony do innego |

Diagnostyka po stronie gry: `F3` w trakcie partii pokazuje adres serwera, kod
pokoju, stan połączenia, ping, liczbę wysłanych i odebranych wiadomości oraz
sumę kontrolną stanu gry. Jeśli dwie osoby mają różne sumy, doszło do rozjazdu —
gra wykrywa to sama i dociąga stan z serwera.

Diagnostyka po stronie serwera:

```bash
python -m pedzacy_piotrek.server --verbose
```
