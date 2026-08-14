# Card backs — one picture per deck

The back of a card is **artwork**, not a drawing. Every deck has its own, and
it is shown wherever the game shows a face-down card: the draw piles in the
left column, and Piotrek's two piles in the character panel.

## The five backs

| Deck | File | The card |
|---|---|---|
| `movement` | `movement.png` | Karty Ruchu |
| `mods` | `mods.png` | Mody Patusa |
| `chest` | `chest.png` | Karty Skrzyni |
| `piotrek_skills` | `piotrek_skills.png` | Umiejętności Piotrka |
| `characters` | `characters.png` | the character-exchange deck |

`characters` is the deck of **character-changing cards**. It is a deck, not a
player and not a role — the picture on its back says nothing about who anybody
is.

## Replacing one back

Two ways, and neither of them touches any code:

1. **Keep the name.** Overwrite `chest.png` with your new picture. Done.
2. **New name.** Put `chest_v2.png` in this folder and change that deck's one
   line in `pedzacy_piotrek/config/settings.py`:

```python
CARD_BACKS = {
    ...
    DECK_CHEST: "chest_v2.png",
    ...
}
```

That table is the **only** place a card-back file is named. Nothing in
`render/` or `ui/` knows a filename, so there is never a rendering function to
go and edit. If you find yourself opening `card_renderer.py` to change a
picture, something has been wired wrong — say so rather than working around it.

Restart the game to see the change; the pictures are loaded once and cached
(see `CardBackLibrary.refresh` if you ever need it live).

## What to draw

* **Portrait, roughly 3:4** — the card is 140×200 (1:1.43). The picture is
  scaled to **cover** the card and centred, so the overflow on the long axis is
  cropped. Nothing is stretched. Keep the emblem centred and away from the very
  edges: the shipped backs are about 1060×1490, so a few pixels come off the
  top and bottom.
* **The corners are rounded for you** to the same radius every other card uses.
  Square corners in the source are fine and correct.
* **No text.** A back carries no title, no count, no badge — nothing is drawn
  over it. Whatever is in the file is the whole picture.
* **Leave some headroom in the highlights.** Hovering a deck adds light to the
  whole back. A back that is already near-white loses its detail when it lights
  up; the shipped set is dark colour under bright metal, which is what makes
  the hover read.
* **About 640 px wide is plenty.** The largest a pile is ever painted is a few
  hundred pixels across. The shipped files are larger than they need to be —
  shrinking them is safe and saves a lot of bytes.

## Formats

Anything pygame can read: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`. Unlike
`card_art/`, the extension is written out in `CARD_BACKS`, so there is no
format ranking and no guessing — the table names one exact file.

## When a picture is missing or broken

Nothing happens, on purpose. A deck whose file has been deleted, half
downloaded, or renamed to `.png` from something that is not a PNG falls back to
the **drawn back** the game painted before these pictures existed: a bound
cover in that deck's own colour from `theme.deck_colors`. The game carries on,
one deck looks different, and nobody loses a match to a missing file.

That fallback is also what a clean checkout gets, which is the project's rule
for everything under `assets/`: it is an override, and the game runs without
it.

## Attribution

Record the source of anything added here. The project is non-commercial and
redistributed from GitHub, so keep to work you are allowed to ship.
