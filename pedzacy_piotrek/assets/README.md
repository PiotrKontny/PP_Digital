# Assets

Nothing here is required — the game draws every card, pawn, tile and prop
procedurally, so it runs on a clean checkout with no binary files at all.
Everything in this folder is an *override*: drop a file in, and the matching
drawn placeholder steps aside.

## Layout

| Folder | What goes in it | How it gets picked up |
|---|---|---|
| `images/cards/` | card artwork (PNG with alpha) | set `"image": "cards/kolos.png"` on the card in `data/cards.json` |
| `images/characters/` | character portraits | same, in `data/characters.json` |
| `images/board/` | tiles, trees, rocks, huts, bridges | referenced from a board theme in `data/board.json` |
| `images/ui/` | button and panel skins | referenced from the theme |
| `fonts/` | `.ttf` / `.otf` | see below |
| `sounds/` | short `.ogg` / `.wav` effects | played by name |
| `music/` | looping `.ogg` tracks | played by name |

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

Polish needs full Latin Extended-A coverage (ą ć ę ł ń ó ś ź ż) — check that
before committing a font, since a font missing those renders them as boxes.

## Licensing

The project is non-commercial and derived from *Pędzące Żółwie*, so keep to
assets you are allowed to redistribute: your own work, CC0, or CC-BY with the
attribution recorded here.
