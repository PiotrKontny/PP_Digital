# Assets

Nothing here is required — the game draws every card, pawn, tile and prop
procedurally, so it runs on a clean checkout with no binary files at all.
Everything in this folder is an *override*: drop a file in, and the matching
drawn placeholder steps aside.

## Layout

| Folder | What goes in it | How it gets picked up |
|---|---|---|
| `card_art/` | **full-card artwork** (Signature Cards) | named after the card — `Troll.png`. See `card_art/README.md` |
| `card_backs/` | **the back of a card**, one per deck | listed in `settings.CARD_BACKS`. See `card_backs/README.md` |
| `portraits/` | **character portraits** (and the placeholder) | named after the character — `Glockboy.png`. See `portraits/README.md` |
| `images/cards/` | small in-card illustrations | set `"image": "cards/kolos.png"` on the card in `data/cards.json` |
| `images/characters/` | small in-card character illustrations | same, in `data/characters.json` |
| `images/board/` | tiles, trees, rocks, huts, bridges | referenced from a board theme in `data/board.json` |
| `images/ui/` | button and panel skins | referenced from the theme |
| `fonts/` | `.ttf` / `.otf` | see below |
| `sounds/` | short `.ogg` / `.wav` effects | played by name |
| `music/` | looping `.ogg` tracks | played by name |

## Two kinds of card picture — do not mix them up

| | `card_art/` | `images/cards/` |
|---|---|---|
| What it does | **replaces** the card face | sits **inside** the parchment body |
| Addressed by | the card's name | a path in `"image"` |
| Card looks like | a Signature Card: art, title, hover description | the normal beige card, with a picture in it |
| Configuration | none — the filename is the link | one line of JSON |

A card may use either, neither, or both; if both, the Signature face wins and
`image` is simply unused. Everything about the first column is in
`card_art/README.md`; the rest of this file is about the second.

**Neither of them is `portraits/`.** A file in `card_art/` is the FACE of a
card and is named after the CARD; a file in `portraits/` is a CHARACTER's face
and is named after the CHARACTER. For a character card these are two different
names for two different pictures:

| | Folder | File |
|---|---|---|
| Big D Randy, the character | `portraits/` | `Big D Randy.png` |
| Granny Costume, his ability | `card_art/` | `Granny Costume.png` |

Neither lookup can reach the other's folder. See `portraits/README.md`.

**Neither of them is `card_backs/`.** Those two are the FRONT of a card and
belong to one card each; a file in `card_backs/` is the BACK and belongs to a
whole deck. They never resolve through each other — a card cannot borrow its
deck's back, and a deck cannot show a card's art. See `card_backs/README.md`.

## Adding a card with a picture

Add one entry to `data/cards.json` — no Python:

```json
{
  "title": "Nowa karta",
  "text": "Opis działania karty.",
  "count": 2,
  "image": "cards/nowa_karta.png",
  "badge": {"type": "pawn", "pawn": "zielony", "sign": "+"}
}
```

Card art is drawn into the card's picture window and scaled to fit; anything
around 420×300 px looks right. If the file is missing the card still renders,
just with its drawn placeholder, so a half-finished art pass never breaks the
build.

## Fonts

`FontBook` prefers a bundled font over the system one, so the game looks the
same on every machine. Name them exactly:

* `fonts/UI-Regular.ttf`
* `fonts/UI-Bold.ttf`

Signature Card titles additionally look for a **decorative** face, and fall
back to the bold one above when there is none:

* `fonts/Display-Bold.ttf`
* `fonts/Display.ttf`
* `fonts/Title-Bold.ttf`

Polish needs full Latin Extended-A coverage (ą ć ę ł ń ó ś ź ż) — check that
before committing a font, since a font missing those renders them as boxes.

## Licensing

The project is non-commercial and derived from *Pędzące Żółwie*, so keep to
assets you are allowed to redistribute: your own work, CC0, or CC-BY with the
attribution recorded here.
