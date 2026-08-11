# CHANGELOG_LLM.md

Development log of "Pędzący Piotrek". One section per stage. Append a new
section at the end of every stage and update `LLM_Instructions.txt` to match.

---

## Stage 1 — Architecture refactor
**Date:** 2026-08-01

### Starting point
A single `game.py` of 2196 lines: game state, rules, layout, rendering and the
event loop interleaved in the same functions. Everything worked, but nothing
could be changed in isolation and there was no place to intercept "what the
player just did", so networking was impossible.

### Implemented features
- Full package split into `config`, `data`, `cards`, `players`, `board`,
  `engine`, `render`, `ui`, `net`, `assets`.
- Command/Event architecture: 13 serialisable commands, 15 events, an event bus.
- Seeded determinism: one `SessionConfig.seed` drives board generation, every
  deck shuffle (one RNG per deck) and character dealing.
- Procedural board generation: Catmull-Rom road with arc-length
  parametrisation, rivers with bridges, villages, forests, rocks, camp, finish.
- Rendering engine: camera with smooth zoom/pan, static world painted once and
  blitted per frame, particles, cached text/shadows/glows, card renderer.
- Data-driven content: `cards.json`, `characters.json`, `board.json`, validated
  at startup.
- Networking layer: JSON-line protocol, `Transport` ABC, `LoopbackTransport`,
  `LocalSession` / `HostSession` / `ClientSession` with deterministic lockstep.
- 75 tests; headless self-test; screenshot tool.

### Architecture changes
- `engine/` imports no pygame and is fully testable without a display.
- All layout arithmetic moved into one `Layout` class.
- Card badges moved from title string-matching into JSON declarations.
- Role detection moved from title comparison to a JSON `"role"` field.

### Bug fixes
- Scenery could grow through the starting camp panel.
- The same geometry (`Kolory Piotrka` grid) was computed in two places and
  could drift apart.

### Notes
Gameplay was preserved exactly: turn cadence, mod rack push/overflow, stacking,
colour notepad, round counter, renaming, free pawn dragging.

---

## Stage 2 — From prototype to a digital board game
**Date:** 2026-08-02

### Goal
Make it feel like a real digital board game: fix the board generator, put the
board at the centre of a new layout, give the hand a proper fan, and make the
first cards actually playable. Multiplayer deliberately untouched.

### Implemented features

**1. Board generation fixed (the overlap bug).**
Neighbouring fields could nearly overlap on tight bends. Root cause: nothing
bounded the road's curvature — the previous parameters produced a minimum turn
radius of ~35 px where ~290 px was needed. The fix is derived rather than tuned:
- required turn radius follows from how much a bend compresses the inner lane
  (`BoardLayout.min_turn_radius`);
- for a sine of amplitude A the tightest radius is `L²/(4π²A)`, so the
  wavelength is now chosen from the amplitude;
- the finished road is then *measured* (curvature via circumscribed circles,
  self-approach via resampled point pairs) and, on failure, the amplitude drops
  12% and generation retries. Amplitude zero is a straight road, so the search
  always terminates.
Added `BoardModel.verify_spacing()` and the `BoardGeometry` record. Verified on
120+ generated boards: zero violations, ~32 ms per board.

**2. Horizontal board.** Generation now works in along/across coordinates
(`BoardLayout.orientation`). The camp is a column behind the start line; rivers
run across the road so bridges still meet it square.

**3. New layout, board-centred.** Left column: active Mody Patusa + the three
table decks. Centre: turn-order ribbon and round counter, player list, and the
board taking the majority of the screen. Right column: character, ability,
character/skill decks, colour notepad. Bottom: the hand fan, full width.

**4. Hand fan.** Cards sit on an arc, overlap, tilt with their position, and
under the cursor rise, straighten, scale up and come to the front. New cards fly
in from the deck side. All motion is frame-rate independent.

**5. Playable movement cards.** New `PlayCard` command, `CardPlayed` and
`TokenWalked` events, and `engine/effects.py` which resolves a card into a
`Plan` of pawn routes or a `Refusal` with a reason. Click a card to play it,
or drag it onto the board; while dragging, the route is highlighted and the
card's border turns green or red. An illegal play returns the card to the hand
and explains why. Cards without an implemented effect keep the prototype's
click-to-discard behaviour.

**6. First card effects.** 20 card definitions (55 physical cards of 69) now
have deterministic effects declared in JSON: `Zerówka` ×6, `Fillerski
przedmiot` ×6, `Wejściówka` ×6, `Przepis`, `Obniżenie progu`. Movement clamps at
both ends, carries riders, and enters the board from the camp.

**7. Pawns walk.** `TokenWalked` carries every field on the route; the view
follows it segment by segment with a hop per field. Riders set off a beat later
so a tower moves like a tower. Nothing teleports.

**8. Easier stacks.** Hovering a tower fans it into a ring around its field so
every pawn gets its own target; the ring closes when the cursor leaves. The pick
radius is now the larger of a world distance and a *screen* distance, so pawns
stay grabbable when zoomed out.

**9. Recently played strip.** The last three played cards fly to the board's
top-right corner, sit for six seconds and fade. Fed from `CardPlayed` events,
so it will show remote players' plays unchanged once networking lands.

**10. Responsive UI.** The fixed design canvas is gone; the game draws at the
window's real resolution and `Layout` recomputes on resize. Verified at
1280×760, 1600×900, 1920×1080 and 2560×1440 — no overlaps, nothing off-screen,
and a bigger window means a bigger board rather than bigger pixels.

**11. Multiplayer preparation.** Playing, discarding and moving are all
Commands; nothing in the new interaction path bypasses them. The engine resolves
card targets itself, so a client can say which card it played but not what that
card does.

### Architecture changes
- `Layout` rewritten: `resize(w, h)`, everything derived, no fixed coordinates.
- `App` draws straight to the window; `Screen.on_resize` hook added.
- `CardRenderer` paints faces at their final size and caches them by content,
  size and state; added `draw_transformed` for the fan.
- `Camera.set_viewport()` for window resizes.
- `CardDef` gained an optional `EffectSpec`; `Card.is_playable`.
- `Deck.find_discarded()` so the recently-played strip can look a card up after
  the engine let go of it.
- `Renderer.text()` accepts every rect anchor.
- New modules: `engine/effects.py`, `ui/hand_fan.py`, `tools/inspect_frame.py`.

### Bug fixes
- Fields could overlap on tight bends (see above).
- `board_renderer` drew glows to the renderer's default surface instead of the
  target it was given — invisible in the game, but it made off-screen rendering
  raise.
- The settings screen could push its Start button off the bottom of a 1280×760
  window; its rows now scale with window height.

### Tests
75 → 135. New coverage: the spacing guarantee across many board sizes and
seeds, road curvature and self-distance bounds, horizontal orientation, river
direction, effect resolution (including refusals, clamping, riders and
deterministic tie-breaking), card play by click and by drag, the fan's geometry
and hover behaviour, stack fanning and pick radius, walk animation visiting
every field, the recently-played strip, and layout invariants at four
resolutions.

### Notes / known limitations after this stage
- Cards needing a player decision are still inert (14 movement cards, all chest
  and mod cards); `PlayCard.target_pawn` is the hook for them.
- No win conditions, no turn enforcement.
- Networking still has no socket transport and no lobby, and a client still
  simulates the full state (hidden-information filtering is the remaining piece).
- Board generation runs synchronously at game start (~30 ms).

---

## Stage 3 — Usability, polish and board behaviour
**Date:** 2026-08-02

### Goal
Make the game feel closer to a commercial digital board game: better board
handling, a real meaning for widened rows, and a pass over every small visual
inconsistency. No new card mechanics.

### Content
Adopted the updated `cards.json` and `characters.json` (text edits, real
character abilities in place of the `[TBA]` placeholders, and "Troll" going from
one copy to two). The movement deck is now 70 physical cards; the deck-size test
was updated and rewritten to say plainly that a changed count needs confirming.

### Implemented features

**1. Board dragging.** Left-dragging empty ground pans the map. Pawns win the
gesture, so picking a pawn up still works, and dragging a card from the hand is
untouched. The pan skips the camera's smoothing (`pan_screen(immediate=True)`) —
with smoothing the board lagged a few pixels behind the cursor and the grab felt
loose.

**2. Recently played.** Cards now stay 11 s (was 6) before fading. Hovering one
enlarges it 2.15×, draws it above the entire interface (a separate
`draw_overlay` pass after the hand fan) and pauses its countdown, so a card can
be read without hurrying. An enlarged card grows towards the middle of the board
so it never spills off-screen.

**3. Character validation.** If every seat has a hand-picked character and none
of them is Piotrek, the Start button is disabled and a red message explains why.
One seat left on "random" is enough, because the dealer guarantees Piotrek then.

**4. Doubled positions (12a / 12b).** Fields and *board positions* are now
different things: a widened row is one position holding two fields, and movement
counts positions. Labels changed from `12, 13` to `12a, 12b`.
- Landing on a widened position stops the move. The engine emits
  `ChoiceRequired`, the interface goes modal (both halves pulse with labelled
  bubbles), and nothing continues until the player clicks one.
- The answer travels in `PlayCard.target_tile`, so it replays identically over
  a network, and the engine validates it against the destination's own fields.
- Passing *through* a widened position asks nothing: the pawn takes the nearer
  half.
- How often rows widen is configurable: `double_frequency` on the board theme,
  in `SessionConfig`, as a menu control, and as `--doubles N` on the command
  line.

**5. Card shadows.** A card's shadow is now its rotated silhouette (RGB
multiplied to zero, keeping the alpha), softened by a scale-down/scale-up pass
and cached by quantised angle and scale. Previously an upright rectangle sat
under every tilted card.

**6. Deck hover.** The halo around a deck is gone. The top card of the pile
brightens and lifts two pixels instead, animated exponentially; the cards
underneath stay dark so it does not look like a light bulb.

**7. Board background.** A seamless countryside tile fills the whole viewport
behind the board and scrolls at 0.45 parallax, with a soft shadow where the map
meets it. Zooming right out no longer leaves the board floating on panel colour.

**8–9. Responsive and visual polish.**
- Deck names and their two counters were being drawn over each other; each deck
  now has a two-line label band, in both the left column and the character
  panel.
- The right column's content is centred vertically, so a hunter's shorter panel
  no longer hugs the top with a hand's width of dead space below.
- Verified at 1280×760, 1600×900, 1920×1080, 2560×1440 and 3840×2160: no
  overlaps, nothing off-screen, nothing unpainted.

### Architecture changes
- `Tile` gained `slot`, `variant` and `label`; new `BoardPosition` groups the
  fields of one position.
- `BoardModel` gained `positions`, `position()`, `position_count`,
  `last_position`, `position_of_pawn()`, `tiles_at_position()`,
  `tile_by_label()`.
- `make_rows()` takes an optional frequency and RNG as an alternative to the
  fixed pattern.
- `effects` gained `NeedsChoice`, `tile_route()` and `preview_tiles()`;
  `PawnRoute` now carries both the positions and the concrete fields.
- `PlayCard.target_tile`, `ChoiceRequired` event, `TokenWalked.tiles`.
- `Camera.pan_screen(immediate=...)`.
- `CardRenderer._silhouette()`, `back(brightness=...)`,
  `draw_pile(brightness=...)`.
- `BoardRenderer._build_backdrop()` / `_draw_backdrop()`.
- `Layout.section_line_h`, `right_content_height()`, `right_content_offset()`.
- `GameScreen.pending_choice` with a modal input path.

### Bug fixes
- **`create_game` silently dropped session settings.** It rebuilt
  `SessionConfig` field by field to stamp in the seed, so `double_frequency`
  never reached the board generator. Now uses `dataclasses.replace`. A test
  walks every field of the dataclass so the next added field cannot repeat it.
- Deck names overlapped their card counters in both side panels.
- The right column's contents sat at the top with a large gap underneath.

### Tests
135 → 171. New coverage: a/b labelling and the choice flow end to end (engine
and interface, including that the rest of the UI is blocked while the table
waits and that Esc returns the card), configurable double frequency, board
dragging including the three gestures it must not steal, the backdrop filling
the viewport and parallaxing, rotated shadows, the recently-played hover, menu
validation (including that the message is actually red), deck-label spacing at
three resolutions, and right-column balance.

### Notes / known limitations after this stage
- Still no card mechanics beyond deterministic movement; that is the next stage.
- A pending choice belongs to whoever is at the keyboard — there is no "the
  table is waiting for player 3" yet, though `ChoiceRequired` carries the seat.
- The right column is sized for Piotrek's taller panel; a hunter's is centred in
  the leftover space rather than growing into it.
- Screenshots could not be inspected visually during this stage (the image
  viewer returned nothing), so verification was numeric: per-region paint
  coverage, overlap and off-screen checks, and text-rect collision tests. The
  layout is provably non-overlapping, but a human should still eyeball the new
  backdrop and the 12a/12b overlay once.

---

## Stage 4 — The gameplay engine
**Date:** 2026-08-02

### Goal
Stop hard-coding card behaviour and build the machinery that will carry every
future card and ability: a registry of effects, a vocabulary of operations, and
persistent gameplay states. Then implement the abilities that do not depend on
the checking mechanic.

### Content
Adopted the updated `cards.json` and `characters.json` (ChatGPT moved from 1x to
5x uses). Extended both with structured fields, keeping every description
verbatim: `ability`, `uses`, `passive`, `presentation`, and an `effect` for Seks
z pedałami. Dropped the blank placeholder card at the end of the chest deck — a
nameless card would otherwise have been dealt to somebody.

### Implemented features

**1. Card effect engine.** `EffectSpec` is now a `type` plus a free-form
parameter bag, and `engine/effects.py` is a registry: `@effect("move_pawn")`,
`@effect("freeze_pawn")` and so on. A handler is a **pure function** that returns
a `Plan` of **Operations** (`MovePawn`, `GrantStatus`, `ClearStatus`,
`SpendStatus`, `PlayRandomCard`, `Announce`), and `GameState._execute` applies
them — one mutation point, one dispatch table, no card-title branching anywhere.
Adding a card is a JSON entry; adding a new kind of behaviour is one handler.

**2. JSON as the source of truth.** No gameplay text or number is written in
Python. Two tests assert that every `effect` and `ability` in the data has a
registered handler, so a typo in the JSON fails the suite instead of failing
silently at the table.

**3. Use counts.** `uses` is a structured field; the counter (`uses_left`) lives
on the `Card`, so it travels with the physical card. The interface shows "Użyj
(2/2)" from that number rather than parsing "2x" out of the description, which
stays human-readable.

**4–5. Character abilities.** A generic ability system: every character card
declares an executable ability, activated from a button under the ability card.
Implemented: **Big D Randy** (freeze a lone pawn), **Lubin** and **Dziubdziuch**
(take away Piotrek's move), **Mitoman** (front-most pawn onto the back-most),
**Norbur** (confine movement to the span between the outermost pawns, minimum
gap enforced), **Dziad** (chosen pawn, one or two fields, either way),
**Ondrej** (link two pawns so moving one moves both), **Atencjusz** (extra
turn). **Glockboy** reports that checking does not exist yet and spends nothing.
Uses decrement automatically and the ability becomes unavailable at zero.

**6. Piotrek's skills.** Dealt at game start. **ChatGPT**: passive — two
movement cards fewer and one chest slot instead of two, both declared as JSON
`passive` values, not code; active — the next movement card reaches one field
further, five times, implemented as a `MOVEMENT_BONUS` status with one charge
that movement spends. **Ice Block** reports the missing checking mechanic.
**Dług u Tomasza** records its state as a `FORBIDDEN_ADJACENCY` status for the
future validation.

**7. Chest card limit.** Piotrek holds 2, hunters 1 (ChatGPT drops Piotrek to
1). Drawing over the limit opens a modal overlay showing every candidate side by
side, with the new card pre-selected; the player picks what to keep and the rest
is discarded through a `KeepChestCards` command.

**8. Gamechanger.** Declared in the JSON as a `role_reveal` presentation. On
draw it appears large in the middle of the screen as "Gamechanger — Zmienia
funkcję w zależności od gracza", then flips after about a second into **Alter
Ego** for Piotrek or **Kingmaker** for a hunter, and only then settles into the
hand. Presentation only, as specified.

**9. Seks z pedałami.** Declared as a `random_movement_card` effect. Playing it
dwells on the card for two seconds, then reveals a random *playable* card from
the movement deck and executes that card's effect. Both cards appear in the
Recently Played strip. The draw uses the game's seeded RNG inside the executor,
so every machine reveals the same card — verified by a test.

**10. Gameplay states.** `engine/statuses.py`: `StatusKind` (FROZEN, SKIP_TURN,
LINKED, EXTRA_TURN, MOVEMENT_BONUS, RESTRICTED_MOVEMENT, FORBIDDEN_ADJACENCY,
CHECK_REFUSAL), attached to a pawn, a player or the table, expiring against a
turn counter that advances with the seat. Rules query the tracker; nothing uses
one-off flags. Statuses are in the snapshot, so they cross the network already.

**11. Animation and presentation.** New `ui/overlays.py`: a generic
`ChoicePrompt` that renders whatever question the engine asks (with coloured
pawn buttons), a `RevealOverlay` with a card-flip transition, and the chest
`ChestChoice` overlay. Frozen pawns get an icy ring on the board and linked
pawns a line drawn between them, because a state nobody can see looks like a
bug when it refuses a card.

### Architecture changes
- `EffectSpec` generalised to `type` + `params`; `Presentation` added.
- `CardDef.ability` / `.uses` / `.passive` / `.presentation`; `Card.uses_left`,
  `spend_use()`, `ability_available`.
- `PlayCard.target_pawn` / `.target_tile` replaced by a single `choices` map;
  new commands `UseAbility` and `KeepChestCards`.
- New events: `ChoiceRequired` (now generic), `StatusGranted`, `StatusEnded`,
  `AbilityUsed`, `AbilityUnavailable`, `CardRevealed`, `CardTransformed`,
  `ChestLimitReached`.
- `GameState`: `turn_counter`, `statuses`, `_execute`, `_OPERATIONS`,
  `chest_limit()`, `_after_draw()`.
- `setup.assign_piotrek_skill()` and `starting_hand_size()` honouring passives.
- `Layout.ability_button_rect`, `choice_prompt`, `reveal_*`, `chest_choice_*`.

### Bug fixes
- The right column did not budget for the new ability button, so the button
  would have covered the deck label under it; the column now reserves the space
  and a test checks it at three resolutions.

### Tests
171 → 220, including a new `tests/test_abilities.py` (34 tests). Coverage: the
registry itself (every ability and effect in the data has a handler; registering
a new effect needs nothing else), use counting and refusals costing nothing,
each implemented ability, ChatGPT's passive and its five charges, status expiry
across turns, snapshot round-tripping, the deterministic random reveal, the
chest limit end to end, Gamechanger's two role variants, and the interface paths
for all of it — including that a prompt blocks the rest of the screen, that Esc
abandons an ability without spending it, and that clicking past a reveal still
performs the click.

### Notes / known limitations after this stage
- Cards with no `effect` in the JSON remain inert: Troll, Janek, Stańczyk, Spy,
  Thunderfuck, Plagiat, and all mods and chest cards. The machinery is there;
  they need declarations.
- Gamechanger/Alter Ego/Kingmaker are presentation only, per the brief.
- SKIP_TURN and EXTRA_TURN are recorded and displayed but not enforced, because
  there is still no turn enforcement; FORBIDDEN_ADJACENCY waits on checking.
- The checking mechanic is now the main blocker: three abilities and the win
  conditions all depend on it.

---

## Stage 5 — Completing the gameplay systems
**Date:** 2026-08-02

### Goal
Finish what stage 4 started: make the cards that were left inert work, give the
"choose a token" mechanic one reusable implementation, and prepare the interface
for multiplayer without implementing any networking.

### Implemented features

**1. Gamechanger fix.** The animation was right but the card underneath it was
still a Gamechanger. `Card.transform()` now swaps the definition while keeping
the uid, so the card that lands in the hand really is **Alter Ego** (Piotrek) or
**Kingmaker** (hunters), and everything already holding a reference to it
follows along. `Deck.return_card()` restores the printed card, so the chest deck
keeps containing a Gamechanger rather than slowly filling with Kingmakers.

**2. Generic target token selection.** The machinery existed from stage 4; this
stage made it look like what it is. A `Choice(kind="pawn")` now renders the
pawns as **coloured tokens** with a glow and a highlight, not as named buttons,
and the same pawns are ringed on the board — the player may click either. One
implementation serves cards and abilities alike, and a future effect that needs
a pawn needs no interface work.

**3. Missing movement cards.** Declared in JSON, no new code: **Janek** (chosen
pawn straight onto the pink one, reusing `stack_pawn` with a literal
destination), **Kolos z paki** (+1), **Astral 2019** (+2), **Astral 2022** (−2).
Janek's wording changed from "Rusz" to "Porusz".

**4. Thunderfuck.** New `draw_into_mods` effect and a `DrawIntoMods` operation.
The drawn mod takes the first free rack slot; with no free slot it falls back to
the existing push-and-discard behaviour, so it never quietly vanishes. The rack
logic was factored into `GameState._install_mod` and is now shared with playing a
mod by hand.

**5. Edit Mode.** A setup option, defaulting to on. On, anybody at the keyboard
plays every seat — the prototype's behaviour. Off, only the local seat may act:
the game starts on it, other tiles are dimmed and marked "cudze miejsce", Tab
does nothing, and the engine refuses every player-owned command for another
seat. The rule lives in `GameState.may_control()`, which is exactly what the
host will use once the game is networked.

**6. Polish.** `EffectSpec.needs_choice` now treats *any* parameter set to
`"choice"` as a decision, so it did not need updating for Janek's `source` and
will not for the next effect either.

### Architecture changes
- `Card.transform()` / `restore()` / `original_definition`; decks restore on
  return.
- `Card.is_playable` split from `Card.resolves_without_asking`.
- New operation `DrawIntoMods`; new effect handler `draw_into_mods`;
  `GameState._install_mod()` shared by both ways into the rack.
- `SessionConfig.edit_mode` and `local_seat`; `GameState.may_control()`,
  `_reject_foreign()`, `_OWNED_BY_PLAYER`.
- `ChoicePrompt` draws pawn options as tokens (`_draw_pawn_option`).

### Bug fixes
- **`is_playable` conflated two questions.** It meant "has an effect *and* needs
  no decision", so clicking a select-a-token card discarded it instead of
  playing it. Split into `is_playable` and `resolves_without_asking`; the random
  reveal uses the latter, since the executor cannot open a prompt mid-plan.
- **The random reveal never discarded the card it revealed.** It stayed in the
  draw pile and could be turned up again. It now goes to the discard pile like
  any other played card.
- **`SessionConfig.normalised()` dropped new fields** — the same rebuild-by-hand
  trap that hid `double_frequency` in stage 3 hid `edit_mode` here. It uses
  `dataclasses.replace` now, and a test walks every field of the dataclass.

### Tests
223 → 249. New coverage: the transformation (in hand, and restored on the way
back to the deck), each newly playable card end to end through the prompt,
Thunderfuck's three rack cases, edit mode on both sides of the engine boundary
and in the interface, the prompt rendering actual pawn colours, answering a
target question from the board as well as from the prompt, and that cards and
abilities go through the same selection system.

### Notes / known limitations after this stage
- Four movement cards remain inert, each needing a mechanic that does not exist:
  Troll, Stańczyk, Spy, Plagiat! (its second half makes another player act).
- All Mody Patusa and chest cards are still text for humans.
- Alter Ego and Kingmaker are cards with no effect yet — the transformation is
  complete, the gameplay behind it is not.
- Edit mode off is a local rule for now; `local_seat` is always 0 until a lobby
  assigns one.

---

## Stage 6 — Multiplayer
**Date:** 2026-08-02

### Goal
Let a few friends play together over the internet: one host, everyone else
connecting directly, gameplay synchronised by actions rather than by state. No
matchmaking, no servers, no accounts.

### Implemented features

**The wire.** `net/tcp.py` — non-blocking, polled TCP sockets behind the
existing `Transport` interface, the only file in the project that knows sockets
exist. No threads: a thread would need locks around the game state. Failures are
*states*, not exceptions — a dropped peer sets `connected = False` and records a
readable reason, so nothing above has to wrap calls in try/except to survive
somebody closing their laptop. `local_ip_addresses()` finds the address friends
should actually type by asking the routing table, because the hostname usually
resolves to 127.0.0.1.

**The lobby.** `net/lobby.py` — a small replicated document: the host owns it and
broadcasts it whole (a few hundred bytes, changing a handful of times before the
game starts; this is the one place where sending state is obviously right). A
client may *ask* for a nickname or character and the host decides. Duplicate
characters are refused host-side, not merely hidden in the client's dropdown.
Validation is the same as the single-machine screen, including the Piotrek rule.
Empty nicknames become "Player", and a second Kuba becomes "Kuba (2)".

**Services.** `net/service.py` — `HostService` and `ClientService` sit between
the sockets and the screens, so `ui/` never sees a message and `net/` never sees
pygame. Starting a game sends the `SessionConfig` *including the seed* plus the
seat map; every peer then builds the game itself. The game state never crosses
the wire.

**Screens.** `ui/network_screens.py` — main menu (Host / Join / local hot-seat /
Quit), host setup (nickname, port, table settings), join form (address, port,
nickname, with real error messages), and the lobby (seats, nicknames, character
dropdown that hides what is taken, host-only Start with the reason it is
disabled, and the host's address and port shown large).

**Ownership and turns.** `GameState.may_control` (whose seat) and `may_act`
(whose turn) now gate every player-owned command, with `authorise_remote` for
commands arriving over the wire — the seat comes from the host's own map, never
from the message. Private bookkeeping (colour notepad, renaming, answering the
chest limit) is deliberately not turn-bound. The game screen shows whose turn it
is and says plainly when you are waiting.

**Leaving and losing the connection.** An Esc pause menu offers resume, leave and
quit. The host leaving sends everyone a reason and returns them to the main menu;
a client dropping frees its lobby seat and the table carries on. Every failure
path ends in a message, not a crash.

**Debug panel.** `ui/debug_panel.py`, F3, off by default: mode, port, addresses,
connection state, peers and their seats, ping, sent/received counters with the
last action each way, turn and round — and a SHA-1 fingerprint of the snapshot,
which is the fastest way to spot a desync: two machines showing the same hash
are in step.

### Architecture changes
- `GameState.apply(command, local=True)`; `authorise_remote()`, `may_act()`,
  `_TURN_BOUND`.
- `HostSession` gained a seat map, `owns()`, departures and `NetworkStats`;
  `ClientSession` gained heartbeat/ping, `disconnected`, and stats.
- New: `net/tcp.py`, `net/lobby.py`, `net/service.py`,
  `ui/network_screens.py`, `ui/debug_panel.py`, `overlays.PauseMenu`,
  `widgets.TextInput`.
- `main.py` opens the main menu; `--host`, `--join`, `--port`, `--net-debug`.

### Bug fixes
- **Card uids were not deterministic.** They came from a process-global counter,
  so two games built in one process numbered their cards differently — and a
  command names a card *by uid*. Uids now come from deck position. This would
  have shown up in a real session as a mysterious "card not in hand" desync.
- **The turn check fired on replayed commands.** It lived inside the
  `SetActivePlayer` handler, so a client applying the host's authoritative
  command re-judged it against its own seat and refused it. Authorisation moved
  to the local-only path.

### Tests
249 → 307, in two new files. `test_multiplayer.py`: real sockets on loopback
(connect, a 500-message burst arriving intact and in order, a closed connection
reported rather than raised, unreachable hosts), the lobby (seat limits, unique
nicknames, character conflicts, the Piotrek rule, JSON round-trip, config
building), host/client play (ownership, turn gating, private bookkeeping off
turn, full exchanges ending in identical snapshots, late joiners, latency), and
the service layer end to end. `test_network_ui.py`: menu flow, join errors,
taken ports, lobby fill and start, three machines reaching identical states,
in-match spectating, leaving, host disappearance, and the debug panel's
fingerprint matching across machines.

### Notes / known limitations after this stage
- **Hidden information is not filtered yet.** A client simulates the full state,
  so a modified client could read another hand. Fine among friends on an
  unmodified build; it must be fixed before any public release.
- No reconnection: a dropped client is out of that match.
- No "return to lobby" mid-match — leaving ends the match for everyone.
- The turn still passes manually; the cadence in `turn_order.py` is displayed
  but not enforced.
- Tested on loopback only. Playing across the internet needs the host to forward
  the port; nothing in the code assumes a LAN.

---

## Stage 7 — Multiplayer polish and bug fixes
**Date:** 2026-08-03

### Goal
Fix what the first multiplayer pass got wrong and make testing it bearable. No
new gameplay.

### Fixed

**1. Overlapping text in the menus.** Every menu placed its contents at
fractions of the window height, which was close enough at 1080p and wrong below
it: the settings subtitle was drawn over the first row label, the lobby's seat
list ran into the character picker with a full table, and the Start button and
its note fell off the bottom of a 1280×760 window.

All four menus now lay themselves out in one downward pass from **measured font
heights**, with the bottom block anchored to the bottom margin and the middle
grown to fit. Where it still would not fit, the gaps shrink (host screen) or the
rows do (lobby seats, character rows) rather than anything leaving the screen.
The shared heading moved into `ui/headings.py`, because three screens measuring
the same title block separately is how they disagreed in the first place.

**2. Text input.** Two bugs with one cause and one consequence:
- *Every keystroke inserted two characters.* SDL sends both a `KEYDOWN` carrying
  `unicode` and a `TEXTINPUT` for the same key, and the field accepted either.
  It now takes characters from `TEXTINPUT` only — which is also the event that
  understands dead keys and the Polish letters this game is full of.
- *Backspace seemed to need two presses.* It did not: typing had inserted two
  characters, so one delete looked like half a delete. Fixed by the above.
- *Holding backspace did nothing.* Held keys do not repeat by themselves, and
  `pygame.key.set_repeat` would have spammed every in-game shortcut too. The
  field now runs its own timer: one delete immediately, a pause, then a steady
  stream — the behaviour of a normal desktop text box.

**3. Debug Version.** A new option in both the host screen and the local setup
screen, clearly labelled as a testing aid ("tylko do testów sieci; normalnie
potrzeba 3 graczy"). When on, a two-player table may start; the host broadcasts
the setting so clients show the same requirement. Nothing else changes — a
two-player game deals a Piotrek and one hunter and plays normally.

**4. Polish.** Clicking a field focuses it and blurs the others; Tab cycles and
wraps; Enter confirms; Delete clears a field; Ctrl+V is left alone so a paste
arrives as `TEXTINPUT`. Buttons and rows are aligned on the computed grid, and
the lobby's rows shrink their font with the row rather than overflowing.

### Architecture changes
None. `ui/headings.py` was extracted from `ui/network_screens.py`;
`SessionConfig.debug_version`, `RULES.debug_min_players` and
`LobbyState.debug_version` were added.

### Tests
307 → 350, in a new `tests/test_menu_layout.py`. The overlap test wraps
`Renderer.text`, records every string each menu draws and asserts that no two
share pixels and none escapes the window — at 1280×760, 1600×900, 1920×1080 and
2560×1440, including a full six-seat lobby with long nicknames and the longest
error messages the screens can show. It found three real overlaps that this
stage then fixed. The text-entry tests feed the exact event pair SDL sends, and
cover Polish letters, numeric-only fields, length limits, focus, Tab order and
held-backspace repeat.

### Notes
- A two-player game is *playable*, not balanced: one hunter against Piotrek is
  not the game the design document describes. The option exists for testing the
  networking, and is off by default.
- Still unfixed from stage 6: hidden information is not filtered, there is no
  reconnection, and the turn passes manually.

---

## Stage 8 — Synchronisation fixes and the turn loop
**Date:** 2026-08-03

### Goal
Fix what the first multiplayer pass got wrong and close the basic gameplay loop,
so two people can play a match from start to finish without touching anything by
hand. No new mechanics.

### Fixed

**1. Text fields behave like native ones.** Caret placement by clicking, drag to
select, Shift+arrows, Home/End, Ctrl+A/C/X/V, typing or Backspace replacing a
selection, Delete forward. `ui/clipboard.py` wraps `pygame.scrap` and falls back
to an internal buffer where the system clipboard is unavailable, so copy/paste
works inside the game regardless — and never raises. Placeholder text is drawn
in a dimmer grey than anything typeable and is never part of the value.

One subtle bug found on the way: a click left a *zero-length selection* behind,
so the first character typed counted as selected and the second replaced it —
which is why typing "Ola" produced "la".

**2. Player ownership.** The reported bug (host "Byd" and client "Lap" both
playing Byd) had a single cause: every screen drew `state.active_player`'s hand.
Seats are now three distinct things — **owned** (`local_seat`), **active**
(whose turn) and **viewed** (`GameScreen.view_seat`) — and the hand fan,
character panel and ability button all follow the viewed seat while the board
follows the active one. A client cannot view another seat at all; `HudContext`
carries `view_index` and `can_act`, and looking is never playing.

**3. Debug Mode viewing.** With the development option (or hot-seat) on, other
seats may be inspected for testing. There is always a way back: the **Home**
key, a "Wróć do: <nick>" button over the board that appears only while you are
looking elsewhere, and every tile marked "twoje miejsce" / "podgląd". A seat that
stops being viewable snaps back to your own rather than leaking a hand.

**4–5. The turn loop runs itself.** Playing — or discarding — a movement card
now refills the hand to the size the rules give that player (`starting_hand_size`,
so Piotrek's larger hand and ChatGPT's smaller one are respected) and hands the
turn on; when a round's slots run out the round advances. All inside the one
command, so it is atomic and replays identically on every machine. **Discarding
is how you pass** — a hand where nothing is legal would otherwise leave the
table stuck for ever.

**6. Chest cards are actually dealt.** The interface had been promising them for
several stages. `_distribute_chest_card` hands this round's card to the hunter
whose turn it is to get one, through the ordinary draw path so the hand limit
and its keep-or-discard prompt behave exactly as they do for a manual draw, and
the prompt appears on the owner's machine only.

**7. Synchronisation review.** Everything above is a Command producing Events, so
it crosses the wire unchanged. Verified end to end by a test that plays a full
two-player match over real sockets and compares snapshots after *every single
action*.

### Bug fixes
- **The chest recipient was looked up by name.** `hunter_names` holds *character
  titles*, so matching them against player names silently found nobody — the
  chest card was promised for rounds and never handed out. Now done by seat.
- **The game always started on seat 0**, regardless of the cadence. It now starts
  on whichever seat the design document's order puts first (Piotrek).
- A click in a text field created an empty selection (see above).

### Architecture changes
None to networking. Added `GameState.seat_order`, `next_seat`,
`chest_recipient_seat`, `_after_play`, `_refill_movement_hand`, `_end_turn`,
`_begin_round`, `_distribute_chest_card`, `turn_slot`; `RULES.auto_turn_flow`;
`ChestCardAwarded`; `HudContext.view_index` / `can_act`; `GameScreen.view_seat`,
`may_view`, `controls_view`, `return_to_my_seat`; `ui/clipboard.py`.

### Tests
350 → 378. New: desktop text-box behaviour (select-all-then-type, copy/cut/paste,
caret placement by click, drag selection, Shift+arrows, placeholder brightness
compared against typed text); ownership (each machine shows its own hand, a
client cannot look at another, watching is not playing, the way back always
works, hot-seat still follows the turn); the turn loop (refill respects each
player's rules, cadence order, round advance, discard passes, a refused play does
*not* end the turn, the loop can be switched off); chest distribution and its
rotation between hunters; and a full unattended two-player match over sockets
with a snapshot comparison after every action.

### Notes
- With a single hunter (the two-player development option) the cadence gives that
  hunter a round of their own, so the same seat can legitimately play twice
  running. Tests assert a turn was *consumed*, not that the seat changed.
- Nothing ends the game yet — win conditions remain unimplemented, so the loop
  runs indefinitely.
- Still unfixed: hidden information is not filtered, and there is no reconnection.

---

## Stage 9 — HD pass, readability and the End Turn button
**Date:** 2026-08-03

### Goal
Make the game look like a desktop board game rather than a scaled prototype, at
every supported resolution. No new mechanics.

### Implemented

**1. End Turn button.** Bottom-right of the board, live on your turn and greyed
out otherwise. `cmd.EndTurn` runs exactly what the automatic ending runs —
refill, then hand the turn on — so the button and the automatic path cannot
drift apart. Refused while a chest choice or a pending decision is open. The
automatic flow is untouched.

**2–3. The HD pass: nothing is enlarged after being drawn.**
- `CardRenderer.quantised(size, scale)` gives the size a card will be *painted*
  at; `draw_transformed` applies the scale before painting and only rotates
  afterwards. A card that grows under the cursor now gets **sharper**.
- Recently Played does the same for its hover preview. It had been
  `rotozoom`-ing a 95×137 face by 2.15× — the one card the player most wanted to
  read was the blurriest thing on screen.
- Sizes are rounded to a 6-pixel step so a smoothly growing card repaints a
  handful of times instead of every frame.
- `FontBook.set_scale` multiplies every requested size by `Layout.ui_scale`
  (window height / 1080). A 1200- or 1440-tall display gets **bigger glyphs
  rendered at that size**, not the same bitmap stretched. The renderer's text,
  shadow and glow caches are cleared on resize so two scales never share a
  screen.

**4 & 9. Panels take what they need; the board gets the rest.** Column widths are
now measured rather than guessed: the cards are sized from the height available,
the column is made exactly wide enough to hold them (with a floor for the labels
and a ceiling of one hand-card), and everything left over goes to the board. Deck
counters moved onto the name row — two stacked label lines per deck were
spending a fifth of the column on words the cards needed more.

Result at 1920×1080: panel cards 113×162 (was 106×152) with the column 99% used,
right-hand cards 154×220 (was 92×131 — a 68% increase), and the board 1348 px
wide (was ~1180). The right column is also sized per case now: Piotrek has a
skill row, a hunter has the colour notepad instead, and sizing both for the
worst case had left a hunter's panel a third empty.

**5, 7, 8. Responsive and readable.** Everything quoted in pixels is multiplied
by `ui_scale`; verified at 1280×760, 1600×900, 1920×1080, 1920×1200, 2560×1440
and 3840×2160 with no overlaps, nothing off-screen and the board holding 60–76%
of the window width.

**6. Chest distribution fixed.** `_set_round` jumped straight to the new number,
so every round it skipped swallowed that round's chest card — which is exactly
the reported symptom of "some players receive them, others never do". It now
steps through each round it crosses and deals what each one owes. Verified over
the network: every hunter receives their card, on every machine.

### Tests
378 → 407, including a new `tests/test_rendering_quality.py` that measures
crispness numerically — the contrast between neighbouring pixels of a card
redrawn at size against the same card zoomed — plus type scaling with the
display, the board's share of the window, and the columns not being mostly
empty. Also: the End Turn button (works, dead off-turn, waits for a pending
decision, never overlaps the board controls) and the chest fix in the engine and
over the wire.

### Notes
- The board's world surface is still scaled by the camera. That is a map being
  zoomed, which is the one place where scaling is the right answer.
- Still unfixed from earlier stages: hidden information is not filtered, there is
  no reconnection, and no win conditions.

---

## Stage 10 — Visual identity
**Date:** 2026-08-03

### Goal
Rebuild the presentation to match the supplied concept art. No gameplay, no
layout moves, no networking — appearance only.

### What the concept asked for
A near-black table with a faint blue wash, panels of dark slate edged in worn
brass with corner brackets, parchment cards with a double frame, upper-case
letter-spaced brass headings, and one green accent for "this is now / this is
yours". The palette was sampled directly from the image rather than guessed.

### Implemented

**A central style system.** `config/theme.py` now holds every colour in the
game, including names for the things the interface used to invent inline
(`prompt`, `valid`, `invalid`, `snap_ring`, `link_line`, `frost`, `brass`,
`counter_minus/plus`…). `render/renderer.py` gained the shared chrome every
panel is built from: `table_background`, `premium_panel` (shadow → gradient body
→ top-edge bevel → brass border → corner brackets), `inset_well` for recesses,
`section_heading`, `spaced_text` (pygame has no letter-spacing, so glyphs are
placed one at a time and cached per word), `circle_button` and `diamond`.

**Everything redrawn in that language.** Background, both sidebars, the top bar,
player tiles, card containers, the board frame, the hand shelf, the status bar,
the played-card shelf, the choice/reveal/chest overlays, the pause menu, the
debug panel, the main menu, host and join screens and the lobby.

**Cards.** Parchment with a vertical shade (lit from above), a double frame —
outer border plus an inset brass rule — a divider with a small diamond, and
warmer typography. Deck backs became bound covers: deep colour, brass frame,
corner diamonds and a central emblem. Empty slots are dashed recesses rather
than card-shaped rectangles.

**Buttons.** One `Button` widget with three real states — idle sits with a
shadow, hover lightens and lifts, pressed drops onto the surface and loses the
shadow — plus a `primary` variant for the one call to action per screen.
Steppers, checkboxes, dropdowns and sliders follow the same palette.

**Player panels and top bar.** Colour bar down the left edge, a crown over the
seat in turn, an accent glow on the active tile, "(Ty)" on your own seat and a
diamond marking it. The round counter became the concept's arrangement —
"RUNDA" above, brass numeral, red and green round steppers either side — and the
chest plaque now puts its label above its value so the hierarchy reads.

**Micro-animations.** Button hover eases rather than snapping, hovered cards get
a warm halo and a deeper shadow, deck piles lift under the cursor, and the
active player's tile glows.

### Tests
407 → 419, with a new `tests/test_visual_style.py` that checks the look
mechanically: the table is dark and cool, the background is a wash rather than a
flat fill, panels sit between table and cards in luminance and carry a warm lit
edge, cards are parchment and shaded from above, deck backs use the themed
colours, buttons differ across idle/hover/pressed and disabled reads as
disabled, the menus share the table, and — the one that keeps the refactor
honest — the number of literal RGB values outside the theme stays near zero.

### Notes
- The board art itself is untouched, as instructed; only its frame joins the new
  interface.
- Region-by-region tone comparison against the concept lands within ~10-15
  luminance for the table, sidebars, top bar and player strip.
- I still cannot see rendered output (the image viewer returns nothing), so the
  match was verified by sampling the concept's pixels and comparing them with
  the game's. A human should confirm the result looks right.

---

## Stage 11 — Dedicated server: multiplayer that works across the internet
**Date:** 2026-08-04

### Starting point
Stage 6–8 multiplayer was host-authoritative deterministic lockstep over raw
TCP: one player's machine opened a listening socket and everybody else
connected to it. It worked, and every test passed — on a LAN.

It could not work across the internet, and no amount of polish would have made
it. A machine behind a home router does not accept incoming connections; that
is what NAT is. So "give your friend your IP" meant port forwarding, Hamachi or
a VPN for every single game. The brief for this stage ruled all three out.

### The decision, and the part that cannot be engineered away
Two friends in different countries can only reach each other if **both make an
outbound connection to something already publicly reachable**. There is no
client-side trick that avoids it. So the host stopped being the server, and a
real one was written.

That means the server has to run somewhere. `docs/SERWER.md` covers free
hosting plans, a VPS and TLS; the game ships pointing at `localhost` and the
owner changes one line after deploying. An in-game "run a server on this
computer" option covers same-network play with nothing deployed, and the
interface says plainly that it will not cross the internet — so it cannot be
mistaken for the answer.

### Reference material
`danqzq/gdg-ws1` was read as instructed. Adopted: the dedicated server process,
WebSockets, JSON messages with a type field, outbound-only clients. Not adopted:
it broadcasts the entire state on every message (this project has sent actions
since stage 1 and kept doing so), it has no lobby, reconnection or heartbeat,
and its own hosting section tells you to port-forward — so it does not actually
solve the problem this stage exists for. No code was copied.

### Implemented features
- **Dedicated server**: `python -m pedzacy_piotrek.server`, `--rooms`, `--port`,
  `--verbose`; also `python run_game.py --serve`. Reads `PORT` from the
  environment, which is how every hosting platform configures a process.
- **WebSocket transport** with automatic reconnection (exponential backoff),
  application-level heartbeat, latency measurement, connection state machine and
  TLS via `wss://`.
- **Room codes**: six characters from an alphabet with no `0/O` or `1/I/L`.
  Joining needs the code and nothing else — no address, no port.
- **Authoritative server**: it owns the only real `GameState`, validates every
  command against its own seat map, and stamps each accepted command with a
  fingerprint of the resulting state.
- **Self-healing desync**: a client whose fingerprint differs, or which sees a
  gap in the sequence, requests `STATE_SYNC` and rebuilds from seed + log.
- **Reconnection**: seats are held for a grace period (180 s), a returning
  player is recognised by a resume token, put back in their seat and replayed up
  to date. The board stays on screen behind a "łączę ponownie" banner.
- **Graceful degradation**: an expired grace period leaves an empty seat and the
  match continues; a player leaving no longer ends anybody else's game.
- **Configuration file** `data/network.json` — every networking value in the
  project, with environment overrides. No hard-coded addresses or timeouts.
- `Dockerfile` and `Procfile`; the server imports and runs **without pygame**,
  and a test enforces that.

### Architecture changes
- `net/tcp.py` deleted. `net/` is now `config, protocol, transport, websocket,
  client, session, service, lobby`; new `server/` package is
  `hub, room, registry, app, embedded`.
- **`server/hub.py`, `room.py` and `registry.py` are synchronous and contain no
  I/O.** The server is a function from (connection, message) to a list of
  (connection, message). All of multiplayer — seating, authority, turn order,
  disconnection, grace periods, state sync — is therefore testable in
  milliseconds without a socket, and `server/app.py` is ~200 lines of asyncio
  with no game rules in it.
- **Connection identity split from player identity**, joined by a resume token.
  Without that split "reconnect" can only mean "join again as a stranger",
  which is exactly why stage 6's dropped players lost their hands.
- `HostService` / `ClientService` kept their shape, so `ui/` changed only where
  the player-visible flow changed (address → room code, plus ready flags).
- Host and client now take the **same** path into a match: the server
  broadcasts `GAME_START` and both screens notice the session appearing. The
  second code path that only one machine took is gone, and several old
  ownership bugs lived in it.
- `LineBuffer` removed — WebSockets frame messages, so there is nothing to
  reassemble.
- `settings.NETWORK` replaced by `net.config.current()`: a module constant is
  captured by every `from … import` at import time, so `--server` would have
  updated the name and nothing else.

### Bug fixes
- **`PlayerRenamed` never worked.** The event declared a field `name`, which
  collides with the read-only `GameEvent.name` property; the generated
  `__init__` assigned over it, so *every* rename since the event was written
  raised `property 'name' has no setter` and came back as a refusal. Renaming
  was the one player action with no test. Field renamed to `new_name`; rule N43
  added.
- **Choice prompts were broadcast to everyone.** A card needing a decision
  changed nothing but was still logged and replayed, so a modal appeared on
  every machine asking a question only one player could answer. Such commands
  are now sent to the asker alone as `CHOICE_REQUIRED` and never logged.
- **Closing a room told nobody**; the registry dropped it underneath its
  players, leaving every client waiting on a server that had forgotten them.
  Added a routed `ServerHub.close_room`.
- Lobby seat rows overlapped at 1280×760 once a third item (ready status) was
  added; positions are now measured from the right edge and long nicknames are
  elided.
- A create/join request sent before the handshake finished was refused; intents
  are now held until `WELCOME` and replayed after a reconnection.

### Tests
436 passing (was 422), ~115 s. `test_multiplayer.py` and `test_session.py`
rewritten for the new architecture, `test_network_ui.py` and
`test_menu_layout.py` updated, `tests/netkit.py` added as the shared harness.
Coverage went up, not down: rooms, registry limits and pruning, room codes,
protocol errors, configuration parsing and environment overrides, authority
(wrong seat, wrong turn), choice routing, fingerprints, disconnection, grace
expiry, reconnection, resync-on-drift, room closure, and a full twelve-turn
match compared after every action. One test drives the whole stack over **real
WebSockets on a real port**.

### Known limitations after this stage
- **L1 (hidden information) is still open and is now the last thing between
  this and a public release.** A client rebuilds the full state from seed and
  log, so a modified client could read another player's hand. The server checks
  permissions but does not filter what it broadcasts. Fixing it is not free:
  per-seat views mean per-seat fingerprints, and the single broadcast that makes
  the current design cheap has to be split up.
- The command log is kept in memory for the life of a room and is never pruned,
  because pruning would break the replay reconnection depends on.
- No "return to lobby" after a match starts.
- The server does not authenticate anyone: whoever has the room code is in.
  Fine for a group of friends; not for a public server.

---

## Stage 12 — One highlight system: UI consistency pass
**Date:** 2026-08-05

### Goal
No new gameplay. Stage 10 gave the game a visual identity and most of the
interface adopted it; this stage is about the parts that did not, and about
making it impossible for the next panel to miss it too.

### The thing that was wrong
There was one highlight primitive, `Renderer.glow(centre, radius, colour)`,
which drew a **radial disc**, and every caller picked its own radius by eye.
The multipliers ranged from 1.15× to 2.6× the component. The worst of them was
inside `premium_panel` itself:

    self.glow(rect.center, int(max(rect.width, rect.height) * 0.75), ...)

On a 300×70 player tile that is a 225-pixel glowing circle centred on a
70-pixel-tall widget — a disc lying across everything around it. Because it
lived in `premium_panel`, *every* panel that asked for emphasis inherited it:
the active player, the lobby seat, the End Turn button, the choice, chest and
pause dialogs. That is what looked unfinished, and it was one line.

Nine call sites in all: player tiles, lobby seats, the turn-order ribbon, End
Turn, the choice prompt's pawn options, the chest and pause dialogs, hand-card
hover, board tile and pawn highlights, and the zoom-slider knob. Plus a circle
drawn around a *rectangular* chest card, which reached well past both corners.

### The rule that replaced it
**A highlight follows the shape of the thing it highlights.** `glow()` is gone.
In its place, two methods that take the component's own geometry rather than a
multiplier, so the old mistake is not expressible:

- `shape_glow(rect, colour, radius=, strength=)` — a rounded-rect bloom, capped
  at `BLOOM_MAX` (26 px) past the edge however big the panel is;
- `ring_glow(centre, radius, colour, strength=)` — the circular counterpart for
  things that really are round (pawns, portraits, board fields, the slider
  knob), reaching `BLOOM` (0.42) of the component's own radius further out.

### Implemented

**`render/highlight.py` (new) — the one highlight system.** Every number that
says what an interaction *looks* like: hover lighten amounts for fill, border
and text; the lift; the pressed drop; glow strengths for hover, selection and
focus; shadow depths. Plus an `Emphasis` dataclass and `emphasis()`, which
turns a component's base colours and its state (hover level, selected, focused,
pressed, enabled) into one description.

**`Renderer.interactive_panel(rect, style)`** is now the single way to draw
anything the mouse can touch. It applies the lift or the press, draws the
shape-following glow, and returns the rect it actually drew at so the caller
centres its label on the right pixels. `premium_panel` stays for furniture that
just sits there.

**Everything routed through it.** Buttons, steppers, dropdowns (closed control
*and* the open list's rows), checkboxes, text fields, player tiles, the rename
pencil, mod slots, lobby seat rows, the ability button, End Turn, return-to-seat,
the choice dialog's options, the chest confirm and the pause menu entries. There
is now one hover in the game: the surface lightens, the border warms, the text
brightens, the thing lifts a pixel and its shadow deepens.

**Button auto-sizing.** `Button.natural_size()` measures the caption;
`Button.fit()` grows the box to hold it; `fit_buttons()` sizes a group to the
widest of them so a menu column stays a column. Every screen that used to quote
a width — main menu, host, join, lobby, setup — now measures. `Stepper` measures
its own ± labels instead of assuming a 40 px square, and no longer gets *taller*
when it gets wider.

**Text fitting.** `spaced_width`, `fitted_font`, `fit_spaced_text` and
`fit_text` (all cached) shrink type to its box as a last resort, so a layout
that insists on a small control still cannot clip a label.

### The two captions the brief named, measured
- **"UŻYJ UMIEJĘTNOŚCI (1/1)"** — box 145 px, text 152 px at 1080p (**7 px
  over**); box 201 px, text 242 px at 1440p (**41 px over**). The button now
  takes the full inner width of the right column (236 px / 324 px) and the type
  fits to it.
- **"Gra lokalna (hot-seat)"** — needed 350 px of text in a hard-coded 320 px
  box at 1440p. Now measured: 428 px.

### Also fixed in the consistency pass
- The circular ring around a selected chest card became the card's own outline.
- The pause menu sized itself 320×52 regardless of its entries; now measured.
- A dead `if False` branch in the choice prompt's "Esc anuluje" hint.
- Focused text fields use the shared focus glow rather than a border of their own.
- Three imports left unused by the refactor.

### Tests
437 → 450. New in `test_visual_style.py`:
- `test_nothing_draws_a_radial_highlight_any_more` — greps `ui/` and `render/`
  for `.glow(`, the way the literal-colour test guards the palette. This is the
  one that keeps the stage from being undone.
- `test_a_highlight_never_reaches_far_past_its_component` — renders a bloom on a
  300×60 panel and *measures* how far the light travels along a scanline.
- `test_the_ring_glow_hugs_the_thing_it_rings` — the same for a token.
- `test_a_button_is_wide_enough_for_its_own_label` (4 captions),
  `test_every_menu_button_holds_its_caption` (3 screens × 3 resolutions),
  `test_a_label_shrinks_rather_than_escaping_a_box_it_was_given`.
- `test_in_game_button_labels_stay_inside_their_buttons` — wraps `spaced_text`,
  records what is actually drawn, and checks it against the button rect at three
  resolutions. This is the one that fails on the old ability button.
- `test_hover_is_the_same_everywhere` — asserts the five states differ from each
  other in the ways the language says they should.

### Verification
- 450 passing (~115 s).
- `tools/inspect_frame.py` at 1280×760, 1920×1080, 2560×1440, 3840×2160:
  **0 problems**.
- A QA sweep drove Main Menu, Host, Join, Setup (including an open dropdown),
  Gameplay, both kinds of choice prompt, the chest overlay, the reveal, the
  pause menu and the debug panel at five resolutions, with the cursor placed on
  every control in turn: no exceptions, nothing off-screen.

### Notes
- I still cannot see rendered output, so "subtle and professional" was verified
  by measuring how far light travels past an edge, not by looking. A human
  should confirm the result reads the way the concept does.
- The lobby's ready button is measured against the longer of the two captions it
  swaps between ("Czekam na hosta"), because sizing it to whichever one happened
  to be showing at layout time is the same class of bug in slow motion.

---

## Stage 13 — Online play that actually connects
**Date:** 2026-08-05

### Starting point
The brief asked for the networking to be redesigned from scratch into a
"production-ready architecture": dedicated server, WebSockets, room codes, one
configurable URL, Railway deployment, no host-side listening.

**Stage 11 had already built exactly that architecture**, and it was sound. What
it did not do was work. Hosting a game failed on every single attempt, with the
player being thrown back to the main menu reading `Najpierw przywitanie
(hello)` — an internal protocol message. So the stage became: find out why a
correct design fails in practice, fix it, and finish the deployment story.

A rewrite was considered and rejected. It would have landed on the same
architecture, discarded 450 passing tests and violated N1. The problem was one
defect, not the design. This is recorded here because the brief did ask for a
rewrite and a future reader deserves to know it was a deliberate refusal.

### The bug, because it is worth understanding
`HostSetupScreen.confirm()` opens the connection and sends the table settings in
the same function:

```python
service = HostService(...)          # queues CREATE_LOBBY as an intent
service.set_settings(..., debug_version=self.debug_version)   # sends NOW
```

`HELLO` is only sent from `GameClient.poll()`, on the next frame. But
`WebSocketTransport.send()` puts messages in a queue that is flushed the instant
the socket opens — so `SET_SETTINGS` arrived **before** `HELLO`. The server
correctly answered "no handshake yet", and because that error was `fatal=True`,
the host was disconnected from a room they never managed to create.

Not a race: a certainty. Reproduced against a real server on a real port:

```
before:  disconnected="Najpierw przywitanie (hello)"  room_code=(none)  debug_version=False
after:   disconnected=None                            room_code=7ZVEDX  debug_version=True
```

One defect caused three separate reported symptoms:
1. the "Hello required" message on screen,
2. hosting not working at all,
3. **Debug Mode never giving 2-player games** — `debug_version=True` was in the
   discarded message and never reached the server.

The existing tests could not see it because `netkit.Table.host()` pumps the
connection before touching any setting. The real screens have no opportunity to.

### Implemented features
- **The handshake gate** (`net/client.py`). `_send()` holds every message except
  `HELLO`/`PING`/`PONG` until the server has greeted **this connection
  generation**. `_on_welcome` flushes the create/join intent first, then the
  held queue in order. On reconnection the gate closes again, so a click during
  a drop is queued and replayed instead of refused.
- **The server no longer kills a session over message order** (`server/hub.py`).
  A pre-handshake message costs the message, not the player.
- **One friendly-error layer** (`net/messages.py`, new). Named Polish sentences
  plus a pattern table, applied at every point where a reason reaches the
  player: `_on_error`, `_on_bye`, `_on_match_ended`, `_drop`, the transport's
  `_readable`, and the three screens that showed `TransportError` text directly.
  Deliberately narrow — see Notes.
- **Bare hostnames resolve the way the deployment needs** (`net/config.py`).
  `piotrek.up.railway.app` → `wss://…` (443); `192.168.0.14` and `localhost` →
  `ws://…:51337`. Getting this backwards was silent and total: the address looks
  right and every connection is refused.
- **An HTTP health page** (`server/app.py`). `GET /`, `/health`, `/healthz`,
  `/status` return plain Polish text saying the server is alive, how many rooms
  are open and what to type in the game. Handles both the pre-14 and post-14
  `websockets` `process_request` signatures. Railway health-checks `/health`.
- **One text editor for every text field** (`ui/widgets.py`). New `TextEditor`
  holds all editing behaviour; `TextInput` and `TextField` both use it.
  The in-game rename box therefore gained selection, caret movement, word jumps
  (Ctrl+←/→), placeholder text and Ctrl+A/C/X/V — it previously had typing,
  backspace and Enter, so a nickname on the clipboard could not be pasted in.
  Shift+Insert / Ctrl+Insert / Shift+Delete work too.
- **The rename caret is drawn where it is** (`ui/hud.py`, `TextField.display_text`).
  It used to be appended to the end, which was only correct while it could not
  move.
- **Renaming starts with everything selected**, so "click the pencil and type"
  replaces the old name.
- **The lobby reserves the right number of rows** (`ui/network_screens.py`).
  Was `RULES.min_players` (3); now `lobby.minimum_players`, which is 2 with the
  debug version on.

### Deployment
- `SERVER_SETUP.md` (new) — the deliverable. Eight sections, written for
  somebody who has never deployed anything: why a server is needed at all,
  running locally, Railway step by step, which files belong to which layer, the
  one configuration value, creating a room, joining a room, and a symptom table
  for when it does not work.
- `requirements-server.txt` (new) — **only** `websockets`. The root
  `requirements.txt` pins pygame, and a platform that installs it pulls in a
  graphics library and its SDL system packages to run a process that never opens
  a window.
- `railway.json` (new) — start command, health-check path, restart policy, and
  an install phase pointing at `requirements-server.txt`.
- `.railwayignore` (new) — omits `ui/`, `render/`, `assets/`, tests, tools, docs.

### Tests
`tests/test_handshake.py` (new, 40 tests):
- the exact `HostSetupScreen.confirm` sequence — construct, set settings,
  **then** poll — and it must produce a room code and keep the settings;
- `HELLO` is first on the wire whatever order the screen acts in;
- no message, notice or disconnect reason ever contains "hello" or
  "przywitanie";
- a premature message is non-fatal and leaves the room intact;
- an action taken while reconnecting is replayed after the new handshake;
- a two-player debug game starts end to end and both machines agree on the
  snapshot; without the debug version, two players are still refused;
- twelve kinds of technical text never reach the player, and five kinds of the
  server's own Polish prose survive untouched;
- the rename box does select-all, copy, paste, cut, caret movement, placeholder,
  Enter and Escape;
- Ctrl+← word jumps; `max_length` limits typing but not assignment;
- **the server imports and constructs with pygame blocked and `ui/`/`render/`
  made invisible** — the `.railwayignore` claim, asserted rather than documented.

`tests/netkit.py` / `server/embedded.py`: `InProcessServer` now records every
message it is handed, in arrival order (`received_from`). Order was the whole
bug and nothing could previously see it.

`tests/test_session.py`: the `normalise_url` cases were updated for the
deliberate `wss://` change, with both branches now covered explicitly.

### Verification
- **496 tests passing** (450 before, +40 new, +6 added URL cases).
- Handshake reproduction against a real server on a real port: before/after
  above.
- Health page fetched over real HTTP on `/`, `/health` and `/healthz?probe=1`.
- Server constructed with `pygame`, `pedzacy_piotrek.ui` and
  `pedzacy_piotrek.render` all made unimportable.

### Notes
- **The error layer is narrower than it first was, and that was a test's doing.**
  The first version matched the server's own Polish sentences too and replaced
  them with blander constants — `"Nie ma pokoju o kodzie ZZZZZZ"` became
  `"Nie ma pokoju o takim kodzie"`, losing the code the player typed, and
  `"Tylko host może rozpocząć grę"` lost which action was refused. Three
  existing tests failed and were right to. The table now catches only text that
  was never written for a player; anything already in our own Polish prose
  passes through. If you add a pattern, ask first whether the sentence you are
  replacing is *more* specific than the one you are substituting.
- `max_length` constrains typing, not programmatic assignment. This looks like
  an oversight and is not: a field pre-filled with a long server address has to
  show all of it, and a test encodes the behaviour.
- The `client/ server/ shared/` directory split in the brief was **not** done.
  The server is authoritative and builds real `GameState`, so it needs `engine/`,
  `cards/`, `board/`, `players/`, `config/` and `data/`; a literal split means
  rewriting imports across ~100 files for no behavioural gain. The separation is
  instead made real where it counts — a lean requirements file, a build that
  omits the client packages, and a test proving the server never reaches for a
  screen. `SERVER_SETUP.md` §4 documents the three-way mapping.
- Still unverified by eye: I cannot see rendered output. The rename caret's new
  position and the lobby's row count were checked by measurement and by test,
  not by looking.

---

## Stage 14 — Usability of the online screens
**Date:** 2026-08-05

### Starting point
Stage 13 made online play work. This stage is about whether a person can
comfortably get at it: paste a nickname, send a room code to a friend, and not
retype a server address every session. No networking behaviour was changed.

Stage 13 had already given both text widgets a shared `TextEditor`, so item 1 of
the brief was mostly satisfied before this stage began. What it had NOT covered
was held-key repeat in the rename box, placeholder colour in the rename box, and
the fact that the host screen pre-filled the nickname instead of hinting it.

### Implemented features

**1. Text editing, finished**
- **Held-key repeat moved into `TextEditor`** (`ui/widgets.py`). It lived in
  `TextInput`, so the in-game rename box did not have it: holding Backspace
  deleted exactly one character, and clearing an eleven-letter name meant eleven
  presses. It is editing behaviour, so it belongs with the other editing
  behaviour. `TextField.update(dt)` is now called from `GameScreen.update`.
  There is one repeat rate in the application instead of one per widget.
- Repeat now respects Ctrl for word-wise `Ctrl+←/→`, which it previously
  dropped on the second and later repeats.
- `TextField.showing_placeholder` (new) — the HUD asks, because `display_text()`
  returns one string either way and a hint must be drawn dimmer than content.
  The rename placeholder now uses the same `darken(text_dim, 0.72)` as the menu
  fields, so every placeholder in the application is the same grey.
- Every editable surface (5 × `TextInput`, 1 × `TextField`) supports
  Ctrl+A/C/X/V, Shift-selection, Home/End, word jumps and held-key repeat.

**2. Copy the room code**
- `CopyNotice` (new, `ui/network_screens.py`) — "show this for two seconds"
  as one class rather than three timers that drift apart. Fades over the last
  third rather than vanishing between frames.
- **"Kopiuj kod pokoju"** under the room code in the lobby, with
  "✓ Skopiowano kod pokoju" beside it for 2 s. Copies **only** the code — not
  the sentence around it, because the person receiving it is going to paste it
  straight into the join field and `KOD POKOJU: K7M2QD` joins nothing.
- The confirmation sits *beside* the button, not under it, so the seat list
  below never moves when it appears and disappears.
- Disabled until a room actually exists.

**3. Copy the server address**
- A small **"Kopiuj"** button beside the server field on both the host and the
  join screen, with its own 2 s confirmation underneath (to the right is the
  window edge at 1280 wide).
- Placed by `_FormScreen._place_copy_button`, measured from the field so it
  stays aligned when the elastic row spacing moves the form on a short window.

**4. Remembering the last server**
- `net/config.py`: `user_config_dir()`, `load_preferences()`,
  `save_preferences()`, `remember_server_url()`, `remembered_server_url()`.
- Written to the user's own config directory (`%APPDATA%`, `~/Library/
  Application Support`, `$XDG_CONFIG_HOME`), **not** `data/network.json`. The
  shipped file is part of the installation and may be read-only or inside a
  temporary PyInstaller extraction; a value the game decides for itself does
  not belong there.
- Config loading is now four layers: defaults → `data/network.json` →
  remembered → environment. Remembered sits above the shipped file so a typed
  address is never typed twice, and below the environment so `--server` and a
  hosting platform still win.
- Remembered **when a room is actually created or joined**, not when the
  address is typed — otherwise the field is helpfully re-filled with the typo
  that caused the failure.
- Never resets to localhost on its own; clearing the field is the way back, and
  an empty field falls through to the configured default.

**5. Polish**
- The host screen's nickname field was **pre-filled** with `Gracz`, so a player
  had to select-all and delete before typing their own. It is a placeholder
  now, matching the join screen — the two forms behave identically.
- Server fields gained the placeholder `wss://twoj-serwer.up.railway.app`: a
  concrete example, because the shape of the answer is the hard part and the
  `wss://` prefix is what people leave out.
- Every field on both forms now has either a value or a hint.

### Bug found while testing
`remember_server_url` was being handed `transport.description`, which for the
in-process transport used by the tests is `in-process:c3`. Saved unchecked it
normalised to `ws://in-process:c3:51337`, whose port is not a number — and every
subsequent `urlparse(...).port` raised `ValueError`, including the one drawing
the "Serwer gry:" line on the main menu. Nine tests went red and the real
preferences file had to be deleted by hand.

Two fixes, because one was not enough:
- `is_usable_url()` guards what may be remembered, checked both on the way out
  and on the way in (the file is editable by hand).
- `describe_target()` no longer raises; a malformed address produces a shrug
  rather than taking the game's first screen down with it.

### Also fixed
`ui/clipboard.py` cached "no display yet" as a definite answer. Any probe before
the window existed pinned availability to False for the whole session, silently
demoting every copy and paste in the game to the internal buffer with nothing in
a log to show for it. A missing display now leaves the question open; only a
real success or failure is recorded.

### Tests
`tests/test_usability.py` (new, 66 tests):
- the four editing behaviours run against **every** editable surface via a
  parametrised `_all_fields()` helper, so a new kind of field that is not listed
  there stops being covered visibly rather than silently;
- held Backspace keeps deleting, a tap deletes exactly one, release stops it,
  and the keyboard beats a swallowed KEYUP — with a `_Pressed` stand-in, because
  headless `pygame.key.get_pressed()` reports nothing;
- the rename box repeats too (the field that did not, before this stage);
- a placeholder is measurably dimmer than typed text — brightest-pixel sampling,
  not an assertion by eye;
- the copy notice lasts ~2 s and fades;
- the lobby copies only the code, and does nothing without one;
- both copy buttons copy the server address, and nothing when it is blank;
- at **four resolutions**: the copy button does not cover its field, is on the
  correct side, stays inside the window, is centred on the field to within a
  pixel, and no two controls on a form overlap;
- the lobby copy button never lands on the seat list;
- remembering: survives a restart, loses to the environment, ignores an empty
  value, ignores a corrupt file, is not fatal when the file cannot be written,
  and rejects an address nothing could reconnect to;
- the clipboard probe is not cached before the window exists, and copy/paste
  still works on a platform with no system clipboard at all.

### Verification
- **562 tests passing** (496 before, +66).
- `tools/inspect_frame.py` at 1280×760, 1920×1080, 2560×1440, 3840×2160:
  **0 problems**.
- A render smoke test drove all three screens at 1280×760 and 3840×2160,
  confirming each copy button is painted, on screen, and that the confirmation
  appears after a copy.

### Notes
- **The rename box still has no mouse selection**, and that is the one part of
  item 1 not delivered. It is an inline editor drawn by the HUD over a player
  tile; it owns no rectangle and no font, so it cannot hit-test a click. Worse,
  `GameScreen` deliberately treats any mouse press during a rename as "confirm",
  which is long-standing behaviour a player relies on. Giving it drag-selection
  means handing it geometry and changing what a click means — a UI redesign,
  which this stage was told not to do. Keyboard selection (Shift+arrows,
  Shift+Home/End, Ctrl+A) all work there.
- The five `TextInput` fields have full mouse selection, including drag.
- `CopyNotice` is deliberately not a general toast system. If a third kind of
  transient message appears, that is the moment to generalise it — not before.

---

## Stage 15 — The clipboard is the system clipboard
**Date:** 2026-08-05

### Starting point
Stage 14 shipped copy buttons, Ctrl+C/X/V in every field, and 66 tests saying
so. All of it worked — inside the game and nowhere else. Ctrl+C copied into a
buffer belonging to the process, Ctrl+V read it back, and the room-code button
put the code somewhere no chat window could see. Text could not leave the game
and could not get in.

### Root cause
`ui/clipboard.py` called **`pygame.scrap.put_text()` and
`pygame.scrap.get_text()`**. Those exist in **pygame-ce**. They do not exist in
upstream pygame, which is what `requirements.txt` asks for (`pygame>=2.5,<3`,
resolving to 2.6.1):

```
>>> pygame.scrap.put_text('x')
AttributeError: module 'pygame.scrap' has no attribute 'put_text'
```

Both call sites sat inside `except Exception: pass`. So every copy raised,
every raise was swallowed, and every operation quietly landed in `_fallback` —
an in-process clipboard, exactly the behaviour the module's docstring said it
was there to *avoid*. There was no log line, no failing test and no visible
symptom short of trying to paste into Discord.

A second trap was waiting behind the first: upstream pygame's SDL2 backend
accepts exactly one type string, `"text/plain;charset=utf-8"`.
`pygame.SCRAP_TEXT` is `"text/plain"`, which it refuses with *"content could
not be placed in clipboard"*. The obvious one-line repair — swap `put_text` for
`put(pygame.SCRAP_TEXT, ...)` — fails just as silently.

### Fix
`ui/clipboard.py` only. `TextEditor`, `TextInput`, `TextField` and every screen
are untouched; `copy()`, `paste()` and `reset()` keep their signatures, so the
copy buttons were fixed by repairing the thing underneath them.

- **Both API shapes are tried.** `put_text`/`get_text` first for pygame-ce,
  then `put`/`get` over a list of type strings with the SDL2 spelling first,
  then `pygame.SCRAP_TEXT` (a platform constant, read at call time because on
  Windows it is not necessarily a MIME string). The type that works is learnt
  once and remembered, so steady-state copying is a single call.
- **System first, always.** `_fallback` is consulted only when the clipboard
  could not be *read* — not when it is empty. Previously an emptied system
  clipboard would have been overruled by a stale in-process value, which is a
  lie about what the user copied.
- **Decoding**: UTF-8, Windows UTF-16 (detected by interleaved NULs), trailing
  NULs stripped, latin-1 as a floor. Polish letters survive both directions.
- **CRLF collapsed before flattening.** Text copied in a Windows application
  arrived with a doubled space at every line break; these are single-line
  fields, so a break becomes one space.
- `_ensure_ready()` keeps N62 (a missing display is not a cached answer) and
  extends it to a missing *window*: some backends need one before
  `scrap.init()` succeeds, and a probe made between `display.init()` and
  `display.set_mode()` must not pin availability off for the session.
- `scrap.set_mode(SCRAP_CLIPBOARD)` on init — a no-op under SDL2, correct on
  the backends that still separate the clipboard from the X11 primary
  selection.
- `reset()` (a test entry point) now blanks the system clipboard as well.
  After the fix that is where the text actually is, and without this a value
  copied by one test stayed readable in the next, making results depend on
  order.

### Why every existing test passed
All 66 stage-14 clipboard tests went through `copy()` and `paste()`. Those two
agreed with each other perfectly — via `_fallback`. A test that only asks a
module whether it agrees with itself cannot see a missing operating system.

The 14 new tests reach **past** the module: they put text with
`pygame.scrap.put` and read it with `clipboard.paste()`, and copy with
`clipboard.copy()` and read it with `pygame.scrap.get`.

- copy reaches the system clipboard; paste reads it, with nothing ever copied
  in-game;
- the outside world beats the internal buffer;
- an emptied system clipboard is not papered over with stale text;
- Polish letters round-trip; a pasted paragraph becomes one line;
- both copy buttons and a menu field land in the system clipboard, and a field
  pastes what another application copied;
- a pygame-ce-shaped `scrap` uses `put_text`; an upstream-shaped one that
  rejects every type but `text/plain;charset=utf-8` still works; a `scrap` that
  raises on everything falls back and copy/paste still works inside the game.

### Added
`tools/clipboard_check.py` — interactive, for a real desktop. Reports the
pygame/SDL version, the video driver, which backend was found and whether the
clipboard is available; copies a marker and holds a window open for 30 s (on
X11 the copying program serves the text on request, so it has to stay alive);
then watches for something copied in another application.

### Verification
- **576 tests passing** (562 before, +14). ~90 s.
- The bug was reproduced against the shipped code first, then again after the
  fix, at the `pygame.scrap` level in both directions.
- **NOT verified: an actual transfer to another application.** This container's
  pygame wheel has no X11 video driver at all (`dummy`, `offscreen`, `wayland`
  only), so a two-process test over a real X server could not be run — Xvfb was
  set up and the attempt failed on that. Under the dummy driver SDL keeps
  clipboard text inside the process, which is exactly the condition that hid
  the original bug, so the green run must not be read as proof of the last
  mile. `tools/clipboard_check.py` on a real desktop is the proof; it has not
  been run by anyone yet.

### Notes
- The in-process buffer stays, and stays load-bearing: it is what keeps the
  game editable on a machine with no clipboard, and one test holds it in place.
- Not attempted, deliberately: ⌘C/⌘V on macOS (`KMOD_META`) is not wired, only
  Ctrl. That is a `TextEditor` key-handling change, and this stage was told to
  fix the clipboard and leave the editor alone. It is a real gap on macOS.
- On X11, clipboard contents vanish when the game exits unless a clipboard
  manager is running. That is how SDL applications behave and is not something
  this module can fix.

---

## Stage 16 — The other half: encoding
**Date:** 2026-08-05

### Starting point
Stage 15 made the clipboard the system clipboard, and pasting in from Discord,
a browser and Notepad worked. Copying *out* produced garbage: `sdh` arrived in
other applications as `摳`, and every string was corrupted the same way.

### Root cause
Not SDL, not `ctypes`, not the text editor. **pygame does not use SDL for the
clipboard on Windows.** `src_c/scrap.c` line 65:

```c
/* Determine what type of clipboard we are using */
#if !defined(__WIN32__)
#define SDL2_SCRAP
#include "scrap_sdl2.c"
...
#elif defined(__WIN32__)
#define WIN_SCRAP
#include "scrap_win.c"
```

So Windows compiles the native `scrap_win.c`, which maps types to Windows
clipboard formats (`_convert_internal_type`):

```c
if (strcmp(type, PYGAME_SCRAP_TEXT) == 0)            return CF_TEXT;
if (strcmp(type, "text/plain;charset=utf-8") == 0)   return CF_UNICODETEXT;
```

and then, in `pygame_scrap_put`, `memcpy`s the bytes it was given straight into
the global memory block and calls `SetClipboardData(format, hMem)`. **It
converts nothing.** The type name is a promise about the bytes, and the module
was breaking that promise: it encoded UTF-8 and handed the result to a format
Windows reads as UTF-16LE.

`sdh` is `0x73 0x64 0x68`. Read as UTF-16LE, `0x73,0x64` is one code unit,
U+6473 — `摳`. The reported character is not a symptom, it is the arithmetic.

Two further details from the same source explain why this was invisible:

- `pygame_scrap_get` begins `if (!pygame_scrap_lost()) return
  PyBytes_AsString(PyDict_GetItemString(_clipdata, type));` — while the game
  still owns the clipboard, pygame hands back **its own cached bytes**. So a
  corrupt copy pasted back into the game perfectly, and copy→paste between two
  in-game fields was fine. Only another application ever saw the truth;
- external text still pasted in correctly because reading falls through to
  `_convert_internal_type`, gets CF_UNICODETEXT, and returns real UTF-16LE
  bytes — which stage 15's decoder detected by their interleaved NULs. Hence
  "pasting works, copying is broken", exactly as reported.

### Fix
`ui/clipboard.py` only — no rewrite, no new backend, no `ctypes`. The type
table became a **format** table, because the encoding is a property of the
format and not of the module:

```python
_WINDOWS_FORMATS = (
    ("text/plain;charset=utf-8", "utf-16-le", b"\x00"),   # CF_UNICODETEXT
    ("text/plain",               "mbcs",      b""),       # CF_TEXT
)
_SDL_FORMATS = (
    ("text/plain;charset=utf-8", "utf-8", b""),
    ...
)
```

- `_encode()` encodes for the format being written, and returns `None` when the
  platform has no such codec (`mbcs` is Windows-only) or the text does not fit
  the code page — which is why ANSI/`CF_TEXT` is a fallback and never the first
  choice: it cannot carry `ąćęłńóśźż` on every machine. Windows synthesises
  `CF_TEXT` from `CF_UNICODETEXT` for applications that ask, so nothing is lost
  by writing only the Unicode format.
- **The second terminator byte.** `pygame_scrap_put` allocates `srclen + 1` and
  zeroes it — one NUL. A UTF-16 string needs two, so one is appended here.
  Without it the closing `WCHAR` straddles the end of the allocation and the
  string ends in whatever the heap had lying around.
- `_decode()` now takes the declared encoding and, on the wide path, **decodes
  the full buffer rather than one stripped of trailing NULs**. The high byte of
  a final ASCII character is a NUL: stripping it truncated `ABCD12` to `ABCD1`.
  The terminator is cut after decoding, which is what Windows does. The
  declared encoding also settles the case a heuristic cannot see — a lone CJK
  character in UTF-16 contains no NUL to spot it by.
- `_text_type` became `_text_format`; the learnt format is remembered whole.

Nothing else moved. `copy`, `paste`, `reset` keep their signatures, the SDL2
path still writes plain UTF-8, and the stage 15 behaviour — system first,
fallback only when the clipboard cannot be read — is untouched.

### Tests
`_WinScrap` (new, `tests/test_usability.py`) reproduces `scrap_win.c`: the
format mapping, the single trailing NUL, the ownership cache that returns the
program's own bytes, and — the point of the whole thing — a
`as_another_application_reads_it()` that decodes `CF_UNICODETEXT` as UTF-16LE.
Fed the stage 15 bytes it produces `摳h` from `sdh`, so it is known to
reproduce the bug before being trusted to prove the fix.

19 new tests (595 total, was 576):
- the reported string, by name: `sdh` must not become `摳`;
- eleven strings copied and read as another application would read them —
  ASCII, Polish lower and upper case, digits, room codes, both server
  addresses, a name with a space, and a single character (odd byte count, where
  the terminator arithmetic is most likely to go wrong);
- the Unicode format is used and the ANSI one is not;
- the terminator is a whole `WCHAR` and lands inside the buffer;
- Windows paste from another application still works, in Polish;
- copy → paste inside the game, where pygame answers from its own cache;
- a full round trip out through another application and back;
- a lone CJK character is not mistaken for UTF-16 by a NUL heuristic;
- the SDL platforms still get plain UTF-8, unterminated.

### Verification
- **595 tests passing**, ~120 s. `--selftest` clean, `inspect_frame.py` 0
  problems at 1920×1080.
- Before/after against `_WinScrap`, printed side by side:
  `'sdh' -> '摳h'`, `'abc' -> '扡c'`, `'ABCD12' -> '䉁䑃㈱'`, `'zażółć' ->
  '慺볅돃苅蟄'` with the old encoding; every one exact with the new one.
- **Still not verified on real hardware.** This container has no Windows and no
  X11 driver, so `_WinScrap` is a reading of `scrap_win.c`, not a Windows box.
  It is a close reading — the mapping, the allocation size and the ownership
  cache are all quoted above from the 2.6.1 source — but
  `tools/clipboard_check.py` on the real machine is still the proof, and it now
  prints a sample set (ASCII, room code, digits, Polish, a server address) to
  compare by eye after pasting into another application.

### Notes
- `mbcs`/`CF_TEXT` remains reachable only if the Unicode format fails outright.
  It is lossy by nature; if it ever starts being used, something else is wrong.
- macOS uses the SDL2 backend (`!defined(__WIN32__)`), so it takes the UTF-8
  path and was never affected by this bug. ⌘C/⌘V still is not wired — see the
  stage 15 note.

---

## Stage 17 — Victory, defeat, and a secret worth keeping
**Date:** 2026-08-05

### Starting point
The game could be played and could not be won. `L3b` said it outright: the loop
would run for ever. Piotrek's colour was already being dealt — by
`setup.assign_secret_pawn`, from the shared seed — which is worse than not
having one at all: every client builds its state from that same number, so the
secret was sitting in the open on five machines.

### The rules, in one module
`engine/victory.py`. `MatchPhase`, `Outcome`, `Verdict`, and `review(state)`,
which the authority calls after every accepted command and which answers with
**commands**:

- Piotrek's colour on the FINISH tile → `DeclareVictory`;
- every pawn on one field, his colour at the bottom → `DeclareVictory`;
- ...some other colour at the bottom, not checked before → `EliminatePawn`;
- otherwise nothing.

Returning commands rather than mutating is the load-bearing decision. An ending
that the server applied privately would be invisible to the command log — so a
player who reconnected would replay the log into a match that never finished —
and invisible to the fingerprint, which is the only desync detector there is.
As a command it travels the road everything else travels, and reconnection,
resync and desync detection all keep working with no new mechanism.

`review` is pure and safe to call anywhere, but only *decides* anything where
the hidden colour is known. On a client it is `None`, so a client running the
identical code decides nothing. That is the safety net under the whole design,
and there is a test that removes the secret from a winning position and asserts
silence.

### Where the identity lives, and where it must not
`Player.secret_pawn`, on the copies entitled to it: the server, and Piotrek's
own machine. `None` everywhere else until the reveal.

Two places it is deliberately absent:

- **`snapshot()`** — the snapshot is the fingerprint. A secret in it would make
  the server drift from all five replicas the instant it was told, and five
  people would resync for ever. A test compares snapshots of one state holding
  three different secrets and requires them identical.
- **the command log** — which is replayed to anyone who asks for a sync. The
  colour travels in `IDENTITY_CHOSEN`, to one peer, never logged, never
  relayed. A test greps the log for it.

`assign_secret_pawn` is now skipped online (`config.piotrek_picks_pawn`), and
its docstring says why at length, because it is exactly the kind of line a
future reader would "restore".

### Starting a match
`START_GAME` builds the state and broadcasts `GAME_START` as before, and the
server sends `IDENTITY_REQUIRED` to the one peer holding the Piotrek card.
Everybody is in `MatchPhase.STARTING`, where the **engine** refuses every
non-authority command — a client that never draws the overlay still cannot move.
`IDENTITY_CHOSEN` comes back, the server stores it and appends `BeginMatch`, so
the whole table starts on one broadcast. Piotrek dropping mid-choice is asked
again from `catch_up`.

### Ending a match
`DeclareVictory` sets `state.victory` and the phase to `ENDED`, and writes the
revealed colour into the player — the moment it stops being a secret is the
moment the state may hold it, and a client has no other way to learn it.

`ui/match_overlays.py` (new, presentation only): `MatchStartOverlay` — the
colour picker for Piotrek, "Gra się rozpoczyna…" with breathing dots for
everyone else — and `VictoryOverlay`, which is two endings rather than one with
a word swapped: green and rising from below for an escape, red and closing in
from above for a capture, the hidden pawn growing into the middle of both.

`GameScreen` drives them from `_sync_match_overlays()` **every frame, from the
state**. Events alone would have failed the one case that matters: a
reconnecting player replays twenty commands in a single frame, and the last
event past the window is not necessarily the ending.

"Wróć do poczekalni" sends `RETURN_TO_LOBBY`; the server drops the match and
broadcasts `LOBBY_STATE`, and every client — not just the one that clicked —
leaves the table on that message. Refused mid-match, or one player could end
everybody else's game. A hot-seat game has no lobby and does not offer it.

### The notepad stopped being a notepad
"Kolory Piotrka" now draws `state.eliminated_pawns` and ignores clicks. A
crossing means "a tower was lifted and it was not him", which only the
authority can establish; a private hunch on a panel that looks shared is worse
than no panel at all. `ToggleMark` and `Player.marks` still exist and still
work — nothing issues them.

### Tests
**630 passing** (595 before, +35), ~110 s.

`tests/test_victory.py` (new, 26): the rules against a bare `GameState` — both
wins, a wrong tower, an eliminated colour never checked twice, a tower one pawn
short, a replica that knows nothing deciding nothing, no verdict before the
match begins or after it ends — then the same things through the real server,
including that only one player is asked, that nobody moves until he answers,
that the colour reaches the server and no other client, that a client cannot
declare itself the winner, that a verdict reaches everyone and survives a
resync, that the room reopens as a lobby only when the match is over, and that
the next match gets a fresh secret.

`tests/test_ui.py` (+9): the two endings differ in colour and wording, the
overlay actually paints over the table, gameplay is dead once somebody has won,
a non-Piotrek client only waits, Piotrek's click sends the colour once, and the
online ending offers the poczekalnia while the hot-seat one does not.

`netkit.Table.playing()` now performs the identity step, so the **whole
existing multiplayer suite** exercises the new phase rather than one test that
remembers to. `starting()`, `piotrek()` and `choose_identity()` are there for
the tests that want the half-built state.

### Verification
- 630 tests, `--selftest` clean, `inspect_frame.py` 0 problems at 1920×1080.
- Both endings and the picker rendered headless and inspected as images; the
  capture ending had two red buttons that were hard to tell apart, so the
  secondary one is now neutral, and the identity hint was sitting on top of the
  pawn row at 1080p, so the panel grew.

### Notes
- Six existing multiplayer tests asserted absolute log positions (`sequence ==
  1`). `BeginMatch` is now entry #1, so they were rewritten as deltas — an
  absolute count has to be edited every time the authority learns to say
  something new.
- "All remaining pawns" is read as **all pawns**: nothing removes a pawn from
  the board, and an eliminated colour still has to stand in the tower. If the
  intended reading is different, `victory.gathering_tile` is the one function to
  change.
- Not attempted: a rematch that keeps the same seats but reshuffles, and any
  reveal animation for the pawn *walking* to the meta. The ending fires the
  moment the state says so.

---

## Stage 18 — Finishing what stage 17 started
**Date:** 2026-08-05

### 1. The picker was never being shown — and why the tests all passed
The identity selection worked online. I proved that first, by building a real
server, three real `GameScreen`s and asking each one whether the overlay was up:

```
who was asked:  [False, True, False]
overlay active: [True, True, True]
choosing:       [False, True, False]
```

So the report had to be about the OTHER entry point, and it was:
`MenuScreen._start` built a `SessionConfig` without `piotrek_picks_pawn`, so a
single-machine game fell through to `assign_secret_pawn` and dealt the colour
from the seed exactly as before. Stage 17 wired the flag into `lobby.to_config`
and stopped. Anybody testing alone — which is how you test alone — saw a random
colour and no picker, and every test I had written agreed with them and passed.

The fix is the flag plus a local answer path: `GameScreen._identity_question`
gets the colours from the server online and from the library in a hot-seat game,
and the click either sends IDENTITY_CHOSEN or calls `set_piotrek_pawn` and
submits `BeginMatch` locally. One overlay, one storage call, two sources of the
question.

`assign_secret_pawn` now runs only under `--players` and `--selftest`: a table
with no interface and nobody to click. That is the debugging fallback the brief
allows, and it is the only one left.

### 2. Three ways out of a finished match
`VictoryOverlay` had two buttons; it has three, stacked rather than in a row
because three captions of this length side by side shrink to unreadable at
1280×760.

- **Wróć do poczekalni** — keeps the connection, the room and the seats. The
  server drops the match and broadcasts `LOBBY_STATE`; every client leaves the
  table on that message, not on its own click. Start works immediately.
- **Menu główne** — `close()` then the main menu, so the seat is freed rather
  than held open behind a grace period nobody is waiting through.
- **Wyjdź z gry** — closes the application.

A hot-seat match has no room to keep and offers only the last two.

### 3. Piotrek's own reminder
A filled circle and the colour name above "Twoja Postać". Two independent
reasons it cannot leak: the row is only drawn on a Piotrek panel, and a client
may not look at anybody else's; and a hunter's replica has no colour in it to
draw in the first place. There is a test for each.

The row is **reserved whenever the panel is Piotrek's**, colour chosen or not.
Making it appear when the colour arrives would move every rect below it by a few
pixels between the frame that draws them and the click that hits them — the
ability card would end up just outside its own rectangle. `Layout.r_identity_h`
went into the right column's vertical budget, so it costs card height instead of
overflowing; measured at all four resolutions, the tightest (1280×760) keeps 26
px of slack.

It survives a reconnection because the server now re-sends `IDENTITY_ACCEPTED`
from `catch_up`. A returning client rebuilds its replica from the seed and the
log, and the colour is deliberately in neither — without this the badge came
back empty and stayed empty.

### 4. A rematch is a new game, not a tidied-up one
`return_to_lobby` already dropped the state, so the reset is structural rather
than a list of fields to clear: the next `start()` calls `create_game` with a
**new seed** and everything — board, decks, discard piles, hands, mods,
statuses, ability counters, round, turn, active seat, pawn positions, the
notepad and the hidden colour — comes from there. Clearing eleven things by hand
is how the twelfth gets forgotten.

One leak did need fixing: `_on_game_start` now clears `identity_pawn`, which is
re-applied to every replica this client builds. Without it the previous match's
colour was written into the new one.

`test_the_next_match_starts_from_nothing` plays a dozen turns, crosses a colour
off, wins, restarts, and compares fourteen kinds of state against a fresh table.
Two of them had to be compared loosely, and the reason is worth keeping: a
rematch reseeds, so Piotrek can land on a different seat, and his opening hand
is five cards or three depending on which skill he draws (ChatGPT trades two for
its range). The assertion is therefore "every hand is the opening hand the setup
would deal", not "the same numbers as last time".

### Tests
**647 passing** (630 before, +17), ~140 s.

`test_victory.py` (+9): a hot-seat game asks too; two clients building the same
seed cannot compute the colour; the random draw survives only where nobody can
click; the colour is chosen once and a second attempt is refused out loud; a
hunter cannot answer for Piotrek; the badge survives a reconnection while no
other client gains it; the rematch reset; the rematch keeps the players and the
connection; and no client still believes it holds last match's colour.

`test_ui.py` (+8): the hot-seat picker appears and starts the match, a pick
cannot be taken back, the badge is drawn in Piotrek's panel and still there
twelve turns later, no hunter panel draws one, and a state without the colour
draws nothing. Plus the main-menu button closing the connection, which is the
one thing that distinguishes it from the lobby button.

The badge tests read pixels out of the identity rect rather than asking the
panel what it would draw — a test that asks the drawing code whether it drew
proves very little about a leak.

### Verification
- 647 tests, `--selftest` clean, `inspect_frame.py` 0 problems at 1280×760 (the
  tightest case for the new panel row).
- Badge and end menu rendered headless and inspected.

### Notes
- Ready flags survive a rematch, so the host really can press Start
  immediately. If that turns out to be too fast — somebody wandering off after
  a game — clearing them in `return_to_lobby` is a one-line change.
- Spectators still do not exist. When they do, the thing to check is that they
  are never `identity_peer` and never receive IDENTITY_ACCEPTED; the colour is
  already absent from the snapshot and the log, so nothing else would leak.


================================================================================

## Stage 19 — The last four movement cards, and four mechanisms
**Date:** 2026-08-06

Troll, Stańczyk, Spy and Plagiat! were the four movement cards with no gameplay.
They are now implemented, which completes the movement deck. Almost none of the
work was the cards.

### 0. There was no if/elif chain to refactor
The brief asked for the card system to be refactored out of "an enormous chain
of if/elif statements" into a registry with handlers. That chain does not exist
and has not since stage 4: `engine/effects.py` already had `@effect("type")`,
pure handlers returning `Plan` / `Choice` / `Refusal`, and an Operation
vocabulary applied by one dispatch table in `GameState._execute`.

So the architectural work was not building a registry. It was finding the four
things the registry could not yet express, and adding each as a general
mechanism rather than as the card that needed it. All four are documented in
LLM_Instructions.txt under TURN INTERRUPTS, FORCED PLAYS AND MULTI-SELECT.

### 1. A card that acts when it is drawn
`_after_draw` knew about Gamechanger **by name**. Troll and Stańczyk would have
made that three branches of exactly the chain the effect engine exists to
prevent, so a card now declares `"on_draw"` and goes through the same registry
as a card that is played.

Draws cascade — Troll draws its own replacement and the replacement can be
another Troll — so every draw now goes through one `_draw_one()` and the chain
is depth-bounded.

### 2. A card that takes over a turn
One StatusKind, `TURN_INTERRUPT`, whose `data["effect"]` **is the effect spec to
run when the turn arrives**. `_begin_turn()` resolves it through the registry
and therefore knows nothing about either card. A future Chest card that hijacks
a turn is a JSON entry and no Python.

Finding, while wiring this up: **`SKIP_TURN` had been dead since stage 4.**
Lubin and Dziubdziuch both granted it and nothing anywhere read it, so both
abilities looked implemented and did nothing. `_begin_turn` now enforces it,
which is most of why Stańczyk needed almost no new code. This is the origin of
new rule N80 — when adding a status, add its consumer in the same change.

### 3. Forced play, without letting a handler roll dice
`ForcedPlay` is an Operation, not a handler decision, because handlers are
resolved by the UI to draw previews *while a card is being dragged* — a handler
that consumed randomness would change what the card does every frame (N78).

The executor picks from a list **sorted by uid**, not in hand order. Hand order
is not something two replicas are obliged to agree on, and relying on it would
desync the first time they differed.

### 4. Two pawns, in order, and the board moves under them
The genuinely hard one. `MovePawn` carries a finished route computed by a pure
handler, which is correct for one pawn and wrong for two: the second pawn
departs from a board the first has already rearranged, possibly carried along
inside a tower.

`MoveBySteps` has the **executor** recompute the route against the board as it
is when that operation's turn comes, so stacking, towers, widened rows and the
walk animation need no special case — by then it is an ordinary move of a pawn
that really is where the engine thinks it is. The only thing that cannot wait
is the 12a/12b question, because nobody can be prompted halfway through
applying a plan, so the handler projects the destination *position* (pure, via
`MoveProjection`) and asks in advance, one keyed question per pawn.

### 5. Bugs found while verifying
Four of these were pre-existing or latent, and none was in the brief:

- **A locked card dealt at setup locked a hand slot for the whole game.**
  Stańczyk was declared `locked` without being withheld from the opening deal.
  Locked cards are armed by `on_draw`, which the deal does not run, so it could
  never be played, discarded or resolved. Fixed structurally:
  `CardDef.opens_a_hand` derives the rule from `locked` rather than trusting
  the JSON (N79).
- **`SetActivePlayer` skipped turn interrupts.** A seat handed the turn in edit
  mode kept it, because the interrupt waited for an end-of-turn that had been
  skipped past — Troll would have looked broken to whoever was testing hot-seat.
- **The card face cache ignored the badge.** Two cards with the same title and
  different badges shared one surface. Latent until `Badge.count` existed. The
  badge went on the END of the cache key: the size lives at index 4 and
  `test_a_hovered_played_card_is_drawn_at_its_hovered_size` reads it there.
- **`netkit.playable_card` took the first movement card in hand**, which may now
  legitimately refuse to be discarded. Correct engine behaviour surfacing a
  stale helper, not a regression — but it broke `test_a_win_reaches_every_machine`
  depending on the seed, which is exactly the kind of intermittent failure worth
  killing at the source.

### 6. Multiplayer
**No new commands were added.** Every mechanic here replays from commands that
already existed — Troll and Stańczyk resolve inside whatever command ended the
previous turn, Spy and Plagiat! are ordinary `PlayCard` with `choices` filled
in — which is why none of them can desync.

Spy's hidden information rides on N40: `ChoiceRequired` goes to the asking peer
alone, and the card titles in that question *are* the opponent's hand, so that
rule is now load-bearing. `CardStolen` carries seats, a uid and a deck id and
never a title (N81). `TURN_INTERRUPT` is a Status, so reconnect is free — which
is precisely what N18 exists to buy.

### 7. The one judgement call
Stańczyk's card text said the card is "immediately discarded" on draw, but the
brief also requires a two-second highlight **of that card** at the start of the
next turn. Those conflict: you cannot point at a discarded card. It now stays in
hand (locked) until the interrupt resolves and is discarded then, so both cards
use one mechanism and one animation path. The Polish card text was reworded to
match. Worth revisiting if the original wording was deliberate.

### Tests
**683 passing** (647 before, +36), ~166 s.

New `tests/test_card_effects.py` (28): the four cards are declared in data and
not coded; every declared effect type has a handler; Troll stays in hand, arms a
status and draws a replacement; no player ever opens holding one; no locked card
is ever dealt, across forty shuffles, and none reaches the discard pile at
setup; Troll's forced play prefers Chest and falls back to Movement; an
unimplemented card is played and discarded and never blocks; Stańczyk skips a
turn and refills afterwards; SKIP_TURN is finally consumed; Spy shows only
movement cards, moves exactly one card, and the thief draws nothing while the
victim draws one; Plagiat! moves two pawns strictly in the chosen order.

`test_ui.py` (+8): the card picker opens showing only movement cards and can be
backed out of; the spotlight holds the board back and lets it go in real time;
a two-pawn badge is measurably wider than a one-pawn badge (rendered and
measured in pixels, not asked of the drawing code).

### Verification
- 683 tests, `--selftest` clean, `inspect_frame.py` 0 problems at 1280×760 and
  3840×2160.
- `engine/`, `net/` and `server/` confirmed free of pygame imports (R1).

### Notes
- The movement deck is complete. The next card work is Chest and Mods, and the
  four mechanisms above are parameterised for exactly that — read the new
  instructions section before writing a handler, because "act when drawn",
  "take over a turn", "look into another hand", "move several pawns in order"
  and "steal a card" all already exist.
- `BoardView.walk_delay` is the general answer to "show something before the
  board moves" without making the engine sleep. Reach for it rather than
  reinventing a wait.
- Hidden-information filtering (L1) is still the biggest remaining gap, and Spy
  makes it slightly more urgent: the client replica can technically see the
  hand it is asked about. The *question* is correctly addressed to one peer, but
  the underlying replica is not yet filtered.


================================================================================

## Stage 20 — The turn cursor
**Date:** 2026-08-06

The game looped over the first three slots of round one for ever. Everybody
further down the round never played, so rounds never ended, the round counter
froze, chest hand-outs stopped, statuses never expired, and Troll and Stańczyk
could not be reached at all.

### 0. It was not a stage 19 regression
Reported as one, and the first thing I did was check, because the fix depends on
knowing where the bug lives. It reproduces on the stage-18 tree with
byte-identical `turn_slot` code:

```
ORIGINAL (pre-stage-19), 6 players:
  round1 order: [4, 0, 1, 4, 2, 3, 4, 5]
  visited: r1:4 -> r1:0 -> r1:1 -> r1:4 -> r1:0 -> r1:1 -> ...
  distinct seats visited: [0, 1, 4] of [0, 1, 2, 3, 4, 5]
```

Thirteen of the new tests fail against that tree too. Stage 19 only made the bug
VISIBLE, by adding the first mechanics that need a round to finish — which is
exactly why it surfaced then. "The last change broke it" and "the last change
revealed it" look identical from the outside; the way to tell them apart is to
run the reproduction against the previous tree before touching anything.

### 1. Root cause
`turn_slot` is a cursor into the round's order, but `_end_turn` recovered it by
searching for the seat about to play:

```python
self.turn_slot = order.index(seat) if seat in order else 0   # WRONG
```

`list.index` returns the **first** match. Piotrek occupies every third slot, so
in `[Piotrek, Glockboy, Atencjusz, Piotrek, Dziubdziuch, Mitoman, Piotrek, ...]`
the cursor was reset to 0 every time his turn came round. It advanced 0, 1, 2,
reset, and did it again for ever.

The deeper mistake is that it was a search at all: **a seat does not identify a
slot.** Any seat may hold several slots in a round, so the seat number does not
contain enough information to say where the round is standing.

### 2. Fix
`next_turn()` returns `NextTurn(seat, round_number, slot)` — the slot travels
with the seat so no caller can reconstruct it wrongly — and `_end_turn` *moves*
the cursor to the slot it was handed rather than looking it up. The invariant is
now stated and self-healing: `seat_order()[turn_slot] == active_player_index`,
repaired by `current_slot()` when something outside the loop breaks it (a seat
set in edit mode, a round jumped to), searching **forward** so a repair can
never rewind a round either.

`turn_order.py`'s cadence was never wrong and was not touched. Piotrek still
takes every third slot and rounds still vary in length.

### 3. A second bug found on the way
`turn_counter` only advanced when the *seat* changed. With a single hunter the
cadence legitimately gives one seat two slots in a row, so those turns did not
count — and status expiry is measured in turns, meaning statuses outstayed their
welcome in exactly the configuration used for 2-player debugging. Every turn
counts now.

### 4. Synchronisation
`turn_slot` is now in `snapshot()`, and this is the sharpest part of the fix.
The cursor is real turn state that cannot be recomputed from the seat, so two
machines could stand on different slots, agree perfectly on whose turn it was,
and disagree about the whole rest of the round — with nothing in the fingerprint
able to see it. That drift is now a detected desync instead of a silent one.

No new commands. `EndTurn` still carries a seat, every machine derives the
cursor by replaying the log, and reconnection needs nothing.

### 5. Why 683 tests passed against it
Every existing test checked `compute_round_turn_order` — the function that
*computes* the order, which was correct all along. Not one drove `EndTurn`
repeatedly and asked who actually played. A game can loop over three seats in a
circle while every test of its scheduler passes.

`tests/test_turn_progression.py` (+21) walks real turns instead: every seat gets
a turn at 2/3/4/5/6/8 players; the cursor advances `0..n-1` strictly and never
rewinds when Piotrek plays; the documented cadence is what actually happens; a
round ends only once everybody has played; nobody plays twice in a row at a full
table; every turn counts; the cursor always agrees with the seat; it is in the
snapshot; edit-mode seat jumps and round jumps leave the walk intact; every
machine online walks the same order; a reconnecting client lands on the same
slot.

Thirteen fail against the old code — fourteen including the online one, which
**needed six players**: with three, Piotrek holds exactly one slot per round and
the bug cannot appear. A regression test sized too small is not a regression
test, and the first version of that test passed against the bug.

### Tests
**704 passing** (683 before, +21), ~160 s. No existing test changed.

### Verification
- 704 tests, `--selftest` clean, `inspect_frame.py` 0 problems at 1280×760 and
  3840×2160.
- End-to-end: 54 turns now reach round 8 and award 6 chest cards; before the fix
  the round counter never left 1.
- Movement cards, chest cards, abilities, end-turn button, automatic draws,
  round counter, current-player highlight and animations all untouched — the
  change is confined to the cursor.

### Notes
- The HUD's round panel highlights `turn_slot` directly, so it was showing the
  wrong circle for the whole life of the bug. That fixed itself.
- `next_seat()` survives as a look-ahead that does NOT move the cursor. If you
  need to advance, use `next_turn()`; the type is the reminder.

---

## Stage 21 — The Mod Patusa selection, hunter voting, and Thunderfuck
**Date:** 2026-08-06

### Starting point
Mods Patusa existed as a deck and a two-slot rack, but nothing put them into
play the way the physical game does. A mod reached the rack only by being
played from a hand or drawn by Thunderfuck — never by being *chosen*. The
round that pauses so both factions pick a mod simply did not exist.

Thunderfuck was implemented, and implemented wrongly: it filled the first FREE
slot, which put a new mod on the right while the left one stayed put. That is
the opposite of the rule, and it was invisible until the rack was half full.

### Implemented features

**1. The Mod Patusa selection.** Every second round from the third — both
numbers configurable in the lobby — `_begin_round` deals three mods to each
faction and parks a `ModSelection` on `pending_mod_selection`. While it exists
the table is paused: `_mod_selection_refusal` refuses every `_TURN_BOUND`
command from both `_authorise` and `authorise_remote`, so the pause holds
against a client that never draws the overlay.

**2. Piotrek's half.** Three cards, on his machine only. `ChooseMod` puts the
chosen one in the LEFT slot and discards the other two. The engine validates
the uid against what it dealt, so a client cannot name a card it was never
offered.

**3. Hunter voting.** `VoteMod` replaces that seat's entry rather than being
refused, which is what makes changing a vote work. Votes are public and every
`ModVoteCast` carries the whole tally, so no two screens can disagree about the
count. The last vote decides: most votes wins, ties go to the LEFTMOST tied
card. That falls out of `max` over the cards in dealt order — it is the
iteration order, not a sort.

**4. Slot ownership.** Each faction owns one slot for the rest of the game, so
`_settle_mod_side` writes its slot directly instead of pushing. Pushing would
have made Piotrek's choice shunt the hunters' card along the rack.

**5. Thunderfuck, rewritten.** One copy became three. With an EMPTY rack it now
does nothing at all — it *replaces* active mods, and before the first selection
there is nothing to replace, so seeding the rack would put a mod in play that
nobody chose. With anything in the rack: new card to LEFT, old LEFT to RIGHT,
old RIGHT discarded, on anybody's turn. The `prefer_free_slot` branch of
`_install_mod` is gone; it had no other caller.

**6. The overlay.** One `ModChoice` serves both factions — the same picture with
a different rule underneath — built on the card-picker geometry so three mods
look like three chest cards. Vote ticks in each card's upper-right corner,
running counts beneath, eased badges and pulses on every change.
`_sync_mod_overlay` decides which half a machine sees FROM THE STATE, so a
client replaying several commands in one frame lands on the right side.

Unlike the chest limit the pause is not modal: `_handle_mod_choice_event`
reports whether it consumed the event, and anything that is not a click on one
of the three cards falls through to the board. A hunter waiting on four other
votes can still pan and zoom.

**7. Settings.** `mod_round_first` (3) and `mod_round_interval` (2) reach the
engine from both the hot-seat menu and the network host screen, through
`SessionConfig` → `LobbyState` → `Room.set_settings`.

### Two bugs found on the way

**The hot-seat hand-off.** The first version marked Piotrek's side "settled"
and stopped there. In edit mode one person owns every seat, so the panel sat on
his spent cards and the vote could never be cast — which froze the table, since
the pause only lifts when both sides have chosen. Caught by rendering the flow
to PNGs and noticing two screenshots were byte-identical. `_voting_seat()` now
routes each click to the next hunter who has not voted.

**The menu's fitting loop.** It accepted a layout on row height alone and never
checked whether the footer fit, so it had been quietly accepting overflowing
layouts. A fifth settings row made the overflow big enough to see. It now
checks the real bottom edge; the two mod numbers share one row on both screens
because they read as one setting.

### Tests
**746 passing** (704 before, +42), ~135 s.

- `tests/test_mod_selection.py` — 31 new: the schedule, what each faction is
  dealt, slot ownership, one-vote-per-hunter and changing it, the leftmost
  tie-break, the pause locally and remotely, and the snapshot.
- `tests/test_ui.py` — 10 new: the overlay opening, clicking through Piotrek's
  pick, the hot-seat hand-off to the vote, counters, closing, the pause, the
  non-modal routing, resize.
- `tests/test_abilities.py` — the three Thunderfuck tests rewritten against the
  new rule, plus two more (any player's turn, three copies in the deck).

Sixteen existing tests broke on the pause, all correctly: they walk many rounds
and now stop in round 3. Three suites opt out with `mod_round_first=10_000`,
the idiom already used for the chest. No test was deleted or weakened.

### Verification
- 746 tests; the selection rendered to PNGs at 1920×1080 and inspected;
  `test_menu_layout` green at all four resolutions after the layout fix.
- Lobby settings round-trip checked end to end: `to_dict` → `from_dict` →
  `to_config`.

### Notes
- The mod rack heading now says when the next selection is due
  (`next_mod_round`), the way the turn bar already announces the chest.
- An open selection is in the fingerprint — candidates, votes, both done flags —
  but it carries UIDs, not titles, so nothing in the snapshot reveals what
  Piotrek was offered.

---

## Stage 22 — Chest hand-out timing on small tables
**Date:** 2026-08-06

### Starting point
The chest dealt a card every eligible round, to one hunter, rotating. That is
right for five or six players. Below that the rota is short enough that the
same hunter came round again almost immediately, and the chest stopped being an
event at all — at three players there are only two hunters, so it alternated.

### Implemented features

**1. A sparse cadence for small tables.** Five or six players are unchanged. A
table of four or fewer deals only every SECOND eligible round, counted from the
opening round so the chest always deals on the round it opens.

    round:      1  2  3  4  5  6  7  8  9
    <=4 players .  .  F  o  F  o  F  o  F
    5-6 players .  .  F  F  F  F  F  F  F

`chest_awards_cards(round)` is the new question, deliberately separate from
`chest_is_open` — the chest can be open while this particular round deals
nothing. `RULES.chest_sparse_max_players` (4) and `chest_sparse_interval` (2)
hold the numbers.

**2. The rota steps once per hand-out.** This is the part that matters. The
obvious implementation — leave the rota stepping every round and just skip
every other deal — starves hunters: with two hunters the dealing rounds all
land on the same half of the rota, so one takes every card in the game and the
other is dealt nothing for the entire match. `chest_recipient_for_round` now
takes an `interval` and indexes by ceiling division, so an awarding round takes
its own place in the rota and a skipped round borrows the place of the award
that follows it. At interval 1 this reduces to the old `(round - open) % n`
exactly, so five and six players are unaffected card for card.

**3. The indicator.** FILLED when a card arrives this round, OUTLINED
otherwise — which now covers both "the chest has not opened" and "this is a
skipped round". The marker still MOVES on a skipped round; only the fill
changes. It is drawn from `chest_awards_cards()`, the same call the engine
deals on, so a filled dot cannot promise a card that never turns up.

### Verified against the design's worked example
Three players, chest opening on round 3 — round for round:

    round 3   FILLED    Norbur    dealt
    round 4   OUTLINED  Lubin     marker moves, nobody dealt
    round 5   FILLED    Lubin     dealt
    round 6   OUTLINED  Norbur    marker moves, nobody dealt
    round 7   FILLED    Norbur    dealt

Rendered at 1920×1080 and inspected: filled dots on round 3, hollow dots on
round 4 with the marker moved to Lubin, filled again on round 5.

### Multiplayer
No new command, no new event, nothing added to the snapshot. The cadence is a
pure function of round number, opening round and table size, all of which every
replica already agrees on. It consumes no RNG, so it cannot shift a deck
shuffle out of step, and a skipped round takes no card off the pile — which is
what keeps the chest deck identical everywhere.

### Tests
**767 passing** (746 before, +21), ~137 s.

- `tests/test_chest_cadence.py` — 21 new: which table sizes are sparse, the
  threshold at four, that the opening round always deals whatever the size,
  that the rota feeds every hunter at 3 and 4 players, that interval 1 is the
  old rota exactly, that a skipped round costs the deck nothing, that the
  marker is filled exactly when a card arrives, and that two independently
  built replicas agree without exchanging anything.

One existing test needed adjusting, and it found a real interaction:
`test_a_chest_card_is_handed_out_when_the_round_opens` asserts the chest
rotates between hunters. Hand-outs are now four rounds apart on its
three-player table, and its turn loop could not get there — not because of the
budget but because the stage 21 Mod Patusa selection pauses the table in round
3 and refuses every play. `_loop_game` now sets `mod_round_first=10_000`, the
opt-out idiom stage 21 established, and the loop budget went 20 → 60. The
assertion itself is untouched — it is the one that proves N91.

### Notes
- `chest_recipient_for_round`'s `interval` defaults to 1, so every existing
  caller and the existing rota test keep working unchanged.
- The old behaviour is still reachable: a 5–6 player table takes the
  `interval == 1` path and is byte-identical to stage 21.

---

## Stage 23 — Piotrek is dealt chest cards again
**Date:** 2026-08-06

### The bug
Piotrek never received a chest card. Hunters received theirs correctly, at
every table size.

### Root cause — and it is older than it looks
`_distribute_chest_card` dealt to `chest_recipient_seat()`, and that method is
hunters-only by construction:

    hunters = [p.index for p in self.players if not p.is_piotrek]

Meanwhile the turn ribbon has drawn a dot under Piotrek's first slot since the
chest existed. So the marker promised him a card and the engine never delivered
one — at every table size, in every mode.

**This was not introduced by the stage 21 or 22 work.** I checked the stage 20
archive: the same hunters-only filter and the same Piotrek dot are both there.
Stage 22 made the marker meaningful (filled vs outlined), which is presumably
what finally drew attention to a discrepancy that had always been on screen.

The suite stayed green through all of it because a test asserted the broken
behaviour outright:

    for award in awarded:
        assert not game.players[award.player_index].is_piotrek

### The fix
`chest_recipient_seats()` returns the seats due a card on a dealing round:
**Piotrek always, plus the hunter the rota has reached**, Piotrek first.
`_distribute_chest_card` loops over it; the ribbon reads the same call.

Nothing about timing changed. The hunter rotation is untouched — Piotrek is
additive and takes no place in it, so `chest_recipient_seat()` (singular) still
means exactly what it did.

### A second bug the fix exposed
Two seats are now fed per round, so two can exceed the chest limit on the same
round. `pending_chest_choice` was a single slot, and the second overflow
silently overwrote the first: that player stayed over the limit and their extra
card left circulation permanently. With eight chest cards the deck bled dry
within a few rounds and hunters stopped being dealt anything — which looks
exactly like the original bug.

It is a queue now (`_pending_chest_choices`). `pending_chest_choice` survives
as a property returning the oldest entry, so every existing caller and test
still sees one prompt at a time. In the interface `_on_chest_limit` no longer
replaces a prompt that is already up, and `_open_next_chest_choice` brings the
next one forward as each is answered.

### Verified
Rounds 3–13 stepped for 2, 3, 4, 5 and 6 players. In every configuration the
dealt seats equal `chest_recipient_seats()` exactly, Piotrek included, and the
card accounting is conserved — nothing leaks. Online: a five-player table taken
to round 5 across a real server, all replicas agreeing on both recipients and
on the fingerprint. Rendered the ribbon at 1920×1080: two filled dots on
dealing rounds, two hollow ones on skipped rounds.

### Known content shortage, deliberately not fixed
The chest deck holds eight cards. At the limit a table holds
Piotrek 2 + one per hunter, so a full six-player table keeps seven of them and
leaves one circulating — while a dealing round wants two. Piotrek is dealt
first, so at six players the hunter is the one who goes without once everybody
sits at their limit.

Nothing leaks; there simply are not enough cards. The fix is a content one
(raise the `count` values on the chest cards in data/cards.json), and deck size
is a balance decision, so it is recorded here and in LLM_Instructions.txt
rather than made silently. `test_a_full_table_runs_the_chest_deck_dry` pins the
current behaviour so the decision stays visible.

### Tests
**791 passing** (767 before, +24), ~125 s.

- `tests/test_chest_cadence.py` — +24: Piotrek dealt at 2/3/4/5/6 players, on
  every dealing round and never on a skipped one; a dealing round feeds exactly
  Piotrek plus one hunter; the indicator names exactly who is dealt; the hunter
  rota is unchanged by Piotrek joining; a table with no Piotrek still deals;
  two overflows in one round are both asked; no card leaks out of the deck; the
  six-player shortage; replicas agree on both seats in order.
- Three tests in `test_abilities.py` corrected — they asserted Piotrek is never
  fed, and one stopped counting after two awards, which is now a single round.

### Notes
- N94: never write a test that asserts a marker and its mechanic disagree.
- N95: two players can be over the chest limit at once; the prompt is a queue.
- N96: recipient order is fixed (Piotrek first) because two draws off one pile
  in a different order on two machines is a desync.

### Follow-up (same stage) — the chest deck goes to sixteen
The six-player shortage recorded above is fixed rather than left standing.

Every chest title now carries `"count": 2`, taking the deck from 8 cards to 16.
The doubling is uniform on purpose: the minimum workable size was 9, but
reaching it means bumping a single title to two copies and making that one card
twice as likely as the other seven. Doubling everything removes the shortage
while leaving the relative odds exactly as they were, and leaves 9 cards
circulating at a full table instead of a bare 2.

Measured before and after, short rounds out of 30 at each table size:

    deck   2p  3p  4p  5p  6p
    8       0   0   0   0  25
    16      0   0   0   0   0

Re-checked over 40 rounds at every size from 2 to 6: zero short rounds, Piotrek
fed on every dealing round, all 16 cards accounted for at all times.

The `cards.json` edit was made by parsing and re-emitting the file, which
round-trips byte-identically at indent 2 — so the diff is exactly 8 added
lines inside the chest deck and nothing else. The mods (8) and movement (72)
decks are untouched.

`test_a_full_table_runs_the_chest_deck_dry` pinned the old shortage and is
replaced by `test_the_deck_can_supply_a_full_table_indefinitely`, which asserts
the opposite. Six players is back in the two strict recipient tests it had been
excluded from, and the deck-size assertion in `test_engine.py` is 8 → 16.

**797 passing** (791 before, +6), ~156 s. N97 added.

---

## Stage 24 — The Mody Patusa start doing things
**Date:** 2026-08-06

### Goal
Four of the eight mods gain their real effect, the deck stops being one copy of
each, and the composition becomes a lobby setting. Nothing else was touched:
no rewrite of movement, turn order or victory.

### The deck (parts 1 and 2)
`cards.json` now gives every mod a `count`: Speedrun 2, Masa solna 2, AKO 1,
Halloween 1, Sesja na PG 2, Paczka 2, Squid Game 1, Shady 2 — **13 cards**, up
from 8. Every title still appears ONCE in the data; copies come from `count`,
never from repeating the entry.

Those numbers are DEFAULTS, not constants. `SessionConfig.mod_counts` overrides
them per title, and `DeckDef.with_counts()` builds the altered deck. Two things
about that method matter and both are load-bearing:

- an ABSENT title keeps the printed count, so an empty mapping means "the real
  deck" — that is what an older client, and every existing test, sends;
- the CARD ORDER stays the JSON's. A shuffle is a permutation of a list, so a
  list assembled in the mapping's iteration order would shuffle differently on
  two machines from the same seed. Do not rebuild it from the mapping.

`clamp_mod_counts` (config/settings.py) is shared by the config, the lobby and
the server so all three agree what a payload means, and it returns the pairs
SORTED because the mapping travels inside the lobby snapshot clients compare.

`room.set_settings` MERGES the incoming counts rather than replacing them. The
panel sends the titles it knows about; a host on an older build sends none, and
a replace would have emptied the deck.

### The rules are in the JSON, not in the engine
Every mod that does something declares a `passive`:

    Speedrun     {"reverse_backward": true}
    Masa solna   {"movement_cap": 1}
    Halloween    {"require_neighbour": true}
    Sesja na PG  {"abilities_locked": true}

`GameState.mod_rule(key)` reads the rack; `movement_cap`, `abilities_locked`,
`requires_neighbour` and `reverses_backward_moves` are named wrappers over it.
No part of the engine asks a mod what it is CALLED — the project rule holds. A
new mod that caps movement is a JSON entry.

The LEFT slot wins a disagreement between two mods. Nothing declares a
conflicting pair today; the rule exists so that the day one does, two machines
resolve it the same way instead of by dictionary order.

### EffectContext learned three things
`origin` ("card" / "ability" / "on_draw"), `deck_id`, and `can_ask`.

The first two exist because Masa solna and Halloween apply to MOVEMENT CARDS
and Dziad's ability is an ordinary `move_pawn` that both would otherwise have
caught. `ctx.from_movement_card` is the test.

`can_ask` is subtler and fixes a bug that would have shipped. Seks z pedałami
and Troll's forced play pick a card from `resolves_without_asking` — a property
of the PRINTED card, computed from its spec, which cannot know that Speedrun
has just given it a question to ask. Without the flag, every revealed backward
card became "bez efektu" while Speedrun was in the rack. With it, those two call
sites resolve non-interactively and Speedrun declines the reversal, which is
always legal because the mod only ever OFFERS one.

### Speedrun (part 3)
A backward movement card asks which way to go, through the existing
`ChoicePrompt` with `kind="option"` — the same modal as the 2A/2B field
question, as asked.

THE ORDER OF THE QUESTIONS IS PART OF THE RULES: direction, then pawn, then
which half of a widened row. Speedrun is resolved at the TOP of `_move_pawn`,
before `_movement_target`, because the card's printed direction is all it needs
and asking after the pawn would mean picking a pawn to move backwards and only
then being told it could go forwards.

Only cards whose direction is literally `backward` ask. `direction: "either"`
(Dziad) does not: those already let the player choose, and a second question
about the same decision would be asked twice.

Plagiat! reaches movement through `_move_pawns`, a different handler, and gets
the same treatment there. That it is a different handler is an implementation
detail and must not make the card behave differently.

### Masa solna (part 4)
Caps the distance a movement card DECLARES at one field, sign preserved, so
Astral 2022 moves one back rather than two. It hits exactly the nine cards the
brief lists — Obniżenie progu, Astral 2019, Astral 2022 and the six Fillerski
przedmiot colours — because those are precisely the entries with `steps: 2`.

NOT capped: abilities (Dziad), chest cards, and the ChatGPT movement bonus. The
bonus is a charge somebody spent an ability to get, it is added after the cap,
and swallowing it here would have quietly rewritten a skill this stage was not
asked to touch. A capped two-field card plus the bonus therefore moves two.
Recorded rather than decided silently — see KNOWN LIMITATIONS.

### Halloween (part 5)
A pawn with nobody directly in front of or behind it cannot move. The card
still RESOLVES: it is played, discarded, the hand refills and the turn passes —
only the movement does nothing.

That could not be an empty `Plan`. `Plan.ok` is `bool(operations)`, so an empty
one reads as a REFUSAL to every caller and the card would have stayed in the
hand — the opposite of the rule. It follows the idiom Thunderfuck already
established for an empty rack: a real operation, `Fizzle`, whose executor
changes nothing and emits `MoveFizzled` so the player is told why. A card that
silently did nothing looks like a bug.

Sharing a field is NOT being a neighbour: a tower is one field, and the rule is
about what is in front and behind, not underneath.

**THE CAMP IS ONE CLUSTER, and this is a decision, not a reading.** Every pawn
starts in the camp at `CAMP_INDEX`. Taken literally nobody there has a
neighbour, so Halloween reaching the rack while the field is still waiting —
which `mod_round_first: 1` in the lobby allows — would have frozen the board
permanently: nothing could move, nobody could reach the finish, no tower could
be built, so NEITHER FACTION COULD WIN. Pawns waiting shoulder to shoulder in
the camp are neighbours of one another. Flagged for the owner.

On a multi-pawn card each pawn is judged against the board THAT move sees, via
`MoveProjection.positions` — an earlier move can give a later pawn the
neighbour it needed, or take it away. A pinned pawn is skipped and the rest of
the card carries on.

### Sesja na PG (part 6)
Refused in `_use_ability`, BEFORE anything resolves, so no charge is spent: a
player who had two uses when the mod arrived has two when it leaves. Blocked in
the engine and not only in the interface, for the usual reason — a client that
does not grey the button must still be unable to act.

The button uses the same `enabled=False` styling as a spent ability, but reads
"SESJA NA PG" rather than "ZUŻYTE", because locked is not spent and a caption
saying otherwise would be false.

### AKO and the rest (part 7)
AKO, Paczka, Squid Game and Shady are placeholders with counts and no
`passive`. They draw, they are selectable, they occupy a slot, they do nothing.
There are tests asserting exactly that — "it does nothing yet" is worth pinning
so the next person does not mistake it for a bug they introduced.

### The interface
`ui/mod_counts_panel.py` — one panel, used by BOTH the hot-seat menu and the
network host screen. One deck needs one place to describe it.

It is an OVERLAY and not eight more settings rows because both screens lay
themselves out by measuring and shrinking their gaps until they fit; stage 21
already records a fifth row pushing the Start button off a 1280×760 window.
Eight more would not fit at any gap on either screen.

Two layout bugs were caught by looking rather than by the suite:

1. the button first sat UNDER the Mod Patusa row and landed on the "pola
   podwójne" label at 1280×760, where the gaps are tightest. It belongs BESIDE
   the row, in the margin, which is free at every supported size.
2. the steppers hung over the panel's right edge. `Stepper` sizes its buttons
   to its own labels at the current type scale, so the hard-coded inset was
   wrong everywhere. The row is measured now and placed by its right edge.

Both have tests, the first parametrised over four window sizes.

### Verified
- Rendered the menu and panel at 1920×1080 and 1280×760, open and shut.
- Six consecutive four-player matches over a real in-process server with a
  resized deck (Speedrun 3, Halloween 2, Paczka 0): all four rules exercised
  through the real command path, every replica AND the server's authoritative
  copy compared after each step. No divergence.
- `--selftest` exits 0.

### Tests
**884 passing** (797 before, +87), ~136 s.

- `tests/test_mod_rules.py` — 53 new: the composition; resizing; clamping;
  determinism across two builds; an emptied deck not hanging the round; the
  rules coming from the JSON; Masa solna over all nine two-field cards and the
  things it must not touch; Speedrun's prompt, its order against the pawn and
  tile questions, the multi-pawn path and the non-interactive fallback;
  Halloween's neighbours, the tower case, the camp, the discard, and per-pawn
  skipping; Sesja na PG's refusal and preserved charges; the placeholders.
- `tests/test_mod_counts_sync.py` — 8 new: settings reaching every client,
  host-only, identical decks and uids everywhere, fingerprints agreeing,
  server-side clamping, and partial updates merging.
- `tests/test_mod_counts_ui.py` — 26 new: the panel's contents, defaults,
  bounds, reset, closing, the modality, the counts reaching a built deck, the
  host screen having the same panel, and the two layout bugs above.

One existing test changed: `test_deck_sizes_match_the_data_file` asserted the
mods deck was 8. Its docstring says a count change should make somebody confirm
the new number, so it is 13 with a comment saying why — the assertion is doing
its job, not in the way.

### Notes
- N98: mod rules are declared in cards.json as `passive`; never match a title.
- N99: an empty Plan is a refusal; a move that does nothing needs an operation.
- N100: a card played by another card cannot be asked a question.
- N101: Halloween treats the camp as one cluster, or the game can deadlock.
- N102: the deck panel is an overlay because the settings rows do not fit.

---

## Stage 25 — The last three Mody Patusa
**Date:** 2026-08-06

### Goal
Paczka, Squid Game and Shady — the three placeholders left after stage 24 —
gain their real effects. AKO is still a placeholder; nothing else was touched.

### 0. The checking mechanic already existed
Two of these cards are about checking, and `LLM_Instructions.txt` says in four
places that the checking mechanic is unimplemented and is "the main blocker".
That text is STALE: it predates stage 17, which built checking as
`engine/victory.py::review()` — all pawns on one field, inspect the bottom
pawn, hunters win or the colour is crossed off. The instructions have been
corrected rather than worked around.

This mattered: it is the difference between "implement checking, then modify
it" and "modify one condition in `review`". Reading the changelog past the
instructions is what caught it, and it is the reason L5 and the old §1 of the
development order have been rewritten.

### 1. The rules are in the JSON, as usual
    Paczka      {"reveal_chest": true}
    Squid Game  {"lead_check_only": true}
    Shady       {"hide_leader": true}

plus the three wrappers on `GameState` — `chest_cards_revealed`,
`lead_check_only`, `hides_leader`. No part of the engine asks a mod what it is
called (N98). A test now walks every `passive` key in the deck and asserts
something reads it, which is N80 applied to mods: a rule nothing consumes is a
card that looks implemented and does nothing.

### 2. The thing these three needed that stage 24's four did not
Speedrun, Masa solna, Halloween and Sesja na PG are pure passives: the answer
depends only on what is in the rack *now*. All three of these depend on a
MOMENT instead — Paczka acts when it arrives, Squid Game needs to know which
round it arrived in so its first check falls on the next one, and Shady acts on
arrival and again a round later.

So `GameState.armed_mods` maps a mod's card uid to the round it entered the
rack, and `_sync_mod_states()` computes the TRANSITIONS into and out of that
mapping. It is called from every path that changes `mod_slots` — the selection,
`PlaceMod` and Thunderfuck — which is what stops a fourth way into the rack
silently skipping an arrival.

The departure half is load-bearing and easy to forget: a mod that leaves must
leave nothing behind. Removing Shady puts its pawn back at once (otherwise
replacing it mid-round strands a pawn nowhere for the rest of the match), and
removing Squid Game drops any pending check and restores ordinary checking.

### 3. Paczka
`ChestCardsRevealed` carries, per holder, the seat, the name and the card
titles. Every machine builds the same list from its own replica and shows its
own window, dismissed independently with OK; nothing is synchronised because
nothing changes. A test compares the three machines' lists.

**It deliberately breaks N81**, which forbids a card title in an event the
whole table sees. That rule exists because only one player was entitled to
look; here the card IS the entitlement — Paczka's entire text is that the Chest
is public. Recorded as the one sanctioned exception, in the event's docstring
and in the rules list, because a future reader finding it will otherwise assume
it is a leak.

The window is a LIST, not a row of card faces: six players holding two cards
each is twelve faces and no lineup fits them at 1280×760. It borrows the card
picker's furniture — dimmed table, `premium_panel`, brass heading, one
`interactive_panel` button — so it reads as the same family of window as the
chest limit rather than as a fourth kind of dialog. Empty table gives the
brief's sentence, "Nikt nie posiada obecnie kart Skrzyni."

Above the keyboard dispatch in `handle_event`, so Esc closes the window instead
of opening the pause menu, and it consumes only the input that dismisses it —
the table underneath stays live while somebody reads.

### 4. Squid Game, and the part that could have leaked
Two halves, both from the one rule so they cannot get out of step: the ordinary
gathering check is skipped entirely in `review`, and an automatic check runs at
the start of every round after the one the mod arrived in.

**THE SPLIT IS THE WHOLE DESIGN.** Deciding WHO is checked is public — the
single furthest pawn — so `_arm_lead_check` runs in `_begin_round` on every
machine and they all agree. Deciding what that MEANS needs the hidden colour,
so it happens in `victory.review` on the authority alone and comes back as the
existing `EliminatePawn` or `DeclareVictory`. Doing it any other way means
either the server drifting from five replicas or a client judging a winner
(N72), and both are worse than the extra field.

**No new command.** The verdict travels the road every other verdict travels,
so logging, broadcast, replay, reconnection and the fingerprint all keep
working untouched.

`pending_lead_check` is cleared by the COMMAND that settles it, in
`_eliminate_pawn` and `_declare_victory` — not by the code that armed it, or
the authority would clear its own copy and every replica would go on waiting
for a check that already happened.

A shared lead is SKIPPED, not tie-broken, and the skip is reported: a round
where nothing happens is otherwise indistinguishable from a broken mod. A
colour already crossed off is not checked again. Piotrek reaching the meta
still wins — Squid Game replaces checking, not the other ending.

### 5. Shady
`StatusKind.HIDDEN` on the pawn (N18), carrying the riders and the round, so
the snapshot, the serialisation and reconnection come for free.

The pawn taken is the BOTTOM of the furthest occupied field, which is a
different question from Squid Game's and needed its own function: the lead mod
skips a shared lead, Shady is told what to do about one.

Removed from the board, and ignored by `hindmost`/`foremost` (one filter in
`_ordered_pawns`), by `pawn_options`, by `MoveProjection.positions` and by
`has_neighbour`. A card that names it anyway resolves, is discarded and does
nothing — `Fizzle`, the Halloween idiom (N99), applied in `_move_pawn`,
`_move_pawns` AND `_stack_pawn`, because N104 is exactly about this.

On screen it disappears through ONE omission, in `BoardView._draw_order` — the
list that is painted is also the list `token_at` hit-tests, so it stops being
drawn and stops being clickable in the same line. `forget_pawn` drops its
animation state, or it would slide the length of the board when it reappeared
somewhere it has never been.

Checking counts against the pawns ON THE TABLE rather than against the palette,
which is the whole of the exception: five onto one field while a pawn is away,
six again once it is back.

Restored at the start of the next round, on top of the rearmost pawn, before
anything else in `_begin_round` sees the board. One-time: it stays on display
and never fires again, because arming is a transition. A SECOND Shady is a new
arrival and does hide a pawn.

### The brief's arithmetic, decoded
The brief says checking normally needs "all five remaining pawns" and four
while Shady is active. The palette has SIX pawns, so that looks off by one. It
is consistent if the count is *pawns stacked onto a base pawn*: five onto one
is all six, four onto one is all five. That reading also matches the brief's
own "since one pawn is absent", and it is what the code does — checking
requires every pawn currently on the board.

### The ruling the brief did not settle
See L17. The brief says Shady removes "the BOTTOM pawn of that stack" and its
example removes ONE pawn (pink on green: "green disappears"), but it also says
to store and restore "its carried stack". Those conflict. One pawn leaves; the
riders settle onto the field. The carried stack is recorded anyway, so the
other reading is a small change rather than a rewrite. **The owner should
confirm.**

### Tests
**944 passing** (884 before, +60), ~138 s.

- `tests/test_mod_effects.py` (new, 42): the three rules coming from the JSON;
  Paczka's list, its omissions, its empty case, that it changes nothing and
  fires once; Squid Game not checking in its arrival round, checking every
  round after, crossing off a wrong colour, winning it for the hunters, both
  skip cases, camp pawns not leading, a blind replica deciding nothing, the
  snapshot, the clearing, and Piotrek's own ending still working; Shady's
  target including the bottom-of-a-tower case, the four ways a hidden pawn is
  ignored, the fizzle, the reduced checking requirement and its restoration,
  where the pawn comes back and where it does not, one-time-ness, a second
  Shady, removal restoring at once, and the status in the snapshot.
- `tests/test_mod_effects_sync.py` (new, 8): every machine building the same
  Paczka window, no command logged for it, the automatic check armed everywhere
  and judged once with the result reaching every machine, the armed round in
  the fingerprint, that the server learning the colour does NOT move the
  fingerprint, the same pawn hidden and restored everywhere, and the table
  still agreeing after a dozen turns.
- `tests/test_ui.py` (+13): the window opening, listing, painting, dismissing
  by button and by Esc but not by a stray click, not blocking the table, the
  empty sentence, fitting at three resolutions with six players holding two
  cards each; a hidden pawn neither painted nor clickable; the skipped check
  still reported.
- `tests/test_mod_rules.py`: the two placeholder tests were honest about stage
  24 and are now wrong, so they were updated rather than deleted — AKO is the
  only placeholder left, and `install` now goes through `_sync_mod_states` so a
  mod that acts on arrival does so there too.

### Verification
- 944 tests; `--selftest` exits 0; `inspect_frame.py` 0 problems at 1280×760
  and 3840×2160.
- R1, N44 and N34a re-checked by grep: no pygame in `engine/`, `net/` or
  `server/`, no radial glows, no literal colours in the new overlay.
- The window rendered at 1920×1080 and 1280×760, full and empty, and inspected
  as images.

### Notes
- Three test bugs of my own were worth recording because each is a trap:
  a title read from a pile *inside* the loop that empties it named a different
  card each iteration; `review_victory()` called by hand returns messages that
  nothing delivers, so a verdict must be triggered by a real command the way
  `test_victory.py` does; and "a client decides nothing" is false on PIOTREK's
  client, which legitimately holds his own colour — the safety net is that a
  `NetworkSession` never calls `review`, not that the answer would be empty.
- AKO is the last mod without an effect, and it is the only one whose text
  ("Wszystkie ruchy poruszają jednego sąsiadującego pionka") still needs a
  ruling from the owner before it can be written.

---

## Stage 26 — Settings that cover the whole table, and type you can read
**Date:** 2026-08-06

### Goal
Four settings tabs instead of one, two new default numbers, better use of the
pixels a 1920×1200 laptop has, and two pieces of feedback that were too easy to
miss. No gameplay, no networking model, no card logic, no mod behaviour.

### 1. One panel, four tabs
`ui/mod_counts_panel.py` became `ui/settings_panel.py`, and `ModCountsPanel`
became `GameSettingsPanel`: Karty ruchu, Mody Patusa, Karty Skrzyni,
Umiejętności. The rename is honest rather than cosmetic — the file no longer
describes one deck.

A tab is a `SettingsTab` record: where its rows come from, what bounds them,
what its numbers add up to and what is wrong with them. Nothing else differs
between the four, so a fifth category is one entry in `_build_tabs` and no new
drawing code. Every title and every default is read from the LOADED DATA, so a
card added to cards.json appears in the panel with no code change.

**The movement deck has thirty titles and no window is tall enough**, so the
list scrolls — wheel, arrow keys, a thumb and an "1–17 z 30" counter — rather
than the rows shrinking into illegibility. The steppers are built per VISIBLE
row and zipped against `visible_titles`, which is the one thing to keep in mind
when touching this: the row you click is not the nth title.

**Umiejętności configures charges, not copies**, and lists both ability decks in
one place — a Piotrek skill is an ability with a number of uses exactly as a
character's is. Cards with no ability are left out: a charge counter on a card
that can never spend one is a control that does nothing.

`Domyślne` resets the VISIBLE tab only. A player who spent five minutes on the
movement deck and then wanted the mods back as printed should not lose the lot
to one button, and the button sits under the tab it undoes.

### 2. The numbers underneath
`SessionConfig` gained `movement_counts`, `chest_counts` and `ability_uses`
beside the existing `mod_counts`. Separate fields rather than one nested
mapping, because `mod_counts` is already on the wire, in the lobby snapshot and
in a dozen tests — a client on an older build sends the old field and still gets
the deck it asked for. All four follow the same rule: EMPTY MEANS AS PRINTED.

`DeckDef.with_uses` mirrors `with_counts` (same two load-bearing properties: an
absent title keeps its printed value, and the card ORDER stays the JSON's, or
two machines shuffle differently from one seed). `setup.build_decks` applies all
four BEFORE the shuffle.

The four clamps are one `clamp_title_map` with three thin wrappers, and the
server merges the three new mappings in one loop rather than three copies of the
`mod_counts` block. Four hand-written merges differing only in their clamp is
how one of them quietly stops merging.

### 3. New defaults
`chest_open_default` 3 → **6**. `mod_round_first_default` was already 3 and is
unchanged. Nothing else moved.

### 4. Scaling — what was actually wrong
Measured before changing anything, and the numbers say it plainly:

    resolution    ui_scale   hand card   panel card
    1920x1080       1.00      162x232      113x162
    1920x1200       1.11      180x258      125x179
    2560x1440       1.33      182x260      157x224
    3840x2160       1.80      182x260      186x265

**A 4K screen showed exactly the same 182×260 hand card as a 1920×1200 laptop.**
Two flat ceilings did it: `hand_h` capped at 300 and the hand card at 260, so
every pixel past about 1224 tall went into margin. Both now scale with
`ui_scale`.

The second half is the reference height, moved from 1080 to **1000**. Keyed to
1080, a 1920×1200 laptop — a physically small panel with a lot of pixels — ran
at 1.11 while a 2560×1440 desktop monitor was comfortable at 1.33. Moving the
reference lifts the middle of the range about 8% and leaves both ends where they
were: 1280×760 is still on the floor and 4K still on the ceiling.

    after:        ui_scale   hand card   panel card
    1280x760        0.85      114x163       75x108
    1920x1080       1.08      162x232      112x161
    1920x1200       1.20      180x258      123x177
    2560x1440       1.44      216x309      149x213
    3840x2160       1.80      325x465      239x341

Board share stays 0.60–0.71 of the window at every size, so the cards were not
paid for out of the board.

**Enlarged previews were already correct and were verified rather than changed.**
`CardRenderer.quantised` has no ceiling, so a hovered card at 1920×1200 is
painted at 386×552 and not zoomed from 180×258 — stage 9's work, now working
from a bigger base. There is a test that measures it at two resolutions.

### 5. Two bugs the bigger type exposed
Both were latent and both are the same shape: a box that stopped growing while
its contents did not.

- **The deck label band** was capped at a flat 26px, so at 1440p a deck name
  overflowed its own band and touched the card below. The ceiling scales now, in
  both the place that budgets the column's width and the place that spends its
  height — the two have to agree about how tall a label line is.
- **"UMIEJĘTNOŚCI PIOTRKA2 / 0"** — the longest deck name in the game ran
  straight into its own counter. The counters own the right of the band and
  cannot shrink, because they are the data; the NAME is fitted to what is left.
  Found by rendering the right column and looking at it, not by the suite: the
  existing collision test walks the LEFT column only, so Piotrek's panel had
  never been measured. The new test renders and reads back what was actually
  drawn, and it fails at exactly the three resolutions where the bug appeared.

### 6. "Nie Piotrek", where it can be seen
A failed check was one line in the status bar, in the corner of the screen
nobody watches while a tower is being lifted — the single most important thing
the hunters learn all game was the easiest thing on screen to miss.

It is now a card against the inside of the board's left edge: heavy red cross,
the colour's own dot, `ŻÓŁTY` / `TO NIE PIOTREK`. It fades in, holds 3.4 s and
fades out, and it swallows no input at any point — there is a test that clicks
straight through it. Inside the board rather than in the left column because
that column is full at every supported size, and a notice that only fits on a
big monitor is a notice that is missed on the machine that reported the problem.
The status-bar line stays as well: the card is the announcement, the bar is the
log for somebody who looked away.

Driven from the event rather than from the state, which is the opposite of N74
and deliberately so: this is a MOMENT, not a condition, and a reconnecting
client replaying twenty commands should not be shown four announcements it
missed. Each show replaces the last, so it is not.

### 7. The eliminated colour
A flat 4-pixel line — a hairline on a 1440p panel. Now the disc is drained, the
ring is red, and the cross is `Renderer.heavy_cross`: thickness a SHARE OF ITS
SIZE rather than a pixel count, with a darker cross behind it for contrast.

It lives in the renderer, not in either caller, so the notepad and the
board-side notice cannot drift into two different marks. Drawn rather than
typed, because the crossed glyphs are not in every font the game may fall back
to and a missing glyph is a blank box exactly where the clearest mark on screen
should be.

### Tests
**995 passing** (944 before, +51), ~150 s.

- `tests/test_settings_panel.py` (new, 51): every category has a tab; the deck
  tabs and the ability tab are seeded from the data; a card with no ability gets
  no row; clicking a tab changes the list; a stepper changes only its own tab;
  values cannot leave their range; the long tab scrolls and cannot be scrolled
  off either end; reset touches one tab; a tab warns when its deck cannot work;
  the panel, its tab strip, its steppers and its buttons all fit at three
  resolutions; the numbers reach `SessionConfig`, the built decks and the actual
  cards; an empty mapping still means the printed decks; the clamps reject junk
  and sort; all three new mappings reach every client, produce identical decks
  and merge rather than replace; the host screen offers the same panel; both new
  defaults; a taller display gets a bigger card at three steps; 1920×1200 is no
  longer the odd one out; the board keeps its share at five sizes; an enlarged
  preview is painted at full size; the notice appears, sits beside the board,
  fades, swallows no input and is actually painted; the elimination mark scales
  and a bigger window draws a bigger one; and the heading-collision regression.
- `tests/test_mod_counts_ui.py`: updated for the rename and for one stepper per
  VISIBLE row rather than per title. No assertion was weakened — it is still the
  proof that the Mody Patusa tab behaves exactly as the old panel did.

### Verification
- 995 tests; `--selftest` exits 0; `inspect_frame.py` 0 problems at 1280×760,
  1920×1200, 2560×1440 and 3840×2160.
- Every tab rendered at 1920×1200 and 1280×760 and inspected; the game rendered
  at 1920×1200 with a card hovered; the notice and the notepad rendered and
  inspected; the right column re-rendered after the heading fix.
- The heading regression test was run against the un-fixed code first and fails
  at three of five resolutions, so it is known to catch what it describes.

### Notes
- The reset button resetting one tab rather than all four is a judgement call.
  If the owner wants "put everything back", it is one line — but a single button
  that can undo four tabs of work without asking is the kind of thing that only
  gets noticed the once.
- `ui_scale`'s reference height is now the one number that moves the whole
  interface. If any future screen looks cramped at one resolution and fine at
  another, that constant is the first place to look and the last place to
  special-case.

---

## Stage 27 — The Chest starts doing things
**Date:** 2026-08-07

### Goal
A new Chest deck composition and the first four Chest cards with real effects:
Dzieckorolka, Rage Quit, Balbinka and Gambit Patusa. No change to victory
conditions, to checking, or to any movement card's own rules — the movement
system is REUSED throughout rather than extended.

### 0. A note on the tree this stage started from
The archive handed over for this stage extracted with a `data/cards.json` that
already contained most of this brief — the new texts, the new counts and four
`effect` blocks naming handlers that did not exist. The suite was therefore
**990 passing and 5 failing** before a line was written, and all five failures
were "this effect type has no handler".

That file was set aside, the tree was re-extracted and checked byte-identical to
the archive, and every change below was made from the pristine copy. Worth
recording because the failures looked like a broken baseline and were not: they
were a half-applied change. **If a suite fails on a tree nobody has touched yet,
diff the tree against the archive before debugging the code.**

### 1. The deck composition
Dzieckorolka 2, Rage Quit 2, Balbinka 2, Nie masz Rosji 2, Gambit Patusa 3,
Shady 2, Gejtos 3, Gamechanger 1 — **seventeen cards**, up from sixteen.

The change is not only the total. Stage 23 reached 16 by DOUBLING EVERY TITLE
UNIFORMLY, specifically so that fixing the supply did not move the odds (N97).
This composition is deliberately uneven, so the odds are now a balance decision
in their own right and the supply guarantee no longer follows from every title
having the same count. Both halves are asserted separately:
`test_the_new_counts_do_not_shrink_the_working_deck` for the size stage 23
measured, and `test_every_chest_title_can_still_be_dealt` for the thing an
uneven deck actually risks — **Gamechanger is down to one copy**, and a title
that cannot reach the table looks exactly like a title that is merely rare.

These are also the LOBBY defaults, and that needed no code: the settings panel
seeds every tab from `card.count` in the loaded data. There is a test asserting
the chest tab shows these eight numbers, which is what would fail the first time
somebody writes a list of chest titles into a screen.

### 2. Dzieckorolka — the card that made widened rows a decision
`move_and_collect` + the `MoveAndCollect` operation.

Three fields forward, sweeping the TOP pawn off every field walked through. Two
things are worth reading twice.

**Every widened position on the route is a question.** Every other card in the
game settles an intermediate 12a/12b by taking the nearer half, because nothing
depends on where a pawn merely passed (D8a). Here everything depends on it —
which half is walked decides which pawn is swept — so each one is asked
separately, keyed `branch0`, `branch1`, `branch2` by STEP rather than by
position, so no answer can overwrite another. The destination is asked the same
way as the intermediates rather than through the usual `tile` key, because from
the player's side they are the same question.

**The order of the finished tower is the rule.** A tile's `stack` is stored
bottom-first, and the tower read DOWNWARDS from the mover has to be the journey
in order. So, onto whoever was already on the destination: the collected pawns
in REVERSE travel order, then the mover, then its own riders. The brief's
example — green through blue-with-pink-on-top, then red, landing on yellow —
comes out `[yellow, red, pink, green]` bottom-first, and there is a test that
spells exactly that.

This is not decoration. **The hunters win by checking the pawn at the BOTTOM of
a tower**, so two machines with the same pawns in a different order disagree
about who wins the game, which is why the multiplayer test asserts the whole
board and not just the positions.

Collected pawns go UNDERNEATH the mover; riders stay above it. Two different
relationships to the same pawn, and getting them the same way round would put a
passenger below the pawn carrying it.

A field whose top pawn is FROZEN yields nothing rather than the sweep reaching
past it: "always take the top pawn" is the rule, and digging underneath one
would take a tower apart.

### 3. Rage Quit
`replace_mods` + the `ReplaceMods` operation. Thunderfuck's neighbour and
deliberately not Thunderfuck's operation: that one PUSHES from the left and lets
the rack shift, which is right for "draw a new mod" and wrong for "replace the
ones in play". Each occupied slot is written IN PLACE, the way the faction
selection does (N85), so Piotrek's slot stays Piotrek's.

**The draws happen before the discards.** A deck whose draw pile has run dry
reshuffles its discard pile, so returning the outgoing mods first lets the card
hand back the very cards it was played to get rid of. There is a test that
empties the mods deck to nothing and checks the rack is left alone rather than
refilled with the outgoing pair.

An empty rack does nothing at all and says so (N86, N99): the card exchanges
what is ACTIVE, and seeding the rack with a mod nobody chose is what N86 exists
to prevent. An empty SLOT stays empty for the same reason.

### 4. Balbinka
`move_all_pawns`, built out of `MoveBySteps` with two new flags.

**Nobody is carried.** The tower rule would move a rider twice — once inside its
tower and once in its own right — and the card says two fields, not four. So
`MoveBySteps.carry_riders` exists, defaulting to True so Plagiat! is untouched.

That makes the ORDER load-bearing, and it is not arbitrary: going forward the
furthest pawn moves first, going backward the rearmost does, so a pawn never
lands on a field whose occupant has not yet left. Within one field the bottom
moves first, so a tower arrives in the order it left. Only pawns already sharing
a field can converge — two pawns a field apart stay a field apart when both move
the same distance — so those two rules are the whole of it, except at the finish
and the start where movement clamps and the pawn behind ends up on top.

**The random half is rolled by the executor**, never the handler (N78):
`MoveBySteps.random_branch` → `GameState._random_half`, from `state.rng`. A die
rolled in a handler would be re-rolled by every preview frame while the card was
being dragged. Only the DESTINATION is randomised; intermediate halves keep the
nearer-half rule and consume no randomness at all, which also keeps the RNG in
step across a table where nobody is crossing a widened row.

### 5. Gambit Patusa
`reverse_movement` + `StatusKind.MOVEMENT_REVERSED`.

**A round is not a number of turns.** Piotrek takes every third slot, so rounds
differ in length, and `Status.expires_after_turn` cannot express "next round,
for one round". The status therefore carries a round NUMBER in its payload and
`StatusTracker.movement_reversed_in(round)` compares it. `_begin_round` retires
the ones already behind, and `_set_round` walks every round it crosses, so a
jump cannot skip the retirement either.

Granted with `stack=True`. Replacing would let a Gambit played DURING a reversed
round cancel the reversal it was played under — they are separate promises about
separate rounds.

Applied in `_move_pawn` AND `_move_pawns` (N104), gated on
`ctx.from_movement_card` (N103) so it leaves abilities and the Chest alone —
including Dzieckorolka, which is tested.

**Speedrun now asks about the EFFECTIVE direction.** A backward card under a
Gambit is already travelling forwards, so offering to turn it round would be
offering to undo the Gambit while describing it as undoing the card. The order
is: Gambit (silent), then Speedrun's question, then the pawn, then the widened
row. The sign is settled as distance → cap → flip → Speedrun's `abs()`.

### 6. A bug the multiplayer tests found: one Shady replacing another
`_sync_mod_states` asked *"does anything still hide?"* rather than *"did THIS
mod leave?"*. Rage Quit can replace a Shady with a Shady — there are two in the
deck — and when it did, the first pawn stayed off the map **for the rest of the
match** while the new arrival took a second one. A stranded pawn cannot be
moved, checked or won with.

The `HIDDEN` status now records `mod_uid`, and `_restore_pawns_hidden_by`
returns the pawns of departed mods BEFORE the arming loop, so a replacement
Shady picks its target from a complete board. The old blanket restore stays as
the catch-all.

**Pre-existing, not introduced here** — Thunderfuck could reach it too — and it
is N107 with the wrong question asked. Recorded because the next mod that holds
something will have the same shape: *a departure is about the card that left,
not about whether anything like it remains.*

### 7. What did NOT change
No new commands, no new message types, no new snapshot fields. All four cards
are ordinary `PlayCard`s whose decisions ride in `choices`, which is why none of
them can desync and why reconnection needed nothing: the reversal is a Status
and was already in the snapshot and already restored.

Victory conditions, checking, the turn cadence, the chest rota and the mod
selection are untouched.

### Tests
**1044 passing** (990 before, +54), ~165 s. The five pre-existing failures were
the half-applied `cards.json` described above and are gone.

- `tests/test_chest_effects.py` (new, 39): the composition against the data
  file; the lobby defaults; the deck still supplying a table and every title
  still reachable; Dzieckorolka's pawn question, the brief's own tower example,
  one-per-field and top-only, the destination left alone, riders above and
  collected below, a question per widened row with distinct keys, collecting
  only from the half it was sent down, the frozen top pawn, and immunity to Masa
  solna and Halloween; Rage Quit replacing both slots, never handing back what
  it discarded, doing nothing to an empty rack, leaving an empty slot empty, and
  running both the departure and arrival halves; Balbinka's direction question,
  every pawn moving two either way, a tower moving two and keeping its order,
  never landing on a pawn that has not moved yet, the silent random half, two
  machines agreeing from one seed, and a frozen pawn skipped; Gambit not firing
  in its own round, firing in the next, lapsing by itself, keeping the distance,
  sparing chest cards, reaching `_move_pawns`, stacking rather than cancelling,
  both Speedrun interactions, and being in the snapshot.
- `tests/test_chest_effects_sync.py` (new, 10): every machine building the same
  tower; no command of its own; an unanswered question logged nowhere (N40); the
  branch questions travelling as `choices` with one resubmission each; the
  random halves identical everywhere; Balbinka moving the whole table on every
  replica; both racks holding the same two mods after Rage Quit; Shady's pawn
  coming back everywhere; and the reversal replicating and naming the same round
  on every machine.
- `tests/test_settings_panel.py`: one test added for the chest tab's numbers.
- Two existing tests EDITED, neither weakened. `test_engine`'s deck-size mirror
  and `test_chest_cadence`'s leak accounting moved 16 → 17 (they mirror the data
  by design). `test_card_effects`'s forced-play test took the top chest card and
  asserted it had no effect; it now SELECTS a card with no effect, because the
  rule under test is about an unimplemented effect and picking one off the pile
  quietly stops testing it as the deck fills in (N94 in miniature).

### Verification
- 1044 tests; `--selftest` exits 0; `inspect_frame.py` **0 problems** at
  1280×760, 1920×1200, 2560×1440 and 3840×2160.
- The Chest tab of the settings panel rendered at all four sizes: eight rows,
  no scrolling needed, total 17, every stepper and both buttons inside the
  panel.

### Notes
- The destination field of a Dzieckorolka move is NOT swept, and it makes no
  observable difference: a collected destination pawn would be re-inserted
  exactly where it already was. The reading that matches "po drodze" was taken
  because it is the one that stays true if the insertion rule ever changes.
- Dzieckorolka does not consume ChatGPT's movement bonus. The collection is
  written against a route of exactly the printed length, and a card whose
  distance a skill could stretch would sweep a field its own text never
  promised. See L20.

---

## Stage 28 — The Chest finished
**Date:** 2026-08-07

### Goal
The remaining Chest cards: Gejtos, Alter Ego (Kingmaker stays a placeholder),
and — the smallest change here and the one a player notices first — every Chest
card playable whether or not its rule exists yet.

### 0. The tree this stage started from
No new archive arrived; `/mnt/user-data/uploads/` still held the stage 26 zip.
Built on the stage 27 output instead, after checking it byte-for-byte against
the working tree and re-running the suite to re-establish the 1045 baseline.

### 1. Every Chest card is playable
`Card.is_playable` reads `definition.effect is not None`. A card waiting on a
ruling had no `effect` block at all, so it **could not be clicked** — it sat in
the hand, and a player holding two of them was holding two dead cards against
the chest limit. That is the bug; the fix is one effect type.

`manual` shows the card, resolves to NOTHING, discards it normally and puts the
printed text in the status bar so the table can settle it by hand. Applied to
Nie masz Rosji, chest-Shady, Kingmaker, and to the untransformed Gamechanger as
a fallback.

**It first used `Announce` and that was wrong.** `_op_announce` emits
`ActionRejected`, and `_play_card` treats a rejection as "the play did not
happen" — so the card stayed in the hand, which is the exact bug being fixed.
`Fizzle` is the idiom for *played, did nothing, said so* (N99), and it is the
one to reach for. The test caught this, not review.

This is NOT a stub in the sense N10 forbids: nothing pretends the rule was
applied, and replacing `manual` with a real handler later is a JSON edit plus a
function.

### 2. Gejtos
One `gejtos` handler for both halves, because they are mirror images and only
differ in where the neighbours end up.

**The option is asked FIRST.** Kobieta needs a destination field beyond each
neighbour and Mężczyzna does not, so settling the option after the pawn would
change what the widened-row questions mean halfway through a resubmission.

New `TransferStack` operation, deliberately NOT built out of the movement
system: the pawns are not walking a route, they are picked up as a block and put
down. No distance, no direction, no widened row on the way, nothing for a Mod
Patusa to shorten. The tower keeps its order and lands on top of whatever is
already there — the arriving block is simply several pawns deep.

**Kobieta REFUSES rather than clamping** when pawns would be pushed before field
one. Every other backward move in the game clamps, and that is right for a card
that says "move back" — arriving at the start is a legal outcome. Here the card
names the case, and a card that silently did three quarters of its rule would be
worse than one that would not be played.

The anchor's own field is not a question. The pawn is STANDING on it, so which
half of a widened row it occupies is a fact. Only neighbours ask.

### 3. Alter Ego — the most delicate thing in the project
The hidden colour is the one fact the server has and the clients do not (N71,
N73). This card makes it change hands mid-match.

**The handler never touches the colour, and cannot.** It runs on every replica
to build the plan, and every replica but the authority's and Piotrek's own holds
`None` for the secret all match. A handler that read it would build a different
plan on different machines and split the table on the one card that must not
split it.

So the flow is:

1. `swap_identity` → `RequestIdentitySwap`, which raises a **colourless** public
   flag (`identity_swap = "revealing"`) and stops the table.
2. The AUTHORITY answers through `victory.review` — the same hook that decides
   an elimination, and for the same reason (N72) — returning `RevealIdentity`.
3. That command wipes the notepad down to the revealed colour, clears the
   secret, and moves the flag to `"choosing"`.
4. The room re-sends `identity_required` to Piotrek's peer alone, minus the
   colour he just gave up. **Same message, same overlay** — nothing new was
   built on either side.
5. `set_identity` accepts the second choice and issues `FinishIdentitySwap`,
   so every replica leaves the pause on the same command rather than guessing.

**The old crossings go.** They were evidence about an identity that no longer
exists, and the brief's example turns on it: Piotrek moves to a colour the
hunters had already ruled out, which is only possible because ruling it out is
void. What survives is the colour he just left.

`eliminated_pawns[-1]` IS the "which colour may he not pick" answer — there is
deliberately no second copy of it to fall out of step.

**Between the reveal and the choice there is NO hidden identity at all**,
including on the authority. `victory.review` reads exactly that and declines to
judge, which is what stops a tower being checked against nobody mid-swap. The
pause also goes through `_phase_refusal`, the one gate the opening pause already
used, rather than a second one.

Kingmaker keeps its presentation, title and description and does nothing. No
mechanic was invented.

### 4. What did NOT change
Movement logic, Mod behaviour, turn order, victory conditions, the chest rota.
Two new commands (`RevealIdentity`, `FinishIdentitySwap`), both AUTHORITY_ONLY;
one new snapshot field (`identity_swap`), public and colourless.

### Tests
**1084 passing** (1045 before, +39), ~175 s, stable across two consecutive full
runs.

- `tests/test_final_chest_cards.py` (new, 27): every Chest card playable; an
  undesigned card resolving, discarding and changing nothing; Kingmaker inert;
  Gejtos asking the option first, both halves, a neighbouring tower moved whole
  and in order, the refusal before field one, empty neighbours, widened-row
  questions and taking only the half pointed at, and immunity to the movement
  mods; Alter Ego refused to a hunter, naming no colour, the authority
  publishing it, a replica deciding nothing, the notepad reset, nobody knowing
  the colour mid-swap, the table stopped, the revealed colour unpickable,
  victory and checking following the NEW colour, a second swap refused, the
  snapshot flag, and a pending Squid Game check dropped.
- `tests/test_final_chest_cards_sync.py` (new, 10): Gejtos identical everywhere
  and needing no command of its own; and for Alter Ego — the colour in neither
  the played command nor the log, the reveal reaching everybody as a command,
  only Piotrek asked and the old colour not offered, the NEW colour never
  travelling, the table resuming together, the pause holding against a client,
  the fingerprint not moving, and a reconnecting player replaying the swap
  without learning either secret.
- `tests/test_ui.py`: two added for the reopened overlay.
- Two existing tests UPDATED, neither weakened. `test_ui` and `test_card_effects`
  both selected a card with `effect is None` to demonstrate "no implementation" —
  a category that no longer exists, which was the GOAL. Both now select a
  `manual` card; the behaviour asserted is unchanged.

### 5. A latent bug in stage 27's own test suite
The full run caught `test_the_branch_questions_travel_as_choices` failing while
passing in isolation. It assumed the first doubled position had doubled rows
along the next three fields — but the room's seed is not the test's to choose,
so "happened to" varied between runs. It now SEARCHES for a start whose route
crosses a widened row.

Recorded because of how it hid: it passed when written, passed in isolation
afterwards, and only failed once the suite grew enough to shift the order. **A
multiplayer test that reads the generated board must derive what it needs from
that board, never index into it.**

### Verification
- 1084 tests, twice; `--selftest` exits 0; `inspect_frame.py` **0 problems** at
  1280×760, 1920×1200, 2560×1440 and 3840×2160.

### Notes
- Kingmaker is the last placeholder in the Chest. Its rule — swapping roles
  mid-match — touches the hidden identity the way Alter Ego does, and the
  machinery Alter Ego just built is most of what it would need.

---

## Stage 29 — Responsive UI: three scales instead of one
**Date:** 2026-08-07

### Starting point
The report, from the project owner:

> The game looks great on my desktop monitor (2560×1440), but on my laptop
> (1920×1200) the interface becomes much harder to read, especially the
> movement cards at the bottom. The entire interface scales almost uniformly.
> I do **not** want to simply increase the rendering resolution again.

Stage 26 had already moved the scaling reference from 1080 to 1000 and lifted
the flat card ceilings. That helped and did not fix it, because it treated the
symptom: `ui_scale` was still ONE number multiplying everything, so the laptop
was the desktop at 83% and nothing could be traded against anything else.

### 1. The actual cause: card type was scaled TWICE
`CardRenderer._font` sized type as a fraction of the card's HEIGHT — and the
card's height already tracks `ui_scale`, because a bigger window makes a bigger
card. `FontBook.get` then multiplied the requested size by `ui_scale` again.

**Card type therefore moved with the interface scale SQUARED.**

| | 2560×1440 | 1920×1200 | ratio |
|---|---|---|---|
| hand card | 216×309 | 180×258 | 0.83 |
| card body type | 23.0 px | 15.6 px | **0.68** |
| type ÷ card width | 0.107 | 0.087 | **0.81** |

So the laptop did not merely get a smaller card. It got a *proportionally*
smaller font printed on it, which is why descriptions collapsed into stacks of
two-word lines. The same bug ran the other way at 4K: 0.138 of the card width,
type so large it crowded the description out. **One line caused both.**

`CardRenderer._font` now divides the font scale back out and anchors the ratio
to `CARD_TYPE_ANCHOR = 1.44` — the scale at the reference display — so the
arithmetic cancels exactly there and a card of a given pixel size renders
IDENTICALLY on every monitor. That invariant is worth keeping: it means a card
face can be reasoned about from its size alone.

### 2. One scale became three
`ui/layout.py`:

    ui_scale     general geometry.  Unchanged formula, unchanged meaning.
    type_scale   what FontBook is set to.  Decays MORE SLOWLY than ui_scale
                 below the reference display, so a caption keeps its absolute
                 size while the furniture around it gives ground.
    panel_scale  the side columns only.  Decays FASTER — a deck pile is the
                 first thing that can afford to be smaller.

Call sites did NOT have to change. They already write
`fonts.get(int(13 * layout.ui_scale))`, and FontBook multiplies again, so the
effective heading size is `13 · ui_scale · type_scale`. Setting FontBook to
`type_scale` moves the whole curve with one line in `App._apply_font_scale`.

**Text BANDS moved onto `type_scale` too** — `section_line_h`, `r_title_h`,
`r_name_h`, `r_identity_h`, `pk_title_h`. Stage 26's rule ("either the box
scales or the text is fitted to the box") applies one step further out now that
type has its own curve.

### 3. Breakpoints, and why the pixels still interpolate
`BREAKPOINTS` is a table of named tiers keyed on
`room = min(w/2560, h/1440)` — the axis that fell furthest behind the
reference. **Width is in there on purpose:** 1920×1200 has 83% of the
reference height but only 75% of its width, and it was the width that squeezed
the hand.

    wide     room ≥ 0.95   2560×1440 and up — the reference layout, untouched
    medium   room ≥ 0.66   1920×1200, 1920×1080 — the laptop band
    compact  room ≥ 0.00   1600×900 down to MIN_WINDOW

The NAMES are for decisions. The PIXELS interpolate continuously through
`compact` (0 at the reference, 1 at `TIGHT_WINDOW`), because a window being
dragged across a breakpoint must not make the board jump. Every adaptive number
goes through `Layout._lerp(roomy, tight)`, so "what changes on a smaller
screen" is one readable pair of numbers at each call site.

### 4. The brief's priority list, made into numbers
1. **Board** gives up height — it is the only region with height to give.
2. **Side columns** shrink: `panel_scale`, tighter width caps, and the
   panel-card ceiling falls from `hand_h × 0.78` to `× 0.62`. That last one
   matters: `hand_h` now GROWS its share on a small screen, so a fixed 0.78
   would have handed the columns the very pixels the hand had just won.
3. **Margins and gaps** shrink (`pad`, `section_gap`, `r_gap`).
4. **The hand shrinks last**, and in fact grows: its share runs 0.245 → 0.300.

### 5. `vertical_room` — adaptation has to be paid for out of something
`compact` is usually decided by the WIDTH, but giving the hand a bigger share
spends HEIGHT, and a 760-tall window has none: every pixel the shelf takes
there comes straight out of a side column already down to four stacked cards.
So both the hand's share increase and the type boost are tapered by
`vertical_room` (0 below 800px tall, 1 at 1200 and up).

This is not a fudge to make tests pass — it is the rule that makes the whole
scheme coherent. **At `MIN_WINDOW` the menu rows are already at `MIN_ROW_GAP`
and the panel bands at their floor; lifting type there does not make anything
more readable, it pushes captions out of boxes that cannot grow.** The smallest
window leans on `MIN_HAND_CARD_W` instead, which is what a floor is for.

`TYPE_BOOST_MAX = 1.18` bounds how much bigger type may get relative to the
furniture. Without a bound, boxes quoted in `ui_scale` burst.

### 6. Results

| | 2560×1440 | 1920×1200 before → after |
|---|---|---|
| hand card | 216×309 | 180×258 → **215×308** |
| card body type | 23.0 px | 15.6 px → **23.0 px** |
| card title type | 30.2 px | 20.4 px → 28.8 px |
| side columns | 26.9% of width | 30.2% → **28.1%** |
| board | 1800×800 | 1281×642 → 1328×618 |

**The reference display is byte-identical.** `pad`, `hand_h`,
`hand_card_size`, `left_w`, `right_w` and `board_viewport` all unchanged, and
`test_the_reference_display_is_untouched` writes the stage-28 numbers out
literally rather than recomputing them — a formula that is wrong the same way
in the test and in the code agrees with itself.

**The board keeps its area on the laptop.** It gives up the height the hand
needs and is repaid out of the side panels: 822k px² before, 821k after. Doing
all four priorities rather than only the one is what bought that.

### 7. Card titles are no longer broken mid-word
`Renderer.wrap_lines` breaks mid-word when a word cannot fit — right for a
paragraph, wrong for a title. "Thunderfuck" rendered as "Thunderfuc" over "k"
at EVERY resolution, the reference display included. `CardRenderer._title_font`
now shrinks the title until the longest word fits, down to three quarters of
its size, past which the mid-word break is allowed to happen again because an
unreadable whole word is not a win. Verified: zero mid-word breaks across every
card in every deck, at hand size and at hover-enlarged size.

### 8. A test that was passing because of the bug
`test_an_enlarged_card_is_redrawn_not_zoomed` demanded a 1.25 ratio of MEAN
edge energy between a redrawn card and a zoomed one. Measured under the OLD
code, at four card sizes:

    1920×1080   1.624  1.521  1.292  1.225
    2560×1440   1.089  1.199  1.176  1.049   ← the reference display

**It would have failed on the owner's own monitor.** It passed only because it
hardcoded 1920×1080 and (110, 157), where the double-scaling made the small
card's type about 8 px and zooming it 2.2× produced mush. The test was
measuring the bug, not the property it named.

The mean averages over the whole card, most of which is flat parchment, so a
blurry card and a sharp one differ by a few per cent. What enlargement destroys
is the PEAK: a glyph edge that steps 400 levels in one pixel steps half that
after a rotozoom. The test now measures 99th-percentile contrast, scores
1.96–2.28 everywhere, and is parametrized over four card sizes AND four window
sizes — **a stronger guard than before, not a weaker one.**

### 9. What was found and NOT fixed
**The character name overruns its ability card**: 7 px at 2560×1440, 24 px at
4K. Same double-scaling — `fonts.get(int(21 * ui_scale))` moves with
`ui_scale²` while the room under it moves with `ui_scale` — in the one place
that was not part of the report. Widening the band moves the reference
display's right-hand column, and this stage promised not to.

It is DIAGNOSED, and `test_the_character_name_does_not_run_into_its_ability_card`
pins it: no overhang at all at or below 1920×1200, and the known overhang may
not exceed 24 px. **A fix is a small stage of its own; the diagnosis above is
the whole of the work.**

### 10. One prior guarantee deliberately relaxed
`test_panel_cards_are_a_readable_size` asserted `panel_card ≥ hand_card × 0.55`.
That coupling was right while everything scaled together and is exactly wrong
now: it would stop the hand growing without dragging the decks up with it,
which is the coupling that made the laptop unreadable. The floor is ABSOLUTE
now (100 px, and 120 px for the right column's cards), and the 0.55 ratio is
still pinned at the reference display, where nothing moved. Every other prior
guarantee is untouched.

### Tests
- `tests/test_responsive_layout.py` (new, 98 with parametrization): the
  reference display untouched and nothing adapting above it; breakpoint
  assignment, ordering and reachability; no jump when dragged across a
  breakpoint; the laptop card within 5% of the desktop card's width; card type
  the same absolute size on both displays; card type keeping its share of the
  card at every resolution; the hand-card floor, aspect ratio and fitting its
  shelf; the columns giving ground; margins shrinking first; the board staying
  comfortable and keeping its area; type never decaying faster than the
  layout; bands measured against RENDERED type rather than against the formula
  that sizes them; the boost spent only where there is room; the two anchor
  constants agreeing across packages; no mid-word title breaks; columns still
  containing their contents; card-size monotonicity; and `resize` being
  idempotent.
- `tests/test_rendering_quality.py`: crispness rewritten (see §8);
  `test_type_is_rendered_larger_on_larger_displays` reads `type_scale`.
- `tests/test_ui.py`: `test_panel_cards_are_a_readable_size` (see §10),
  now also parametrized at 1600×900 and 1920×1200.

### Verification
- **1182 tests pass** (1084 before this stage).
- `run_game.py --selftest` exits 0.
- `tools/inspect_frame.py` reports **0 problems** at 1280×760, 1600×900,
  1920×1080, 1920×1200, 2560×1440 and 3840×2160, and with `--hover-hand`.
- Screenshots inspected at 2560×1440, 1920×1200 and 1280×760.

### Notes
- **Never quote card type against the interface scale.** The card's own size
  already carries it. This is the second time a flat-versus-scaled mismatch has
  cost a stage (26 had it with a label band); the first question to ask about
  any pixel number is which of the three scales it belongs to.
- `CARD_TYPE_ANCHOR` (render) and `TYPE_ANCHOR` (ui) must stay equal. They live
  in packages that must not import each other, so a test asserts it.

---

## Stage 30 — Signature Cards: optional full-card artwork
**Date:** 2026-08-11

### Starting point
The request, from the project owner:

> I want to introduce a second type of card presentation: cards with custom
> artwork. The important part is that this must be OPTIONAL. I want to
> gradually add artwork to individual cards over time. I do NOT want to
> redesign every card immediately.

Plus a workflow: put a file in a folder, and that card starts using it — no
rendering-code changes, ever. Two reference images of a Troll card were
supplied, one resting and one hovered.

### The shape of it
The whole feature is one branch on the first line of `CardRenderer.face`:

    Card ──> art.surface(definition)
               │
               ├── None ──────> the parchment face, unchanged since stage 29
               └── a Surface ─> _signature_face()

`face()` is where this belongs because it is the ONE entry point every card in
the game already goes through — the hand fan via `draw_transformed`, panels via
`draw_in`, the overlays, the RecentlyPlayed preview. Branching there meant
nothing else had to learn that artwork exists. **No file in `ui/` mentions it**
except one keyword argument in the fan.

### 1. The link is the filename
`render/card_art.py` scans `assets/card_art` once and folds both filenames and
card titles to the same key:

| File | Key | Card |
|---|---|---|
| `Troll.png` | `troll` | Troll |
| `rage-quit.PNG` | `rage_quit` | Rage Quit |
| `Stanczyk.png` | `stanczyk` | Stańczyk |

Case, spaces, punctuation and Polish diacritics all fold. `ł` is handled
explicitly — it is the one Polish letter Unicode will not decompose, and a
`unicodedata`-only implementation silently drops it.

**Titles are not unique across decks.** "Shady" is both a Mod Patusa and a
Chest card and they want different pictures; the brief did not anticipate this.
A file in a subfolder named after a deck (`mods/Shady.png`) is scoped to it and
tried first, and a bare name that two subfolders both claim is treated as
ambiguous and dropped rather than guessed at — first-match-wins would resolve
differently on different filesystems.

`CardDef.art` overrides the derived name. It has three states, and the third is
one this stage discovered it needed:

    None   derive from the title   (the default)
    "x"    use exactly that name
    ""     no artwork, do not look (the opt-out)

### 2. Why matching on the title is not N7
N7 forbids inferring card BEHAVIOUR from titles, because the prototype's badges
and effects broke the moment a card was renamed. Nothing here touches
behaviour: the worst a rename can do is un-match a picture, and an un-matched
card is a standard card — the same fallback a missing file gets. `"art"` pins
it where that matters. **This reasoning does not extend to anything the engine
reads.**

### 3. The reveal defaults from `highlighted`
`face(..., reveal=None)` → `1.0 if highlighted else 0.0`.

That default is doing real work. Every panel and overlay in the game already
passes `highlighted`, so all of them got both card states without a single
edit. Only `hand_fan`, which owns a smooth `slot.hover`, passes a float — which
is what makes the transition glide instead of snapping at a threshold. Reveal
is quantised to `REVEAL_STEPS` before it reaches the cache key, for the reason
`SIZE_STEP` exists one axis over.

### 4. The two states are one layout
The title, divider and description are laid out as ONE block anchored to the
bottom margin, and the resting title position is that same block with the
description removed:

    resting_top = h - bottom_pad - title_h
    opened_top  = h - bottom_pad - desc_h - 2*gap - title_h
    title_top   = lerp(resting_top, opened_top, reveal)

So the title rises by **exactly** the height of the description appearing under
it — at any card size, for any length of text, with no tuned offsets to be
wrong on a monitor nobody tested on. Same reasoning as stage 29's
`_lerp(roomy, tight)`.

### 5. Responsive: stage 29's invariant now covers the new face
Every number on a Signature face is a fraction of the CARD — title, body,
scrim heights, outline thickness — and all type goes through `_font`, so:

    A CARD OF A GIVEN PIXEL SIZE RENDERS IDENTICALLY ON EVERY MONITOR

still holds. `test_a_card_of_a_given_size_renders_identically_on_every_monitor`
pins it across six font scales (0.85 → 1.8) by comparing every fifth pixel
against the reference render.

Verified in a live game: hand card 216×309 at 2560×1440 and 215×308 at
1920×1200 — effectively identical, exactly as stage 29 intended.

**Long Polish rules text is cut, not spilled.** `_description_font` shrinks the
type and then TRUNCATES the lines that still do not fit. Found at 84×120, where
the font floor in `_font` takes over and shrinking stops helping: the
description ran off the bottom of the card. This is what `Renderer.draw_wrapped`
already does to a standard card's body.

The artwork is **cover-scaled** — scaled by the larger of the two ratios, with
the overflow cropped. Fitting would letterbox and show the parchment the
picture was supposed to replace; stretching would distort it.

### 6. A test failure that was worth having
`test_a_two_pawn_badge_is_wider_than_a_one_pawn_badge` failed: `142 < 142`.

Its helper builds a throwaway card out of `movement.cards[0]` — which is
**Troll**, now a Signature card, which draws no badge strip at all. The test was
right and the feature had a hole: there was no way to say "this card keeps its
parchment face". Hence the `""` opt-out, and the helper now strips `art` the
way it already stripped `image`. Nothing was deleted or relaxed.

### 7. Typography
Titles ask `FontBook` for a DISPLAY face and fall back to the bold UI face when
none is installed. Nothing proprietary is bundled: dropping
`assets/fonts/Display-Bold.ttf` in restyles every artwork card at once. The
supplied references are Hearthstone-framed; **the frame was not reproduced** —
it is Blizzard's, and the brief rules out copying their assets. `Troll.png` is
the inner illustration only, with the baked-in title cropped away so the game
can draw it.

### Files
- `render/card_art.py` — **new.** `slugify`, `CardArtLibrary`. Nothing in it
  raises; every failure path answers `None`.
- `render/card_renderer.py` — the branch, `_signature_face`, `_cover`,
  `_scrim`, `_outlined`, `_description_font`, `_lerp`; `reveal` threaded
  through `face` / `draw` / `draw_transformed`; `_font` and `_title_font` take
  a fraction and a display flag.
- `cards/base_card.py` — `CardDef.art`, `Card.art`, `from_dict` (accepts
  `false` as the opt-out).
- `config/settings.py` — `CARD_ART_DIR`, `CARD_ART_SUFFIXES`.
- `config/theme.py` — six `card_art_*` colours; `FontBook.get(display=)`.
- `ui/hand_fan.py` — one keyword argument.
- `assets/card_art/` — **new folder**, with `Troll.png` and its own README.
- `tests/test_card_art.py` — **new**, 61 tests.
- `tests/test_ui.py` — `_badge_card` strips `art` (see §6).
- Docs: `assets/card_art/README.md` (new), `assets/README.md`, `README.md`,
  `docs/ARCHITECTURE.md` §6a, `LLM_Instructions.txt` (new section
  "HOW SIGNATURE CARDS WORK", N36a–c).

### Verification
- **1243 tests pass** (1182 before this stage).
- The new tests come in matching pairs: half prove the Signature path works,
  half prove nothing else moved —
  `test_a_card_without_artwork_is_byte_identical_to_before`,
  `test_artwork_changes_no_gameplay_property`,
  `test_the_deck_composition_is_untouched`.
- Layout is measured against **synthetic flat-grey artwork**, not the shipped
  photo. The first draft scanned for pale pixels and kept finding the white
  sneaker in the Troll picture instead of the title; against a flat backdrop
  the only pale pixels are ones the renderer drew.
- Rendered and inspected: six card sizes × both states, and live game screens
  at 2560×1440 and 1920×1200 with Troll hovered in the hand.

### Notes
- **No gameplay file was touched.** No command, event, effect handler, status,
  deck count or turn rule changed. Troll is still locked, still kept out of
  opening hands, still unplayable by hand — a test pins all three against the
  same definition with its artwork stripped.
- `art` is **not** `image`. `image` is a small illustration inside the
  parchment body, addressed by path under `assets/images`; `art` replaces the
  face and is addressed by name under `assets/card_art`. Different folders,
  different fields, different pictures.
- The folder is *meant* to be incomplete. It fills up by hand over months, so
  "a missing file falls back silently" is a requirement, not a courtesy.


---

## Stage 31 — The old card overlay comes off
**Date:** 2026-08-11

### Starting point
The report, from the project owner:

> There is currently an old visual overlay present on cards. It appears on
> basically every card [...] a very thin brown rectangular border somewhere
> inside the card, a small green circle positioned at the top center of that
> inner border. [...] now that Signature Cards can use full-card artwork, this
> overlay is visually problematic.

Correct, and it had become a real defect rather than a taste question. On Troll
the pip landed exactly on the gem the artwork draws at its top edge.

### What it actually was — two unrelated things that looked like one
The owner described the pip as belonging to the inner border. It does not; they
come from different files and are drawn at different times. They only *look*
related because the pip is pinned ten pixels below the card's top edge and the
rule runs at `max(3, h*0.028)`, which at hand size puts the circle exactly on
the line.

| Element | Source | Was it functional? |
|---|---|---|
| inset brass rule | `card_renderer.face`, `_signature_face`, `overlays.py` reveal card | no — decoration |
| green circle | `hand_fan._playable_marker` (`theme.valid`) | display only |
| pale circle | `hand_fan._locked_marker` (`theme.prompt`) | display only |

The brief asked for this to be checked rather than assumed, so it was. **The
pips were display only.** Nothing hit-tests them, no gesture consults them and
no rule reads them; `draw()` was their only caller, and `playable` was a local
computed for their sake alone. Every question they answered is still answered,
and still by the engine:

* *may I play this?* — `Card.is_playable` still gates `_activate`, an
  unplayable card is still discarded rather than played, and a drag still
  colours the card's own border green / prompt / red from `effects.preview`;
* *is it locked?* — `GameState` still refuses `PlayCard` and `DiscardCard`, and
  clicking one still says so in the status bar.

So the removal took a redundant hint, not a mechanic. What is genuinely lost is
the *anticipatory* cue — the player now learns a card is locked by clicking it
rather than by looking at it. Recorded here deliberately; if it is wanted back
it belongs on the shelf or the border, never on the card face.

### Removed
- `card_renderer.face` — the inset rule. `inset` survives as the text margin it
  always doubled as.
- `card_renderer._signature_face` — the same rule, over the artwork.
- `ui/overlays.py` (`RevealOverlay`) — a third copy. This overlay paints its own
  card instead of calling `CardRenderer.face`, so leaving it would have made the
  reveal the only place in the game where the double frame survived.
- `ui/hand_fan.py` — `_playable_marker`, `_locked_marker`, their call sites, the
  `playable` local and the now-unused `lighten` import.
- `theme.card_frame` — retired. Nothing referenced it any more, and a colour
  named for a frame that frames nothing is an invitation to draw the line again.

**Kept:** the outer border (the card's silhouette, and the channel the drag
preview colours through `border_color`), the title/body divider and its brass
diamond (content, not overlay), the card back's own inset frame and emblem (a
different composition, and no artwork under it), and the badge strip.

### Verification
- **1221 pass, 26 fail — and the 26 are pre-existing.** The uploaded zip fails
  the identical set before any edit (1216 / 27). See "Not this stage" below.
- Three new tests in `tests/test_card_art.py`, each confirmed to FAIL against
  the pre-change code so they are not vacuous:
  `test_no_inner_rule_is_drawn_inside_a_standard_card`,
  `test_nothing_is_drawn_over_signature_artwork_but_the_border`,
  `test_the_hand_draws_no_status_pip_on_a_card`. The pip test recomputes the
  former pip centre from the live fan geometry rather than counting theme
  colours across the shelf — the artwork is a photograph and always contains a
  few pixels that match a theme colour by chance, so a count would be flaky.
- `test_removing_the_pips_left_the_answers_they_gave_intact` pins the rules the
  pips used to hint at.
- `inspect_frame.py`: 0 problems at 1280×760, 1920×1200, 2560×1440, 3840×2160,
  hovered and not. `run_game.py --selftest` exits 0.
- Hover reveal re-checked end to end: artwork darkens, title lifts, description
  appears, `slot.hover` reaches 1.0.

### Not this stage — two things found on the way in
1. **26 tests were already failing.** The Mod Patusa card `Shady` was renamed to
   `Obóz Harcerski` in `cards.json`; the mod tests look it up by title and raise
   `StopIteration`. The ENGINE is fine — mod rules are declared as `passive` and
   read through `GameState.mod_rule`, never by title — so this is test content
   lagging card content, not a broken game. Untouched here because it is card
   data and out of this brief's scope.
2. **`Shady.png` now illustrates the CHEST card, not the mod**, because the mod
   no longer has that title. If the picture was drawn for the mod it needs
   renaming to `Obóz Harcerski.png`.

`test_a_title_two_decks_share_is_scoped_by_folder` was one of the 26 and IS
fixed here, because it was mine and it was wrong: it used Shady as its example
of a title two decks share, so the rename deleted the collision and the test
with it. It now builds its two definitions instead of looking them up — a
mechanism has to stay tested whether or not today's content exercises it.

### Notes
- **Nothing goes on top of a card face** (new N36d). One border, the card's own
  content, nothing else. A pip, badge, counter or frame added there lands in
  the middle of somebody's illustration.
- The card face is the one surface in this game whose content the project does
  not control. Everything drawn over it is a bet that no artwork will ever want
  that spot, and Troll lost that bet at the first attempt.
