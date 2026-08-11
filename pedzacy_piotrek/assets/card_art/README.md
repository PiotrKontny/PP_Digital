# Card art — Signature Cards

Drop a picture in this folder and the matching card starts drawing itself as a
**Signature Card**: the artwork fills the whole face, the game writes the title
across the bottom, and hovering darkens the picture and reveals the rules text.

Every card **without** a picture here keeps the parchment face it has always
had. That is the point of the system — cards can be illustrated one at a time,
over months, and nothing has to be converted all at once.

## The whole workflow

1. Make the artwork.
2. Save it here, named after the card: `Troll.png`.
3. That's it. No JSON, no code.

## How a file finds its card

The filename and the card title are both reduced to a *key* and compared, so
the spelling does not have to be exact:

| File | Key | Matches the card titled |
|---|---|---|
| `Troll.png` | `troll` | Troll |
| `Rage Quit.png` | `rage_quit` | Rage Quit |
| `rage-quit.PNG` | `rage_quit` | Rage Quit |
| `Stanczyk.png` | `stanczyk` | Stańczyk |
| `Nie masz Rosji.jpg` | `nie_masz_rosji` | Nie masz Rosji |

Case, spaces, hyphens, punctuation and Polish diacritics all fold away, so
`Dzieckorolka.png`, `dzieckorolka.jpg` and `Dzieckorolka .PNG` are one name.

### Two decks, one title

`Shady` is both a Mod Patusa and a Chest card, and they want different
pictures. Put each in a subfolder named after its deck:

```
card_art/
    mods/Shady.png          -> the Mod Patusa
    chest/Shady.png         -> the Chest card
```

The deck folder is tried first, so a file in `movement/` still matches a
movement card by its bare name. A bare name that **two different** subfolders
both claim is ambiguous and is ignored rather than guessed at — the scoped
names keep working.

Deck folder names: `movement`, `mods`, `chest`, `characters`, `piotrek_skills`.

## Overriding the name, and opting out

Both are one line in `data/cards.json`, and neither is normally needed:

```json
{ "title": "Troll", "art": "wielki_troll" }   // use wielki_troll.png
{ "title": "Troll", "art": "chest/shady" }    // a scoped name
{ "title": "Troll", "art": "" }               // never use artwork
{ "title": "Troll", "art": false }            // the same thing
```

`art` is a **name, not a path** — no folder prefix beyond the deck scope, no
file extension. Use it when the picture cannot be called after the card, or
when a card must keep its parchment face even though a matching file exists.

## Formats

`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`.

PNG for anything with transparency or flat colour; **JPG for photographic
montages**, which is most of them — `Troll.png` is 1.1 MB and would be about a
tenth of that as a JPG. If both `Troll.png` and `Troll.jpg` exist, PNG wins.

## What to draw

* **Portrait, roughly 3:4** — the card is 140×200 (a 1:1.43 ratio). The picture
  is scaled to *cover* the card and centred, so the overflow on the long axis
  is cropped. Keep the subject centred and away from the very edges.
* **No title, no rules text, no frame.** The game draws the title and the
  description itself, which is what lets one picture serve the resting state
  and the hover state. A title baked into the artwork would appear twice.
* **Around 640 px wide is plenty.** The largest a card is ever painted is about
  460 px across, on a 4K display with the card enlarged; anything beyond that
  is bytes nobody sees.
* **Leave the bottom third calm.** A dark scrim fades up from the bottom edge
  and the title sits on it. Busy detail there still reads, but a calm area
  reads better.

## When a picture is missing or broken

Nothing happens, on purpose. A card whose file has been deleted, half
downloaded, or renamed to `.png` from something that is not a PNG falls back to
the **standard parchment card** and the game carries on. A card-art folder is
filled in by hand over months; it is expected to be incomplete, and an
incomplete folder must never be able to stop a game.

## Typography

Titles use a **display font** if one is present, and the bold UI font if not.
To change the look of every Signature Card at once, drop a font in
`assets/fonts/` under one of these names:

* `Display-Bold.ttf`
* `Display.ttf`
* `Title-Bold.ttf`

It must cover Latin Extended-A (ą ć ę ł ń ó ś ź ż) or Polish titles render as
boxes. Nothing proprietary — the project is non-commercial and redistributed
from GitHub, so use your own work, an SIL/OFL face, or something else you are
allowed to ship.

## Attribution

`Troll.png` is the project owner's own composition, cropped to the illustration
only. Record the source of anything added here that is not.
