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

---

## Stage 32 — The Card Library

**Date:** 2026-08-11

### Goal
An in-game encyclopedia of every card, opened from a book in the bottom-right
corner, showing REAL game cards rather than a list of names — and, for the three
table decks, letting the table change how many of each there are without leaving
the match. Abilities get their own category with a different set of controls,
because an ability is not a deck.

### What it is
`ui/card_library.py` — one overlay, four tabs:

| Tab | Source | Controls under each card |
|---|---|---|
| Karty ruchu | `movement` deck definition | `n w talii`, `[-1] n [+1]` |
| Mody Patusa | `mods` deck definition | the same |
| Karty skrzyni | `chest` deck definition | the same |
| Umiejętności | both ability decks | owner's name ABOVE, `PRZYWRÓĆ UŻYCIA (n)`, `Ilość użyć`, `[-1] n [+1]` |

Every card is drawn by `CardRenderer.draw_in(..., reveal=hover)`. There is no
second renderer, no second card definition and no `if card.has_art` anywhere in
the file — stage 30 put that branch inside `face()` precisely so a new screen
would not need one. Signature cards therefore arrived working: artwork at rest,
artwork darkened with the description sliding up on hover, on the same float
curve the hand fan uses.

### The question the brief asked to be answered rather than invented
**What does a quantity MEAN once the match has started?** The lobby's `[-] n [+]`
sets how many copies are printed into a deck *before the shuffle*; from the first
deal onwards those copies are spread across a draw pile, a discard pile, several
hands, the mod rack and possibly an open Mod Patusa selection.

The library counts **all of them** — `GameState.deck_card_count` walks piles,
hands, rack and selection — so the number under a card is the number the lobby
configured and the two readings never disagree. Counting only the draw pile
would have made the library contradict the lobby the instant anybody drew.

**Nothing mirrors anything.** `config.movement_counts` and friends are what the
match was BUILT from and say nothing about what has happened since; the cards
themselves are the only honest answer. There is exactly one number and it is
computed from the cards on every frame.

### The safety rule for editing a live deck
```
+   a NEW Card, fresh deterministic uid, inserted at deck.rng.randrange(...)
-   the draw pile first, the discard pile second, and NOWHERE ELSE
```
A hand, the mod rack and an open selection are cards out on the table in front of
somebody. When every remaining copy is out there, `-` is REFUSED —
`„Zerówka - czerwony” są w grze` — rather than reaching into a hand. That is the
whole of "must not silently delete cards from players' hands".

Not on top of the pile, either: a card conjured onto the top of the deck is the
next card somebody draws, which is a way of handing a chosen card to the next
player. `deck.rng` has been advanced by exactly the same shuffles on every
machine, so the position is agreed on without being predictable.

### Default uses vs current uses
This distinction needed no new storage, because it already existed:

```
CardDef.uses    the configured default — ALREADY the lobby's number, because
                DeckDef.with_uses rewrote the definition before the deck was built
Card.uses_left  the runtime counter, which travels with the physical card
```

So `RestoreAbilityUses` is one assignment of one onto the other and keeps no
memory of its own. `AdjustAbilityUses` has a floor of zero and **no ceiling** —
the table may deliberately give an ability more charges than it was printed with,
and restoring afterwards returns it to the printed default, not to the inflated
number.

`GameState.ability_card(title)` finds the one physical copy wherever it is: on a
player (dealt character or Piotrek's skill) or still in its deck. Seats first,
then decks, in a fixed order, so every replica finds the same card.

### Multiplayer
Three new commands, and the notable thing about them is a field they do NOT have:

```
AdjustDeckCount(deck_id, title, delta)
AdjustAbilityUses(title, delta)
RestoreAbilityUses(title)
```

No `player_index`. They are therefore in neither `_OWNED_BY_PLAYER` nor
`_TURN_BOUND`, `authorise_remote` has no seat to compare against, and **any
player may restore any character's ability at any time** — which is the
requirement, and which written as an ordinary seat-owned command would have come
out restricted to the owner.

`delta` rather than an absolute value so two players clicking `+` at once add two
cards; the server applies both in order and neither is a stale absolute
overwriting the other's work.

The snapshot gained `deck_composition` and `ability_charges`. Both were needed:
the existing `decks` entry carries pile SIZES, which would not notice two
machines adding copies of different titles, and the existing `ability_uses`
covers only cards that have been dealt — the library can top up a character
nobody is playing.

### Layout: why the panel's width follows the grid
`card_library_panel` is measured from the cards, not taken as a share of the
window. A share gives a content area so much wider than it is tall that four or
five columns always fit and the cards end up sized by the leftovers. Sizing the
card against the viewport's HEIGHT (a card is tall, and a row has to leave room
for the controls under it) and wrapping the panel around three of them makes
"three big cards per row" a consequence rather than a number written down.

A display tall enough to push the card into `CARD_LIBRARY_MAX_H` has stopped
spending height on the card, and the width that buys is worth a fourth column:

| window | columns | card |
|---|---|---|
| 1280×760 | 3 | 217×310 |
| 1920×1080 | 3 | 318×454 |
| 1920×1200 | 3 | 352×502 |
| 2560×1440 | 3 | 441×630 |
| 3840×2160 | 4 | 448×640 |

### Stage 31 is intact
The count, the steppers, the owner's name and the restore button are all OUTSIDE
the card's rectangle. `test_nothing_is_drawn_over_a_card_face` asserts it for
every visible cell of every tab — no card rect collides with any control box —
so the next "just a small quantity badge in the corner" fails a test rather than
landing in the middle of somebody's illustration.

### Changed
- `engine/commands.py` — three commands + registry entries.
- `engine/events.py` — `DeckCountChanged`, `AbilityUsesChanged`.
- `engine/game_state.py` — `cards_of_deck`, `deck_card_count`,
  `deck_composition`, `_next_card_uid`, `_definition_of`, `_adjust_deck_count`,
  `_remove_one_copy`, `ability_card`, `ability_default_uses`,
  `_adjust_ability_uses`, `_restore_ability_uses`; `_HANDLERS`; two snapshot
  entries.
- `ui/layout.py` — the whole `card_library_*` family, including the book button.
- `ui/card_library.py` — NEW.
- `ui/game_screen.py` — construction, input routing (above the keyboard
  dispatch, so Esc closes the library rather than opening the pause menu), the
  book's click, `update`, `draw`, and refusals routed to the library while it is
  open.

### Two defects found by the verification, both fixed
1. **The book painted `panel_bg` inside the board viewport**, which is the colour
   that means "nothing has been drawn here" to
   `test_the_world_fills_the_viewport_even_when_zoomed_right_out`. Six sampled
   pixels failed it. The icon is now drawn entirely in the button's own text
   colour and a darkened copy of it.
2. **The footer hint printed over the last row of cards.** It was anchored to the
   close button's TOP edge; `Button.fit` sizes itself to its caption, so at
   2560×1440 that put the line fourteen pixels inside the content rectangle. It
   is anchored to the content's bottom edge now and the footer band budgets for
   both. This is N46 in yet another costume.

### Verification
- **1306 pass, 26 fail — and the 26 are the SAME 26 that failed before this
  stage.** Baseline on the supplied zip: 1221 / 26. Every one of them is the
  `Shady` → `Obóz Harcerski` rename in cards.json versus mod tests that still
  look the card up by title (`StopIteration`), recorded in stage 31 and still out
  of scope here. No regressions.
- 85 new tests: `tests/test_card_library.py` (76) and
  `tests/test_card_library_sync.py` (9).
- `tools/inspect_frame.py`: 0 problems at 1280×760, 1920×1080, 1920×1200,
  2560×1440 and 3840×2160. `run_game.py --selftest` exits 0.
- Clipping is checked in PIXELS, not in arithmetic:
  `test_the_grid_paints_nothing_outside_its_viewport` paints the frame twice —
  once whole, once with the grid suppressed — and requires every differing pixel
  to be inside the content rect, with the grid parked half a row out so the
  straddling row is actually exercised.
- Screenshots at 2560×1440 reviewed for all four tabs, hovered and not.

### Limitations, honestly
- **The library is refused before the match begins and after it ends.**
  `_phase_refusal` gates every non-authority command, and the library's three are
  not exempt. Consistent with the rest of the engine, but it means the book opens
  and its buttons do nothing while Piotrek is choosing a colour. Exempting them
  is a one-line change *if* the owner wants it — it was not invented here.
- **`-` can refuse for a reason the player cannot see.** When the last copies are
  in hands the refusal names the card but not who is holding it, because saying
  so would leak hidden information.
- **Removing a copy does not un-deal anything.** Cards already in hands stay
  there; the count therefore cannot be driven below the number currently in play.
- The scrollbar is drawn but not draggable — the wheel, the arrow keys and
  Home/End scroll. Same bargain the settings panel makes.
- The display cards are throwaway `Card` objects built from definitions, so they
  take uids from the global counter. They are never submitted in a command and
  never enter a deck; commands address cards by TITLE.

---

## Stage 33 — Four across, and "Dobierz kartę"

**Date:** 2026-08-11

Two changes to the Card Library, both asked for by the owner after using it.

### 1. Four cards per row instead of three

Stage 32 sized a library card at ~0.6 of the viewport's height, which is what
left room for exactly three. The owner wanted more of the collection on screen
at once, so **two numbers moved** and nothing else did:

```
card height share   _lerp(0.62, 0.55)  →  _lerp(0.46, 0.50)
preferred columns   3 (4 at the ceiling)  →  CARD_LIBRARY_COLUMNS = 4
CARD_LIBRARY_MAX_H  640 → 480
```

Everything else — the panel width, the cell, the scroll extent, the hit test —
is derived from those, which was the point of deriving it. Measured:

| window | columns | card | vs stage 32 | rows visible |
|---|---|---|---|---|
| 1280×760 | 4 | 184×262 | was 217×310 | 1.41 |
| 1920×1080 | 4 | 252×360 | was 318×454 | 1.43 |
| 1920×1200 | 4 | 277×395 | was 352×502 | 1.44 |
| 2560×1440 | 4 | 309×441 | was 441×630 | 1.51 |
| 3840×2160 | 4 | 336×480 | was 448×640 | 2.23 |

Note the height share runs **backwards** — a small window spends a *bigger*
share (0.50) than a large one (0.46). That looks like a mistake and is not: at
1280×760 the flat share left a 154px card, *smaller than the one in the
player's own hand*, which is the thumbnail gallery the grid is explicitly not.
A big window can afford to spend its height on more rows instead.

`card_library_columns` still **measures** whether four fit rather than
asserting it, so a genuinely narrow window gets three or two instead of four
cards on top of each other.

### 2. "Dobierz kartę" — fetch one named card into your hand

A button under every Movement / Mod / Chest card (never under an ability) that
takes **that** card out of **that** deck and puts it in the clicking player's
hand. It exists so a tester can get a particular card in front of them without
drawing thirty others first.

```
DrawTitledCard(player_index, deck_id, title)
```

**What was reused rather than rebuilt:**

* `Deck.take_titled(title)` already existed for `setup`'s character picks — it
  gained an `include_discard` flag and nothing else.
* `_draw_one`'s tail was extracted into `_deliver_card(player, deck, card)`,
  and **`_draw_card` and `_draw_one` now both go through it**. A card that
  arrives by name owes exactly the same debts as one off the top: it is
  reported as `CardDrawn`, and it *acts on the way in* via `_after_draw`. Troll
  still draws its replacement when fetched by name, which is a test.
* Authority is the existing `_OWNED_BY_PLAYER` check — the command carries a
  `player_index` (unlike the library's other three) precisely because this one
  *is* about a particular player. It is deliberately **not** `_TURN_BOUND`:
  fetching yourself a card to try is not a move.

**Scoping.** Card identity here is (deck_id, title). Only the deck named by the
tab is searched, so a title printed in two decks could never be fetched from the
wrong one. There are no such titles today; the test asserts the scoping anyway.

**The discard pile counts.** `take_titled(include_discard=True)` looks in the
draw pile first and the discard pile second, because `take_card` already
reshuffles the discard back in the moment the draw pile runs dry — refusing a
card that is one shuffle from being drawn anyway would be a lie told by an
off-by-one pile. `setup` keeps the old draw-pile-only behaviour via the default.

**Nothing is ever fabricated.** No copy in the deck means `ActionRejected` and
an untouched hand.

### Feedback, in the one place the player can see it

The library sits *on top of* the hand, so both outcomes are reported in its
footer, which stage 32 already built for refusals:

* failure → `Brak karty „…” w talii`, in `theme.warning` (red)
* success → `Dodano kartę do ręki`, in `theme.valid` (green)

`notice_colour(theme)` is a method so the red/green rule is one thing a test can
ask a question of. The confirmation deliberately **does not name the card**: a
drawn card acts on the way in, and Troll's action is to draw a replacement, so
the last `CardDrawn` of the chain is some other card entirely and naming it
would confidently report the wrong one. It also stays quiet when the library is
shut — every other draw announces itself by appearing in the fan.

### Hover now follows the CELL, not the card

Stage 32 keyed the reveal to the card rectangle, which was right when nothing
sat under it. With a button there, reaching for it dropped the reveal on the way
down and the description flickered shut just as the player went to act on it. A
cell is one entry, so the reveal now holds still while you work with it.

### Changed
- `engine/commands.py` — `DrawTitledCard` + registry.
- `engine/game_state.py` — `_deliver_card`, `_draw_titled_card`, `_draw_one`
  and `_draw_card` routed through the shared arrival, `_OWNED_BY_PLAYER`.
- `cards/deck.py` — `take_titled(include_discard=False)`.
- `ui/layout.py` — the three retuned numbers, `CARD_LIBRARY_COLUMNS`, and the
  draw button's height in `card_library_cell_size`.
- `ui/card_library.py` — the `draw` box, its click, `_draw_action` (now shared
  with the restore button), `seat` callable, `notice_ok`/`notice_colour`,
  cell-based hover.
- `ui/game_screen.py` — `seat=` for the library, `_on_card_drawn`.
- `assets/card_art/` — see below.

### An asset that arrived broken
`Stańczyk.png` was in this task's ZIP as `Sta#U0144czyk.png` — the "ń" replaced
by a literal ASCII escape somewhere in the packaging round-trip. `slugify` was
working correctly; the file was genuinely misnamed, so Stańczyk silently lost
its artwork and rendered as a plain card. **Renamed back.** Deliberately NOT
fixed in code: teaching `slugify` to decode `#Uxxxx` would bake a packaging
accident into the product. Worth knowing if art vanishes again after a zip trip.

### Verification
- **1351 pass, 26 fail — the same 26 as before this stage** (baseline on this
  task's ZIP was 1306/26). All 26 are the `Shady` → `Obóz Harcerski` rename in
  cards.json versus mod tests still looking it up by the old title. No
  regressions; all 85 stage-32 library tests still pass untouched.
- 45 new tests: 30 in `test_card_library.py`, 6 in `test_card_library_sync.py`,
  plus the retuned column assertions.
- Geometry swept at all five reference resolutions × 4 tabs × 3 scroll
  positions: no cell overlap, no box outside its cell or the viewport, **no box
  touching a card face**, no two draw buttons touching, steppers inside their
  bands. `inspect_frame.py` 0 problems; `--selftest` exits 0.
- Screenshots at 2560×1440 and 1280×760 reviewed.

### Limitations
- Inherited from stage 32: the library's commands are refused before the match
  begins and after it ends (`_phase_refusal`), and `DrawTitledCard` is no
  exception.
- A fetched card counts against the hand limit and is refused when the hand is
  full — it is a shortcut, not a cheat.
- The fetched card is drawn for the seat **on screen** (`view_seat`), matching
  the hand fan. In a hot-seat game that is whoever is being viewed; in a network
  match `_OWNED_BY_PLAYER` means it can only ever be your own seat.
- 'Dobierz kartę' does not change the library's quantity, and should not: the
  count is copies *in the match*, and a card in a hand is still in the match.

---

## Stage 34 — Card variants, and "Sesja na PG" gains a second one
**Date:** 2026-08-11

### Goal
A reusable way to give a card two or more predefined readings that differ in
what they SAY and what they DO, without splitting it into two cards — then use
it for both versions of the Mod Patusa **Sesja na PG**. `AKO` and `Nie masz
Rosji` are next and must need no new mechanism.

### The shape of it: a variant is configuration, not a card

    cards.json          Sesja na PG  ─┬─ lock              (the printed card)
                                      └─ lock_and_cancel

    this match          card_variants[("mods", "Sesja na PG")] = "lock_and_cancel"
    another match       ...= "lock"          ← at the same moment, unaffected

`CardVariant` may replace exactly three things — `text`, `passive` and
`effect`. The title, the artwork, the count and the deck id are the card's
IDENTITY and no variant can reach them; that is the whole difference between
this and printing two cards. Anything a variant leaves out is inherited, so the
smallest useful variant is an id and one key.

`CardDef.with_variant` always resolves against `CardDef.printed` (kept in
`base`). Without that, going 1 → 2 → 1 would leave variant 2's sentence on the
card wherever variant 1 declares none — a bug the tests now pin.

### Where the choice lives, and how it travels
- `SessionConfig.card_variants` and `LobbyState.card_variants`, title → id.
  **EMPTY MEANS AS PRINTED** — the fifth member of the `mod_counts` family and
  it behaves exactly like them, so an older client, or any of the 1300 tests
  that pass no mapping, gets each card's FIRST variant: the card that shipped.
- `DeckDef.with_variants`, applied in `setup.build_decks` BEFORE the shuffle
  beside `with_counts`/`with_uses`. It cannot change a `count`, so the pile is
  the same permutation either way — pinned by a test, because two machines
  building different piles from one seed is what every deck setting must avoid.
- `server/room.set_settings` merges it through the same loop the other three
  mappings use. Host-only, for free.
- `GameState.card_variants` is seeded from the config and is **in the
  snapshot**, so it is in the fingerprint: a table that agrees about every
  count and every uid and disagrees about what one card DOES is exactly the
  desync nothing else in that dictionary would notice.

### Changing it mid-match
`SetCardVariant(deck_id, title, variant)` — no `player_index`, so like the
library's other bookkeeping commands it is in neither `_OWNED_BY_PLAYER` nor
`_TURN_BOUND` and any seat may issue it. An ABSOLUTE id, not "next variant":
two players cycling at once would otherwise land somewhere neither chose.

Applying it rewrites `card.definition` on every copy of that title in the match
(`_reread_copies`), which is what keeps the deck's two physical copies ONE
logical card. `_adjust_deck_count` was also changed to build a new copy from
`variant_definition` — a copy added from the library on the printed variant
while the rest played another was the same failure arriving later.

### How an ability effect is identified — the one genuinely new idea
`Status` gained **`origin`** (`"ability"` / `"card"` / `"on_draw"`), stamped in
ONE place: `effects.resolve_spec`, from the `EffectContext.origin` that already
existed and already reached every handler. So the thirteenth effect gets it
without knowing it exists.

`source` is a NAME and is for humans ("Granny Costume"); `origin` is the
machine's answer to "is this an ability effect?", which no amount of
string-matching on the name could give without a table of every ability in the
game. Selecting by kind would have been wrong: a frozen pawn may be Granny
Costume's or a Chest card's and the two are identical from outside.

### Sesja na PG
    variant 1  "lock"             abilities_locked                    (unchanged)
    variant 2  "lock_and_cancel"  abilities_locked
                                  + cancel_ability_effects

Variant 2's cancellation runs from `_arm_mod`, keyed on the declared `passive`
and never on the title (N98) — so it is a TRANSITION, not a per-frame sweep.
`_cancel_ability_effects` drops `statuses.cancel_origin("ability")` and nothing
else: Mods, Chest promises, movement statuses and engine-attached statuses (a
hidden pawn) all stay.

Changing the variant of a card **already in the rack** asks whether the RACK's
answer changed (`cancels_ability_effects`, an ordinary `mod_rule`) and fires the
cancellation if it did, so the card does not have to be played again.

**There is deliberately no departure half.** A cancelled effect does not come
back when the mod leaves — it was cancelled, not suspended — and the ability
lock is a passive that lifts by itself. Going 2 → 1 restores nothing either.

### The two interfaces
- **Lobby:** a fifth tab, "Warianty", in the shared `GameSettingsPanel` — so
  both setup screens get it at once, as stage 26 intended. Only cards that
  declare two or more variants appear; the rows are gathered from EVERY deck,
  so `AKO` and `Nie masz Rosji` will show up with no code change. `SettingsTab`
  grew `options`, which turns its number into an INDEX into named choices and
  let bump/clamp/reset/merge be reused whole. Each row shows the variant's own
  DESCRIPTION under the title, because reading the difference is the point.
- **Card Library:** a button under 'Dobierz kartę' saying `WARIANT 2 (2/2)`.
  UNDER the card, never on it (N36d / stage 31). The row's height is a property
  of the TAB, not the card — a grid whose cells were each as tall as their own
  contents would put every column out of line — so the mods tab reserves the
  room for all its cells and only the cards with variants draw in it. The
  display cards follow the live state through `_refresh_variants`, so the face,
  and therefore the hover description, is always the variant in force.

### Changed
- `cards/base_card.py` — `CardVariant`, `CardDef.variants/variant/base` and
  `has_variants`/`variant_ids`/`default_variant`/`printed`/`with_variant`,
  `Card.variant`/`has_variants`, `DeckDef.with_variants`.
- `data/cards.json` — Sesja na PG's two variants.
- `config/settings.py` — `SessionConfig.card_variants`, `clean_card_variants`.
- `net/lobby.py`, `server/room.py` — the field on the wire and its merge.
- `engine/setup.py` — variants applied in `build_decks`.
- `engine/statuses.py` — `Status.origin`, `of_origin`, `cancel_origin`.
- `engine/effects.py` — `_stamp_origin` in `resolve_spec`.
- `engine/commands.py`, `engine/events.py` — `SetCardVariant`,
  `CardVariantChanged`.
- `engine/game_state.py` — `card_variants`, `card_variant`,
  `variant_definition`, `cancels_ability_effects`, `_cancel_ability_effects`,
  `_set_card_variant`, `_reread_copies`, `_arm_mod`, `_adjust_deck_count`,
  snapshot.
- `ui/settings_panel.py`, `ui/layout.py`, `ui/card_library.py`,
  `ui/game_screen.py`, `ui/menu.py`, `ui/network_screens.py`.

### Verification
- **1410 pass, 30 fail — the same 30 as the baseline on this task's ZIP**
  (1347/30 before the stage). No regressions and nothing fixed by accident; the
  30 are the pre-existing `Shady` → `Obóz Harcerski` rename in cards.json
  versus mod tests still looking the card up by its old title, plus a health-page
  and a real-socket test that already failed here.
- 63 new tests: 54 in `test_card_variants.py`, 9 in
  `test_card_variants_sync.py`. Two existing assertions in
  `test_settings_panel.py` updated for the fifth tab.
- Covered: the variant definitions (identity, artwork, wording, inheritance,
  round-trip); variant 1 locking and LEAVING a running effect alone; variant 2
  locking, cancelling on arrival, and cancelling ONLY ability-originated
  effects with three other statuses in play; 1 → 2 mid-match cancelling; 2 → 1
  restoring nothing; the lock lifting while the cancellation stands; two copies
  staying one card; unknown ids refused; the command's JSON round trip and its
  lack of a seat; lobby → clients; a mid-match change reaching every machine
  and surviving four turns; the fingerprint; the library's control geometry at
  all five reference resolutions (never touching a card face), its click, its
  caption, and the other library controls still working under it.
- NOT verified: no screenshots were reviewed and `inspect_frame.py` /
  `--selftest` were not run this stage.

### Limitations
- `SessionConfig.card_variants` is keyed by TITLE while `GameState` keys by
  (deck, title). One title with variants in two decks would therefore take the
  same variant in both. No title is in two decks today; the day one is, the
  config key is the thing to widen.
- The library's variant button inherits stage 32's gate: like every other
  library command it is refused before the match begins and after it ends.
- The extra row makes the Mody Patusa tab scroll one row sooner. A control
  scrolled out of sight is out of reach, which is stage 32's intended
  behaviour, not a new limitation — but tests that click it must scroll first.
- A variant may not change a card's artwork. That is the requirement today
  (`Sesja na PG` keeps one picture); allowing it would mean letting a variant
  reach `art`, which is deliberately one of the identity fields it cannot.

---

## Stage 35 — AKO, and the pawn that comes along
**Date:** 2026-08-11

### Goal
Give the last placeholder Mod Patusa its rules, in both of the versions the
owner wrote, on top of stage 34's variant system and with no new mechanism.

    variant 1  "with_stack"  Wszystkie ruchy poruszają jednego sąsiadującego pionka
    variant 2  "alone"       Wszystkie ruchy poruszają TYLKO jednego sąsiadującego pionka

### Where it sits in the movement pipeline
LAST, after the mover's own move is completely settled. `_move_pawn` asks its
questions in the order the rules fix — Gambit Patusa, Speedrun's direction,
which pawn, which half of a widened row — and AKO is appended after all of
them, because the passenger is a detail of a move that has already been
decided. The mover's operation goes into the plan first, so the companion walks
into a field the mover has already left.

    Gambit → Speedrun → pawn → distance (Masa solna, movement bonus)
                                    → widened half → AKO's neighbour

Because it joins after the distance is settled, it inherits every other
modifier for free: Masa solna shortens the companion's move too, a ChatGPT
bonus stretches it, Gambit and Speedrun decide its direction. Both are pinned
by tests.

`ako_companion()` is shared by `_move_pawn` AND `_move_pawns`, so Plagiat!
brings a neighbour for each of its moves — keyed `pawns_ako0`, `pawns_ako1`, and
judged against `MoveProjection.positions`, exactly as Halloween is, because an
earlier move can put a neighbour beside a later pawn or take one away. The
instructions' standing warning is that a rule added to one handler and not the
other drifts; this is one function called from both.

### Which pawn comes along
    neighbour_side(steps) = -1 forward, +1 backward

THE COMPANION FOLLOWS THE MOVER INTO THE SPACE IT LEAVES, so it stands on the
far side of the direction of travel. Both of the brief's examples are that one
rule — green forward from 3 takes blue from 2, green backward from 3 takes
yellow from 4 — which is why it is a sign and not two cases. Board topology
only: a position index, never a screen coordinate.

`neighbour_candidates` offers everything standing on that ONE position — both
halves of a widened row, and every pawn of a tower there, because the brief's
own example picks the pawn at the BOTTOM of one. Nothing else on the board is
eligible. Frozen pawns are left out (a freeze refuses a move everywhere else,
so offering one would be offering a choice that cannot be carried out) and
hidden pawns by `live_pawns`, without this having to know Shady exists.

One candidate is taken silently; several open the ORDINARY pawn `Choice` every
other card uses, so the existing selection overlay, the resubmission path and
the network all work with no new UI. `can_ask=False` (a card played BY another
card) takes the first candidate in palette order — AKO has no "decline" the way
Speedrun does, and fizzling those cards is the failure the `can_ask` note warns
about.

**THE COMPANION MOVES THE SAME MOVE** — the same signed distance the mover is
making, not "one field into the gap". On a one-field card, which is both of the
brief's examples, the two readings coincide; this one keeps the pair adjacent on
a longer card and composes with every other modifier instead of fighting them.

### Variant 2 needed no new movement path
`MoveBySteps.carry_riders` already existed, for Balbinka, and
`_op_move_by_steps` already honours it by handing the executor an empty
`carried` tuple. `board.place_pawn` then lifts that one pawn out of its tile's
stack and leaves the rest standing — which is exactly "TYLKO jednego", with a
valid board and no reconstruction. Variant 1 passes `carry_riders=True` and gets
the ordinary tower rule, including Ondrej's Radar links, because `travellers`
does the work and AKO never touches it.

`MoveProjection.move` gained `with_riders=False` so the projection agrees with
that on a multi-pawn card.

### Changed
- `data/cards.json` — AKO's passive and its two variants.
- `engine/game_state.py` — `carries_neighbour`, `carries_neighbour_alone`.
- `engine/effects.py` — `neighbour_side`, `neighbour_candidates`,
  `ako_companion`, `_Companion`, `_with_companion`, the two calls in
  `_move_pawn` / `_move_pawns`, `MoveProjection.move(with_riders=)`.

### Verification
- **1450 pass, 30 fail — the same 30 as the baseline**, unchanged since before
  stage 34 (the `Shady` → `Obóz Harcerski` rename in cards.json versus mod
  tests still using the old title, a health-page test and a real-socket test).
  No regressions.
- 39 new tests in `tests/test_ako.py`, including both of the brief's worked
  examples asserted literally, both variants side by side, the selection rules,
  the interaction with Masa solna / Halloween / a movement bonus / an ability,
  Plagiat!'s separate handler, two copies in the rack, the variant changed
  mid-match, and one over the in-process server.
- FOUR existing tests were updated deliberately, not worked around:
  `test_the_placeholder_mods_change_nothing[AKO]` became
  `test_no_mod_is_a_placeholder_any_more` plus `test_ako_changes_only_its_own_rule`
  (AKO is no longer inert, and asserting that it is would pin the opposite of
  the card); `test_every_declared_mod_rule_has_a_reader` gained the three new
  keys AND now walks VARIANT passives too — a gap stage 34 left, since a rule
  that exists only on a card's second reading is just as unimplemented if
  nothing reads it; and two stage-34 tests that asserted Sesja na PG was the
  only card with variants.
- NOT verified: no screenshots, and `inspect_frame.py` / `--selftest` were not
  run this stage. The AKO prompt was not driven through the real UI overlay —
  it is the same `Choice(kind="pawn")` every targeting card already returns, and
  that path has its own tests, but this stage added none of its own.

### Limitations and edge cases
- AKO is gated on `ctx.from_movement_card`, like Masa solna, Halloween and
  Gambit Patusa (N103), so a character ability that happens to move a pawn
  (Dziad's Skrypt) and the Chest cards are untouched. The card says "wszystkie
  ruchy"; this reading is the project's existing convention for the movement
  mods, and one line plus one test would change it if the owner disagrees.
- The companion takes the NEARER half of a widened destination rather than
  asking: it is being dragged, not steered. That is the rule a pawn passing
  through a widened row already follows (D8a); it asks nothing and consumes no
  randomness.
- A companion with nowhere to go (already at the finish, or at the start of a
  backward move) is simply not brought. The mover's own move is unaffected —
  not a refusal and not a fizzle.
- With several candidates the prompt is a THIRD question on some cards
  (direction, pawn, half, neighbour). It cannot be folded into the pawn
  question, which is about a different pawn.
- Two copies of AKO in the rack bring ONE neighbour: `mod_rule` reads a rule,
  it does not accumulate, and the left slot wins. Both copies are one logical
  card carrying one variant, per stage 34.

---

## Stage 36 — "Nie masz Rosji": stopping one opponent movement
**Date:** 2026-08-12

### Goal
Implement the Chest card in both variants: for a while, one movement made by an
opponent may be stopped. Variant 1 lasts two full rounds, variant 2 one.

### 1. The temporary effect
`StatusKind.MOVEMENT_VETO`, granted by a new `movement_veto` effect handler and
nothing else. `charges=1` IS "one movement": the tracker removes a status whose
last charge is spent, so a used veto cannot block again without anything having
to remember that it was used.

    data = {"pending": [seats that still owe a turn], "rounds_left": 1 or 2}

### 2. A full round is NOT the round counter
`_note_turn_completed`, called at the top of `_end_turn`, strikes the finishing
seat off `pending`; when that empties, one full round has passed and either the
next one begins or the status ends.

THE OWNER IS NOT IN `pending`. That is what makes the brief's own example
right: Lubin plays, and the round is up when the turn comes back to him — which
is the moment everybody else has had one. It is also why Piotrek appearing
again three slots later ends nothing: he is one seat, and the other five still
owe their turns.

### 3. Opponents
`GameState.are_opponents(a, b)` is `is_piotrek != is_piotrek` — the roles the
game already has. No character title appears anywhere in this feature.

### 4. The decision window
`SessionConfig.block_decision_seconds`, default 7, clamped 1–30, set in the
lobby ("Zasady" tab of the shared settings panel) and carried through
`LobbyState` and `room.set_settings` like every other setting.

THE ENGINE HOLDS THE LENGTH; THE AUTHORITY HOLDS THE CLOCK. `seconds` is
configuration, identical everywhere and in the snapshot. WHEN a window opened is
wall-clock time, so it lives on the authority only — `room.expire_decisions()`
on the existing hub tick, `LocalSession.tick(now)` hot-seat — and is
deliberately not in the fingerprint. A client's countdown is a picture of the
command that closes the window arriving. `ExpireMovementDecision` is
`AUTHORITY_ONLY`, so a machine with a fast clock cannot time anybody out.

### 5. Cancellation: there is nothing to roll back
`_play_card` resolves the plan and then SETS IT ASIDE. The card stays in the
hand, the board is untouched, and no operation runs:

    play → resolve → (blockable? and an opponent holds a veto?)
                          ↓ yes                      ↓ no
                     hold the plan            execute as always
                          ↓
        accept → replay the SAME command   block → discard, never run it

Accepting replays the original command through the ordinary path (guarded by
`_resolved_movement` so it is not held a second time); nothing can have changed
in between, because every other command is refused while a decision is open.

This is why "movement-triggered consequences must not happen" needed no code:
there is no tower, no check and no victory to prevent, because the movement
never happened. Pinned by a test that stacks the table into a check position and
shows `victory.review` finding nothing after a block.

### 6. The blocked card
Out of the hand and onto its own deck's discard pile — `_apply_block` uses the
same two lines `_play_card` uses — then `_after_play`, so the turn ends exactly
as playing that card would have ended it. No second discard system.

### 7. The automatic final block
`_veto_is_last_chance` simulates the rest of the effect's life against the REAL
cadence: the current turn completes, seats keep completing turns, full rounds
keep elapsing, and it asks whether any later turn belongs to an opponent while
the effect is still alive. If none does, the block fires immediately and no
window opens — a window nobody has a reason to answer is not a decision.

### 8. Several vetoes at once
All eligible opponents are blockers. The movement runs only when EVERY blocker
has accepted, so one hunter's acceptance cannot spend Piotrek's chance. The
first `BlockMovement` the authority applies wins; the second finds no decision
and is refused — which is what stops two clients cancelling the same movement.

### 9. The interface
`ui/movement_decision.py`: two buttons under the recently-played strip (where
the brief asks for them — the card being answered is the one at the top of that
strip), a countdown, and a confirmation dialog that draws the card through the
ordinary `CardRenderer.draw_in` with the ordinary reveal. It owns no game state:
it reads `state.pending_movement` every frame and answers with Commands only.

### Changed
- `data/cards.json` — the card's effect and its two variants.
- `engine/statuses.py` — `MOVEMENT_VETO` and its label.
- `engine/effects.py` — `movement_veto`, `MOVEMENT_OPERATIONS`,
  `plan_moves_pawns`.
- `engine/game_state.py` — `PendingMovementDecision`, `pending_movement`,
  `are_opponents`, `vetoes`, `veto_of`, `_note_turn_completed`,
  `_upcoming_seats`, `_veto_is_last_chance`, `_blockers_for`,
  `_open_movement_decision`, `_resume_movement`, `_apply_block`,
  `_accept_movement`, `_block_movement`, `_expire_movement_decision`,
  `_movement_decision_refusal`, snapshot.
- `engine/commands.py`, `engine/events.py` — three commands, three events.
- `net/session.py`, `server/room.py`, `server/hub.py` — the authority's clock.
- `config/settings.py`, `net/lobby.py` — the cooldown setting.
- `ui/movement_decision.py` (new), `ui/layout.py`, `ui/game_screen.py`,
  `ui/settings_panel.py`, `ui/menu.py`, `ui/network_screens.py`.

### Verification
- **1513 pass, 30 fail — the same 30 as the baseline**, unchanged since before
  stage 34. No regressions.
- 63 new tests in `tests/test_movement_veto.py`: the variants and durations;
  the three opponent rules; movement cards and movement-causing Chest cards
  blockable, while a dragged pawn, a character ability, a card that moves
  nobody and a teammate's movement are not; the window's configured length, the
  table stopping while it is open, accepting, timing out (through both the
  session's and the room's clock) and the authority-only expiry; blocking,
  the discard, the consumed use, the second movement that cannot be blocked;
  the consequence-free rollback; full rounds including the Piotrek cadence
  case; the automatic final block and that an earlier movement still opens a
  window; two vetoes with one authoritative answer; the lobby setting; the
  snapshot; the whole exchange over the in-process server; and eleven UI tests
  driving the real buttons and the confirmation dialog.
- FOUR existing tests updated: two stage-34/35 tests that listed which cards
  have variants, and the two tab-list tests for the new "Zasady" tab.
- NOT verified: no screenshots were reviewed and `inspect_frame.py` /
  `--selftest` were not run. Nothing was played end-to-end by hand.

### Limitations and edge cases
- "Blockable" is asked of the RESOLVED PLAN (`plan_moves_pawns`), not of a list
  of card titles, so a card that gains a movement later becomes blockable the
  day it does. It is additionally restricted to the movement and chest decks,
  because the brief asks for a played movement action.
- The whole table is frozen while a window is open: every turn-bound command,
  `PlayCard` and `PlaceMod` are refused. That is deliberate — a card played
  into the pause would resolve against a board that is about to change, or
  about not to.
- A veto granted WHILE a window is open does not join it: the blockers are
  fixed when the window opens.
- The automatic final block picks the lowest seat when two vetoes are both on
  their last chance. Deterministic, and the same on every replica.
- A hot-seat game's window is closed by `LocalSession.tick`, which the game
  screen calls with `time.monotonic()`. A screen that never updates never times
  out — which is the same property every other animation in the project has.
- The countdown drawn on a client starts when the event arrives, so it can
  differ from the authority's by the network latency. It is a picture; the
  buttons stop working when the authority says the window is over, not when the
  local number reaches zero.

---

## Stage 36 — "Nie masz Rosji": stopping one movement
**Date:** 2026-08-11

### Goal
The Chest card, in both variants, on top of stage 34's variant system.

    variant 1  "two_rounds"  two full rounds, one block
    variant 2  "one_round"   one full round, one block

### The effect
A status, `StatusKind.MOVEMENT_VETO`, granted by the new `movement_veto`
effect. It carries `pending` (the seats that still owe it a turn),
`rounds_left`, and `charges=1` — the single use is the tracker's ordinary
charge, so a spent veto removes itself and nothing has to remember it was used.

### A full round is not the round counter
`_note_turn_completed`, called at the top of `_end_turn`, strikes each
finishing seat off `pending`; when that set empties, one full round has passed
and the next one starts with the same debt again.

`pending` deliberately EXCLUDES the owner. That is what makes the brief's own
example right: Lubin plays, everybody else takes a turn, and the round is up at
the moment the turn comes back to Lubin. It is also what stops Piotrek's second
slot in a round ending anything — he holds every third slot, so the round
counter and "everybody has had a turn" are genuinely different things here.

### Pausing instead of rolling back
`_play_card` resolves the plan and then SETS IT ASIDE: the card stays in the
hand, the board is untouched, `_after_play` is not reached.

    play → resolve → (blockers?) → hold → answer → replay, or discard

So there is nothing to undo. A blocked movement cannot have built a tower,
cannot have triggered an identity check and cannot have won anybody the game,
because it never happened — which is what the brief asks for and what a rollback
would only have approximated. Accepting replays the SAME command through the
ordinary path (`_resolved_movement` guards against holding it twice); blocking
discards the card through the ordinary lifecycle and ends the turn exactly as
playing it would have.

Every other command is refused while a decision is open: the table is genuinely
stopped, and a card played into the pause would resolve against a board that is
about to change — or about to not change.

### Who may block
`are_opponents(a, b)` is `is_piotrek != is_piotrek`, read from the roles the
game already has. No character title appears anywhere near this feature.

### The clock, and who owns it
The engine holds HOW LONG the window is (configuration, identical everywhere,
in the snapshot). WHEN it started is wall-clock time, different on every
machine, so it lives on the authority only — `Room.expire_decisions()` on the
existing hub tick, `LocalSession.tick(now)` hot-seat, `now` passed in so
nothing depends on a frame rate and nothing sleeps.
`ExpireMovementDecision` is AUTHORITY_ONLY: a client's countdown is a picture of
that command arriving, and a machine with a fast clock cannot time anybody out.

### The automatic final block
`_veto_is_last_chance` simulates the rest of the effect's life against the real
cadence — the current turn completes, seats keep completing turns, full rounds
keep elapsing — and asks whether any LATER turn belongs to an opponent while the
effect is still alive. If none does, the block fires immediately with no window,
because a window nobody has a reason to answer is not a decision.

### Several vetoes at once
All eligible opponents are blockers. The movement runs only when EVERY blocker
has accepted, so one hunter's acceptance cannot spend Piotrek's chance; the
first `BlockMovement` the authority applies wins and the second finds nothing to
answer. One authority applying commands in order is the whole mechanism — no
client ever cancels anything locally.

### The interface
`ui/movement_decision.py`: two buttons under the recently-played strip (where
the brief asks for them — the card being answered is at the top of that strip),
a countdown, and a confirmation dialog that draws the card through the ordinary
`CardRenderer.draw_in` with the ordinary reveal hover. It owns no game state:
whether there is a decision, who may answer and whether an answer is still valid
are all read from the state on the frame they are drawn, and both buttons
produce a Command and nothing else.

The lobby setting is a sixth tab in the shared settings panel, "Zasady" — a
number per TABLE rather than per card. In the panel rather than as another row
on the two setup screens because both lay themselves out by measuring rows, and
stage 21 already records a fifth row pushing Start off a 1280x760 window.

### Changed
- `data/cards.json` — the card's effect and its two variants.
- `engine/statuses.py` — `MOVEMENT_VETO` and its label.
- `engine/effects.py` — `movement_veto`, `MOVEMENT_OPERATIONS`,
  `plan_moves_pawns`.
- `engine/commands.py`, `engine/events.py` — `AcceptMovement`,
  `BlockMovement`, `ExpireMovementDecision`; `MovementDecisionOpened`,
  `MovementAccepted`, `MovementBlocked`.
- `engine/game_state.py` — `PendingMovementDecision`, `pending_movement`,
  `vetoes`/`veto_of`/`are_opponents`, `_note_turn_completed`,
  `_upcoming_seats`, `_veto_is_last_chance`, `_blockers_for`,
  `_open_movement_decision`, `_resume_movement`, `_apply_block`, the three
  handlers, the refusal gate and the snapshot.
- `config/settings.py`, `net/lobby.py`, `server/room.py` — the cooldown.
- `net/session.py`, `server/room.py`, `server/hub.py` — the authority's tick.
- `ui/movement_decision.py` (new), `ui/layout.py`, `ui/game_screen.py`,
  `ui/settings_panel.py`, `ui/menu.py`, `ui/network_screens.py`.

### Verification
- **1516 pass, 30 fail — the same 30 as the baseline**, unchanged since before
  stage 34. No regressions, and NO existing test was modified this stage.
- 66 new tests in `tests/test_movement_veto.py`: the variants and durations;
  the three opponent rules; movement cards and movement-causing Chest cards
  blockable, while a Chest card that moves nobody, a dragged pawn, a character
  ability and a teammate's movement are not; the window's configured length,
  the table stopping while it is open, acceptance, timeout, and that neither
  spends the veto; blocking (board unchanged, card discarded, veto spent, turn
  passed, never twice); a blocked movement triggering no identity check; full
  rounds including Piotrek's repeated slots; the last chance recognised and
  fired automatically; two vetoes with one authoritative answer; the snapshot;
  the interface at four window sizes; and two over the in-process server — one
  playing the whole exchange through commands, one timing the window out from
  the room's own tick with an injected clock.
- NOT verified: no screenshots were reviewed, `inspect_frame.py` / `--selftest`
  were not run, and the feature has not been played by a human. The countdown
  was tested as a number, not as pixels.

### Limitations and edge cases
- ONE decision at a time. A second blockable movement cannot open while one is
  pending, which cannot happen today because the table is stopped meanwhile.
- Accepting requires EVERY blocker to accept. The alternative — first answer
  closes the window — would let a hunter spend Piotrek's chance, so this is a
  deliberate reading of an ambiguity in the brief.
- The automatic final block picks the LOWEST seat when two vetoes are both on
  their last chance. Deterministic on every replica; the other veto survives.
- A client that reconnects mid-window rebuilds the pause from the snapshot and
  starts its countdown from the full length, so it may see a few seconds more
  than remain. The authority's deadline is unaffected.
- `_veto_is_last_chance` walks at most 200 upcoming slots. A table whose cadence
  could not produce an opponent turn in 200 slots would fire the block early;
  no configuration reaches that.
- A blocked Chest card goes to the Chest discard pile, and a blocked movement
  card to the movement one — the card's own deck, as the ordinary lifecycle
  does it. Neither is returned to the hand.

## Stage 37 — Granny Costume, Jazdy, Where are you Marcus?, and Plac
**Date:** 2026-08-12

### Goal
Four character abilities, and one rule that sits above all of them.

    Big D Randy  Granny Costume        freeze one lone pawn for a full round
    Lubin        Jazdy                 skip Piotrek's NEXT turn
    Glockboy     Where are you Marcus? one staked check, after three others
    Norbur       Plac                  deliberately a no-op, for now

### The rule above them: no abilities while a pawn is on START
`GameState.ability_refusal()` is the ONE gate. `_use_ability` asks it once,
before any charge is spent; `hud.CharacterPanel` greys the button from the same
method, and `game_screen._ability_click` reports the same sentence. A fifth
ability written tomorrow inherits the rule without its author knowing it exists
— which is the whole point of not writing it four times.

Sesja na PG's lock moved INTO that method rather than sitting beside it, so the
call sites ask one question rather than a growing list of them.

`pawns_on_start()` deliberately excludes a pawn Obóz Harcerski is holding off
the map. It has left START; it is simply somewhere else for a round. Treating
it as still in the camp would lock every ability in the game for the length of
that card, which is a rule nobody wrote.

### Can this pawn move? — asked in one place
`effects.pawn_is_frozen`, `pawn_may_move` and `movable_pawns`. The seven
scattered `statuses.pawn_has(FROZEN, ...)` reads now all route through them, so
a second reason a pawn may not move (a future Radar link, a future Dług u
Tomasza separation) is one edit here rather than a search for every place
somebody remembered to check.

`_ordered_pawns`, `hindmost_pawn`, `foremost_pawn` and `_named_pawn` gained
`movable_only`. That single flag is how Przepis, Obniżenie progu and PAA skip a
frozen pawn and act on the next one — the skipping lives in the ORDERING, not
in the three effects, so a fourth card that names a pawn by position inherits
it. A card that names a pawn by COLOUR (`fixed`) gets no substitution: Zerówka
- czerwony means the red one.

### A full round is the owner's round — and not Nie masz Rosji's
`full_round_payload` stamps `until_seat` and `granted_turn` on the status;
`_expire_full_round_statuses` retires it when that seat BEGINS a turn again,
from both `_end_turn` and `_set_active_player`.

Stage 36's `pending` set would have been WRONG here, and this is worth reading
before anybody "unifies" the two. On the brief's own six-player order that set
empties two slots BEFORE the turn returns to Big D Randy, because the seats
still owing a turn are Piotrek's earlier slots. The brief says the freeze ends
when the turn REACHES Big D Randy. Three different rules; this is the third.

`duration_turns = 1` therefore does NOT mean `turn_counter + 1`. That reading —
one global turn — is the one the brief rules out by name.

### Jazdy is a turn taken, not a window of time
`SKIP_TURN` now carries NO expiry and is spent by the first matching turn. It
used to carry `turn_counter + 1`, which is a different promise and a real bug:
on a table where Piotrek holds every third slot that deadline can pass before
his next slot, and the skip expired having skipped nothing.

Nothing marks WHICH slot in advance, and nothing should — "the next one" is
whichever comes first, and a recorded slot number goes stale the moment the
round order changes. One status is spent by one turn, so exactly one of
Piotrek's three slots is lost.

Refused during the target's own turn, in the engine as well as the interface.

### Where are you Marcus? — the question is public, the answer is not
`RequestPawnCheck` records the colour and the seat staking itself on it, on
every machine. `victory._asked_check` answers it on the authority, exactly as
Squid Game's automatic check is already answered: a correct guess produces the
EXISTING `DeclareVictory(HUNTERS)`, a wrong one `EliminatePawn` +
`EliminatePlayer`. There is no second game-over path.

`min_checked` reads `eliminated_pawns`, which IS the completed-check record and
the only one: a colour lands there when a check resolved and the answer was no,
and a check whose answer was yes ended the match. Previews and unanswered
prompts never touch it.

### Elimination is a permission withdrawn, not a seat removed
`Player.eliminated`. The seat stays in `players`, stays in the turn order and
stays connected — every index in the command log, the server's seat map and
every snapshot assumes the seats never move.

`_resolve_eliminated` runs BEFORE `_resolve_skip_turn` (a one-off skip must not
be spent on a turn that was never going to happen) and before the interrupt (a
card cannot hijack the turn of somebody who has none). `_reject_eliminated` in
the authorisation layer refuses everything else, because the brief says they
cannot MOVE either and their turns being skipped means such a command is stale
or malicious by definition. Renaming and answering a movement are still
allowed: an observer keeps their name and their vote.

### Two bugs the new tests found
`Gejtos` (Mężczyzna) MOVED the frozen pawn. `TransferStack` picked up whole
towers without consulting the freeze. Fixed in `_op_transfer_stack` through
`pawn_may_move`, so every future user of that operation inherits it rather than
Gejtos getting a clause.

`Skrypt` OFFERED the frozen pawn. Ability prompts now filter it out (§16 asks
for exactly that). Card prompts deliberately do NOT — §8 asks for the opposite,
"choosing it does nothing", which is a thing that happens at the table and
should be allowed to happen on screen. Both end with the pawn not moving.

### Plac is a declared no-op
New `no_effect` handler; `characters.json` points at it and KEEPS `min_gap` and
`duration_turns` as documentation. It is not a stub that pretends: the plan
holds a Fizzle carrying the card's own text, so the status bar tells the table
what the card says and the table settles it — the ability-shaped version of
what `manual` does for Chest cards. `restrict_movement` stays registered and
working, so building the rule later is a JSON edit back.

### Changed
- `data/characters.json` — Norbur's ability type.
- `engine/effects.py` — `pawn_is_frozen`, `pawn_may_move`, `movable_pawns`,
  `pawn_stands_alone`, `freezable_pawns`, `completed_checks`,
  `full_round_payload`/`FULL_ROUND_KEY`; `RequestPawnCheck`, `EliminatePlayer`;
  `movable_only` on the ordering helpers; `_freeze_pawn`, `_freeze_player`,
  `_check_pawn`, `_no_effect` rewritten or added; `_movement_target`'s prompt.
- `engine/game_state.py` — `pawns_on_start`, `ability_refusal`,
  `pending_pawn_check`, `_op_request_pawn_check`, `_op_eliminate_player`,
  `_eliminate_seat`, `_eliminate_player`, `_expire_full_round_statuses`,
  `_resolve_eliminated`, `_reject_eliminated`, `_thaw_dragged`, the freeze in
  `_op_transfer_stack`, and the snapshot.
- `engine/commands.py` — `EliminatePlayer` (AUTHORITY_ONLY).
- `engine/events.py` — `PawnCheckRequested`, `PlayerEliminated`.
- `engine/victory.py` — `_asked_check`.
- `players/player.py` — `eliminated`, and it in `to_public_dict`.
- `ui/board_view.py` — `frozen_tiles`, `_draw_frozen_fields`.
- `ui/hud.py` — the button's blocked state, `_elimination_cross`.
- `ui/game_screen.py` — `_ability_click`, `_on_player_eliminated`.

### Verification
- **1616 pass, 0 fail.** The baseline was 1519 pass / 27 fail; all 27 were
  pre-existing and are fixed below.
- 71 new tests in `tests/test_stage37_abilities.py`: the START rule for all
  four abilities and its release; target filtering including the brief's
  six-pawn worked example; the freeze walked one real turn at a time until it
  reaches Big D Randy, and NOT ending after one global turn; the frozen pawn
  unmoved by Zerówka / Fillerski przedmiot / Wejściówka / Kolos z paki / Astral
  2019 / Astral 2022 / Plagiat! / Seks z pedałami (over twelve seeds) / AKO,
  and Przepis and Obniżenie progu acting on the next pawn instead; a rider on a
  frozen pawn moving alone; Sesja na PG both variants, Obóz Harcerski,
  Dzieckorolka (asserting the mover's route really crossed the field),
  Balbinka, Gejtos both halves, PAA, Skrypt; manual drag cancelling the freeze
  and cards working again afterwards; the highlight derived and never stale;
  Jazdy's timing rule, one skip out of three Piotrek slots, and the order
  resuming; Glockboy's three-check threshold, hunter victory, elimination,
  observer status, and the snapshot; and Plac's six no-op assertions.
- Several tests were STRENGTHENED after being caught passing vacuously — wrong
  choice keys left cards sitting on unanswered prompts, which moves nothing and
  looks like success. Plagiat! (`pawns`, comma-separated), Gejtos
  (`gather`/`scatter`) and AKO (`ako`, with a control proving it WOULD have
  taken the pawn) now assert the card actually resolved.
- Existing tests updated for two deliberate rule changes: ability tests now
  clear the camp (the START rule), and the Big D Randy / Norbur / Glockboy
  tests were rewritten for filtering, the no-op and the implemented check.
- Pre-existing failures fixed, unrelated to this stage: 26 from the `Shady` →
  `Obóz Harcerski` MOD rename (the CHEST card `Shady` still exists and its
  references were left alone), and `test_an_undesigned_card_resolves_and_is_discarded`,
  which still named `Nie masz Rosji` after stage 36 implemented it.
- NOT verified: no screenshots were reviewed, `inspect_frame.py` / `--selftest`
  were not run, and nothing here has been played by a human. The blue highlight
  and the elimination X are tested as STATE — `frozen_tiles()` returns the
  right tile indices, `to_public_dict()["eliminated"]` is true — not as pixels.

### Limitations and edge cases
- A frozen pawn's card returns `Refusal` (the card stays in the hand), where
  Obóz Harcerski returns `Fizzle` (the card resolves, does nothing, is
  discarded). The brief says "no effect", which reads more like a Fizzle; the
  existing behaviour and its tests predate this stage, so it was left alone and
  is flagged here rather than changed unasked. THIS NEEDS A DECISION.
- Ability prompts filter frozen pawns and card prompts do not. Deliberate: §16
  and §8 of the brief ask for opposite things. If that asymmetry is wrong, the
  one place to change it is `_movement_target`.
- `Jazdy` refuses when the target already has a skip pending rather than
  queueing a second one. Two Lubins at one table is the only way to reach it.
- Radar, Dług u Tomasza and PAA's future mechanics are NOT implemented, per the
  brief. They will consult `pawn_may_move`; nothing else was built for them.
- An eliminated seat is not dealt movement cards on its skipped turns, or the
  deck would drain one card per skipped turn for the rest of the match.
- If EVERY seat were eliminated the turn loop would stop after
  `MAX_TURN_INTERRUPTS` hand-offs. Only Glockboy can be eliminated today, so
  this is unreachable.

## Stage 38 — Ondrej's "Radar", and pawns that travel as one
**Date:** 2026-08-12

### Goal
Two pawns become one movement unit without ceasing to be two pawns, for one
full round, in two variants.

    variant 1  "check_both"  checking either linked pawn checks both
    variant 2  "check_one"   checking one checks only that one

Movement is identical under both; only the checking rule differs.

### What was already there, and what was wrong with it
`StatusKind.LINKED`, `linked_partners` and a `link_pawns` effect existed from
an earlier stage, and `travellers` already dragged a partner along. Four things
were wrong, and all four mattered:

* the members were stored `sorted()`, which threw away the selection order —
  the one thing the same-field reordering rule needs;
* the duration was `turn_counter + 1`, one global turn, not a full round;
* the partner was appended as a RIDER and placed on top, so moving the upper
  pawn of a pair landed it under its own partner and inverted the pair every
  time it moved;
* nothing brought the two pawns together in the first place.

### Ordered selection, asked once
One `Choice` with `key="pawns"`, `count=2`, `ordered=True` — the same ordered
multi-select Plagiat! uses, rather than two prompts in a row. The order is
stored on the status as `members`, first pick first.

### The same-field rule, from four examples to one expression
`restack_for_link` reads the brief's four cases as a single rule: the pair ends
up ADJACENT with the first pick directly under the second, everybody else keeps
their relative order, and the pair sits where the first pick was standing
RELATIVE TO THE PAWNS THAT ARE NOT IN IT.

The anchor is the interesting part and the three obvious guesses all fail one
of the four cases. Counting non-pair pawns below the first pick is what makes
all four come out, including case D returning the tower unchanged without being
special-cased.

Carried out by a new `RestackTile` operation, deliberately not built out of
moves: no pawn changes field, no route is walked, no distance exists. The tower
is simply standing in a different order afterwards.

### Different fields
The pawn further behind walks onto the one further ahead and lands on top by
the ordinary stacking rule. WHICH pawn moves is decided by the board, not by
the picking order — picking the front pawn first still moves the rear one.

### One movement unit, either end, right way up
`travellers` now returns its group ORDERED BOTTOM-TO-TOP over the current
board, and `_op_move_pawn` sorts the whole group — mover included — before
placing it. The mover is not always the bottom: a linked partner may be
standing under it. For an ordinary tower the mover IS the bottom, so this is
the old behaviour with the order written down instead of assumed.

`travelling_group` exposes the same list for anything that needs to reason
about the unit rather than move it.

No double movement falls out of this rather than being special-cased: a card
that moves everybody moves the pair once because the pair is one placement.

### Checking follows the link
`victory.checked_with` answers "which colours does a check on this pawn
actually inspect", and all THREE checking routes — the completed tower, Squid
Game's automatic check and Glockboy's deliberate one — now go through one
`_resolve_check`. A linked partner that turns out to be Piotrek ends the match
exactly as the checked pawn would have; checking a pawn and declining to notice
the answer would be a different rule.

The variant is recorded ON THE STATUS as `check_together` at the moment the
link is made, not looked up from the character card when the check happens.
Two reasons: `victory` has no business knowing which abilities exist, and a
variant switched mid-match must not retroactively rewrite a running link.

### A character card is a card
`CardVariant` gained an `ability` field and `with_variant` applies it. That is
the whole of the variant support — the seeding, the lobby tab, the Card
Library, `SetCardVariant` and `_reread_copies` are all deck-agnostic already,
so Ondrej appears in the variants tab beside two Mods and a Chest card without
a line of new plumbing.

### Granny Costume stays authoritative
Making the link is itself a movement, so a frozen pawn that would have to walk
cannot, and the ability is REFUSED — not allowed to bypass the freeze, and not
allowed to leave a "pair" standing on two fields, which is not a pair any
movement rule below could honour. A frozen ANCHOR is fine: it is stood next to,
not moved. A refusal spends no charge.

A freeze landing on one member of an existing pair leaves that member behind
and the other still moves. Same answer AKO already gives a frozen neighbour:
the effect does less rather than being refused.

### Changed
- `data/characters.json` — Ondrej's two variants.
- `cards/base_card.py` — `CardVariant.ability`, applied in `with_variant`.
- `engine/effects.py` — `linked_group`, `link_status`, `checks_together`,
  `stack_order_key`, `travelling_group`, `restack_for_link`, `RestackTile`;
  `travellers` reordered and made freeze-aware; `_link_pawns` rewritten.
- `engine/game_state.py` — `_op_move_pawn` places a sorted group,
  `_op_restack_tile`, `_thaw_dragged` also ends a link.
- `engine/events.py` — `TileRestacked`.
- `engine/victory.py` — `checked_with`, `_resolve_check`, and the three
  checking routes rewired through it.

### Verification
- **1665 pass, 0 fail.** The previous stage ended at 1616 pass / 0 fail.
- 49 new tests in `tests/test_stage38_radar.py`: the ordered prompt and that
  the order is stored rather than sorted; the START rule; the rear pawn walking
  onto the front one from either picking order, and stacking onto a third pawn
  already there; the four brief examples asserted TWICE — against the pure
  reordering function and again through a real activation on a real board —
  plus the invariant they are instances of and the untouched pawns keeping
  their order; movement from either end with the pair's order intact, landing
  on an occupied field, a pawn stacked above not unlinking it, an unrelated
  pawn still moving alone, and Balbinka moving the pair exactly one step;
  variant 1 checking both and variant 2 checking one, each through the real
  check, including a linked partner who IS Piotrek winning the match under
  variant 1 and not under variant 2; a colour already ruled out not being
  checked twice; a mid-match variant switch not rewriting a running link;
  expiry after a full round with movement and checking both individual again
  and no stale state anywhere; and four freeze tests.
- Two existing tests updated for deliberate changes: the Ondrej ability test
  now uses the ordered prompt, and the two variant INVENTORIES gained Ondrej.
- NOT verified: no screenshots, no `--selftest`, no human play. The link line
  the board already draws between linked pawns was not looked at.

### Limitations and edge cases
- The DIFFERENT-FIELDS case does not reorder: the arriving pawn lands on top by
  the ordinary stacking rule, whichever was picked first. §5 asks for
  reordering only when both pawns already share a field, and §4 asks for the
  stacking rules to be respected. So the first pick is guaranteed to be under
  the second ONLY in the same-field case.
- Radar refuses rather than linking in place when the rear pawn is frozen. The
  brief asks for "the smallest consistent solution"; the alternative — a pair
  spanning two fields — has no meaning under §7.
- Dragging either member by hand ends the link, following the freeze's
  existing manual-movement semantics. The brief asks for consistency with the
  existing architecture rather than naming this outcome.
- A third pawn stacked between the pair by an unrelated effect is carried along
  by the tower rule and stays between them. The pair keeps its relative order,
  which is what §7 asks; it does not become adjacent again.
- `check_together` is fixed when the link is made. A table that switches
  variant mid-round finishes the round under the rule it started.

## Stage 38a — Two Radar bugfixes: the invisible reorder and the roof landing
**Date:** 2026-08-12

### Bug 1 — a reordered tower stayed on screen in its old order
`board_view.visual` is the board's own copy of where each pawn is DRAWN, and it
was written by exactly two reactions: `TokenMoved` and `TokenWalked`. A Radar
reorder changes a pawn's HEIGHT without moving it between fields, so it emits
neither — it emits `TileRestacked`, which nothing was listening to. The engine
had the tower right immediately; the screen went on showing the old order until
some later card happened to touch those pawns and dragged the view back into
step, which is exactly the "it fixes itself after another move" symptom.

Fixed by subscribing to the event that already existed and gliding the affected
pawns to the positions the ENGINE already computed. No stack state was touched:
this is the missing propagation, not a redraw hack, and the pawn whose height
did not change is snapped rather than animated so nothing twitches.

### Bug 2 — the arriving pawn landed on the roof instead of beside its partner
`_link_pawns` moved the rear pawn onto the anchor's field and left the ordinary
stacking rule to place it, which appends to the top of the tower. With `pink`
under `yellow`, linking `pink -> green` gave `pink, yellow, green` instead of
`pink, green, yellow`.

That is worse than cosmetic. It left the pair split by a pawn that is not in
it, and it left `green` standing ON `yellow` — so the next card to move
`yellow` would carry `green` off by the tower rule while the Radar link was
still active, tearing the pair apart across two fields.

Fixed with a new pure function, `effects.insert_above`, and a second operation
in the plan: the move puts the block on the field (an ordinary move, unchanged)
and the existing `RestackTile` decides where in the tower it belongs. Bottom,
middle and top all fall out of one expression — at the top, "directly above"
IS the roof, so that case needs no special handling and gets none.

Deliberately NOT a second stacking system and not a Radar-shaped `MovePawn`:
`RestackTile` is the operation the same-field case already used, so both halves
of Radar now seat the pair through the same mechanism.

### What did NOT change
Duration, selection, the ordered pair, checking, both variants, the freeze
interaction and every other card. `insert_above` is called from one place.

### Changed
- `engine/effects.py` — `insert_above`; `_link_pawns`'s cross-field branch now
  plans `MovePawn` + `RestackTile`.
- `ui/board_view.py` — `_on_tile_restacked`, subscribed to `TileRestacked`.

### Verification
- **1682 pass, 0 fail.** The previous stage ended at 1665 pass / 0 fail.
- 14 new tests in `tests/test_stage38_radar.py`: `insert_above` at the bottom,
  middle and top of a five-tower and preserving everybody else's order; the
  brief's `pink -> green` case on a real board; the anchor at each of three
  heights; the pair surviving the insertion; a pawn above the pair NOT being
  part of it — moving it leaves the pair alone, which is the gameplay bug
  asserted directly; either member still moving the pair afterwards; and the
  insertion going through exactly one `TileRestacked`.
- 3 new tests in `tests/test_ui.py`, and these were **checked against the bug**:
  with the subscription commented out, two of the three fail. They assert
  `display_position` — what the renderer actually asks — against the engine's
  own token positions, so they cannot pass by the view inventing coordinates.
- Two stage-38 tests were UPDATED because they encoded the old, wrong
  behaviour: the arrival landing on the roof, and a pawn above the pair being
  expected to stay behind.
- All four same-field orderings from the original brief re-verified unchanged.
- NOT verified: no screenshots, no `--selftest`, no human play. The settle
  animation is tested as end-state positions, not as pixels over time.

### Limitations
- A pawn standing ABOVE the pair is still carried along when either member
  moves. That is the ordinary tower rule and not a Radar rule — the pair stays
  adjacent and in order, and the passenger is not a member of it.

## Stage 39 — Dziubdziuch's "Przerwanie Systemowe"
**Date:** 2026-08-12

### Goal
Dziubdziuch may interrupt Piotrek's movement, in four variants crossing
duration with card category.

    Variant 1  forever_any        rest of the game   Movement + Chest
    Variant 2  forever_movement   rest of the game   Movement only
    Variant 3  round_any          one full round     Movement + Chest
    Variant 4  round_movement     one full round     Movement only

### One mechanism, not two
The ability is Nie masz Rosji's interception with different numbers on it, so
NOTHING was reimplemented. The pause, the decision window, the configurable
seven-second cooldown, the confirmation, the timeout, the automatic final
block, the "the movement never happened" guarantee and the blocked-card
lifecycle are all the existing code, untouched.

What was EXTRACTED is the scope rule. `movement_veto` grew three parameters —
`targets`, `rounds`, `decks` — and `GameState.veto_covers(status, mover,
deck_id)` is now the single place that answers "is this veto entitled to stop
this movement". `_blockers_for`, `_open_movement_decision` and the last-chance
simulation all ask it instead of each working the answer out.

### The four differences, as parameters
* WHO. `targets: "piotrek"` is narrower than Nie masz Rosji's `"opponents"` —
  narrower even for a hunter, because a table with no Piotrek gives nobody to
  interrupt rather than everybody. Read from roles; no title is compared.
* WHAT. `decks` is the game's own card category. Variants 2 and 4 allow the
  movement deck only, so a Chest card that moves a pawn is simply not a
  blockable movement FOR THEM — no window opens and the veto is not spent.
  A Chest card that moves nobody is still not held, because `plan_moves_pawns`
  is asked as it always was.
* HOW LONG. `rounds: 0` (`effects.UNLIMITED_ROUNDS`) means no clock: ageing
  skips it entirely and turns passing do nothing. `rounds: 1` reuses Nie masz
  Rosji's `pending`/`rounds_left` machinery unchanged.
* USES. The existing ability-use system IS the counter, and there is no other.

### Uses, and what "until all uses are consumed" means
Activating spends one use and grants a veto with ONE CHARGE. Blocking spends
the charge and the status is gone. With uses remaining the ability may be
activated again; with none left it cannot, so the blocking power is over —
which is exactly "the effect remains available until all uses have been
consumed", expressed entirely in machinery that already existed.

A stale block command after exhaustion does nothing: with no veto there is no
window, and a late `BlockMovement` finds nothing to block.

### The automatic final block, and why it does not fire here
An unlimited veto always has another chance coming, so `_veto_is_last_chance`
returns false for it and it opens a window rather than spending itself unasked.
The forced block belongs to a veto that is running out of time; a veto with no
clock has none to run out of.

### Changed
- `data/characters.json` — Dziubdziuch's ability is `movement_veto`, with four
  variants. It was `freeze_player`, which is Lubin's skip-a-turn mechanism and
  would have skipped Piotrek's turn rather than interrupting a movement.
- `engine/effects.py` — `blockable_decks`, `UNLIMITED_ROUNDS`,
  `VETO_OPPONENTS`/`VETO_PIOTREK`; `_movement_veto` parameterised;
  `_veto_description`.
- `engine/game_state.py` — `veto_covers`; `_blockers_for` takes a deck;
  `_open_movement_decision` passes it; `_note_turn_completed` and
  `_veto_is_last_chance` understand an unlimited veto; `_BLOCKABLE_DECKS` now
  points at the shared definition.

### Verification
- **1722 pass, 0 fail.** The previous stage ended at 1682 pass / 0 fail.
- 40 new tests in `tests/test_stage39_przerwanie.py`: activation granting the
  same status kind and charge as the Chest card, spending a use, and obeying
  the START rule; Piotrek intercepted, a hunter NOT intercepted, Dziubdziuch
  not interrupting himself; every variant holding a Movement Card; variants 1
  and 3 holding a movement-causing Chest card and variants 2 and 4 letting it
  through WITHOUT spending the veto; a Chest card that moves nobody never held;
  the forever variants carrying no clock and surviving forty turns, and still
  blocking twenty-five turns later; blocking spending the veto, leaving no
  second block, and the ability being unusable with no uses left; a second use
  re-arming it; a stale block command doing nothing; the round variants
  expiring after a full round and not after one turn, and opening no window
  afterwards; the configurable cooldown, accept, and timeout all reaching the
  movement; the blocked card reaching its own discard pile; an unlimited veto
  never firing automatically; the snapshot; a seat without a veto unable to
  block; and four tests that Nie masz Rosji still has its WIDER rule, including
  both vetoes live at once with only the right one offered the window.
- Both halves of the new rule were MUTATION-TESTED: breaking the deck
  restriction fails three tests, breaking the unlimited-duration skip fails
  three others.
- Two variant INVENTORIES updated — Dziubdziuch legitimately joins them.
- NOT verified: no screenshots, no `--selftest`, no human play. The interface
  was not changed and its existing veto tests still pass.

### Limitations and edge cases
- "spala użytą kartę" was read as the existing behaviour: a blocked card leaves
  the hand and goes to its own deck's DISCARD pile, exactly as Nie masz Rosji
  already does. If "burn" was meant as removed-from-the-game entirely, that is
  a different lifecycle and is not implemented. FLAGGED FOR A DECISION.
- The ability arms a veto that must then wait for Piotrek to play something.
  It cannot interrupt a movement already in progress, and it does not stop
  movement caused by a Mod, a character ability or a manual drag — none of
  those is a played card and none comes through `_open_movement_decision`.
  Nie masz Rosji has always had the same boundary.
- With more than one use configured, the uses are spent one activation at a
  time rather than one veto carrying several charges. Either reading fits the
  brief; this one needs no new counter.

## Stage 40 — Ice Block, tower breakup, and the Piotrek victory variants
**Date:** 2026-08-12

### Ice Block: one gate in front of every check
`victory.ice_block_pending()` is asked by all THREE checking routes — the
completed tower, Squid Game's automatic check and Glockboy's deliberate one —
before any of them resolves, so a fourth route added later inherits the ability
by calling the same function rather than by remembering to.

It returns a COMMAND (`OpenCheckDecision`) rather than mutating, like
everything else in that module: the window is state every client must agree
about, so it is opened by a logged command and not by the authority quietly
setting a flag.

ICE BLOCK IS A PIOTREK **SKILL**, NOT A CHARACTER ABILITY. Read off
`player.character` first, it silently never fired — the ability existed, the
window never opened, and every existing check test still passed. `ice_block_card`
now checks `player.skill` and falls back to `character`, so a future table that
hands it out the other way needs no change.

### The window, and why nothing sleeps
`PendingCheckDecision` is `PendingMovementDecision`'s shape and for its reasons:
`seconds` is configuration and travels in the snapshot, `opened_at` is
wall-clock and lives on the authority only. The timeout runs on the SAME tick
in `room.py` and `session.py`, via authority-only `ExpireCheckDecision` — so a
client with a fast clock cannot time Piotrek out early, and a stale one cannot
refuse after the deadline.

Default ten seconds (`RULES.check_decision_default`), clamped by the limits the
movement window already uses.

### Allowing, refusing, and timing out
Allow and timeout are the SAME PATH and cost nothing — which is the brief's
rule and also the only one that makes a timeout safe, because a player who
loses their connection must not lose a charge for it. `check_allowed` records
the colour so `review` resolves the check instead of re-asking about the same
unchanged tower.

Refusing spends one use and CANCELS the check rather than answering it: no
colour is crossed off, no identity is compared, nothing is revealed — there is
no answer to leak because the question was never put.

The re-check lock is the card's own text — "Pionki muszą być rozdzielone przed
kolejnym sprawdzeniem" — as `check_needs_separation`, released from
`_sync_token_positions` when the pawns are no longer gathered. Without it the
identical tower would be re-checked on the very next command and the refusal
would have bought nothing.

### Checking variant 2: the tower comes apart
Armed in `_eliminate_pawn`, because that is the one place every FAILED check
goes through. A check Ice Block refused produces no elimination at all, so "no
check, no breakup" falls out rather than needing to be stated.

The two-second pause is a deadline on the authority's clock aged by the same
tick as the other two, never a sleep. `effects.tower_pairs` /
`breakup_positions` / `tower_breakup_plan` are pure functions asked of the
BOARD: positions are the game's own logical steps, so a doubled row is one
position with two fields and counts as one step back — which is what makes
"2a or 2b" a choice rather than two destinations. Nothing is hard-coded.

Five pawns work because nothing assumes six: the last group is simply a group
of one and travels alone.

### ⚠ ONE DELIBERATE DEVIATION FROM THE BRIEF — NEEDS A RULING
§8 states the principle as "divide the tower into groups of two according to
their position inside the original tower, then place those groups on the fields
behind the original tower". Implemented literally: bottom group nearest, top
group furthest back — which is what §7 and §9 both need, since the group
Piotrek places on the doubled 2a/2b field is the top one.

The brief's WORKED EXAMPLE instead puts the bottom pair on 3 and the middle
pair on 4, i.e. those two swapped. No rule stated anywhere produces that
ordering; both worked examples share it, so it reads as a slip carried between
them. Everything else in both examples comes out exactly: the pairing, each
pair's internal order, the top group on 2a/2b, the lone fifth pawn.

IF THE EXAMPLE IS RIGHT AND THIS IS WRONG, the fix is one line in
`tower_breakup_plan` — swap the first two entries of `groups`. It is a single
function for exactly that reason.

### Piotrek's 2a/2b choice
`pending_breakup.choice_position` is the doubled row the furthest-back group
lands on; `ChooseBreakupTile` is refused for anybody but Piotrek's seat — NOT
the player whose card built the tower, which the brief is explicit about. No
answer before the deadline uses the first field, deterministically, so a
disconnected Piotrek cannot hang the table.

### Victory variants
`victory.escaped_pawn()` — `own_pawn` (the game as it was) or `any_pawn`. The
`Verdict` still names the HIDDEN colour under both, so winning on somebody
else's pawn does not rename Piotrek or leak which pawn he was. Nothing else in
the module asks the question, which is what keeps §11 true.

### Configuration
`check_variant`, `victory_variant` and `check_decision_seconds` on
`SessionConfig`, with `CHECK_VARIANTS` / `VICTORY_VARIANTS` id→label tables so
the lobby, the panel and the config read one list. Unknown values fall back to
the default rather than crashing. Defaults preserve the old game exactly.

### Changed
- `config/settings.py` — the two variant tables, three new fields, clamps,
  `check_decision_default`, `tower_breakup_seconds`.
- `engine/victory.py` — `ice_block_card`/`ice_block_uses`/`ice_block_pending`,
  `escaped_pawn`, and the three checking routes gated.
- `engine/effects.py` — `tower_pairs`, `breakup_positions`,
  `tower_breakup_plan`; `refuse_check` now explains that it is reactive.
- `engine/game_state.py` — `PendingCheckDecision`, `PendingTowerBreakup`, the
  five handlers, `_arm_tower_breakup`, `_release_check_lock`, snapshot fields.
- `engine/commands.py`, `engine/events.py` — the new commands and events.
- `server/room.py`, `net/session.py` — both deadlines on the existing tick.

### Verification
- **1769 pass, 0 fail.** The previous stage ended at 1722 pass / 0 fail.
- 47 new tests in `tests/test_stage40_ice_block.py`: the window opening before
  a check resolves and NOT opening with no skill or no uses; the ten-second
  default and its configurability; allow → check proceeds and the use remains;
  refuse → check cancelled, one use spent, nothing revealed, no victory; the
  same tower not re-checked until the pawns separate, and separating re-arming
  it; only Piotrek answering; nothing at all decided while the window is open,
  not even a victory; the timeout driven through the real session tick, costing
  nothing, and a late refusal after the deadline rejected; variant 1 leaving
  the tower alone; a SUCCESSFUL check breaking nothing; the two-second wait
  driven tick by tick; six-pawn and five-pawn towers (the latter hidden through
  the real HIDDEN status, since a pawn merely lifted off the board leaves the
  tower incomplete and no check happens); pair order preserved; the pairing
  rule as a pure function; Piotrek choosing 2a/2b, a hunter refused, and no
  answer defaulting deterministically; a refused check breaking no tower and an
  allowed one still doing so; both victory variants including the verdict still
  naming the hidden colour and checking being untouched; and the defaults.
- Four existing tests updated for the deliberate rule change: `test_victory.py`
  now answers the Ice Block window in its tower helper (Piotrek is DEALT a
  skill at setup, so when it is Ice Block the window is correct behaviour), the
  Ice Block button test now asserts the button explains itself, and
  `test_the_automatic_check_reaches_every_machine` puts the skill aside.
- THAT LAST ONE WAS FLAKY AND WAS ONLY CAUGHT BY THE CLEAN-EXTRACT RUN. Whether
  Piotrek holds Ice Block depends on the shuffle, so the test passed or failed
  on the deal — measured at three failures in five runs before the fix, eight
  clean runs after. The check-dependent suites were then run six times over to
  look for the same latent problem elsewhere: 350 pass every time.
- 11 further tests for the interface and the lobby: the panel showing itself to
  Piotrek and to nobody else; both buttons producing Commands and the refusal
  asking first; the drawn countdown reaching zero WITHOUT closing the window;
  the scatter choice offered to Piotrek only, naming the two real fields, and
  going away once answered; the lobby carrying all three settings through
  `to_dict`/`from_dict`; an unknown variant id falling back; the settings panel
  defaulting to the old game and stepping to the new one; and the panel's
  choices reaching a normalised `SessionConfig`.
- The two panels were MUTATION-TESTED: removing the seat guard and removing the
  confirmation each fail two tests.
- NOT verified: no screenshots, no `--selftest`, no human play. The panels are
  tested through their state, their hit-testing and the Commands they return,
  never as pixels.

### The interface, and the lobby (stage 40b)
`ui/check_decision.py` carries both panels, built on the shapes that already
existed rather than beside them:

* `CheckDecision` — the same two buttons in the same place as the movement
  window's, with the same confirmation in front of the destructive answer,
  because spending the only use of a card by mis-clicking a small button is not
  a decision anybody made. The two windows can never be up at once: `review`
  decides nothing while a check is pending, and a paused movement is not a
  command that can arm one, so `check_decision_panel` returns the movement
  window's rect rather than finding somewhere else to live.
* `BreakupChoice` — the 2a/2b pick, centred over the board because the choice
  is about two fields and the panel names them.

Both are windows onto authoritative state: they read the engine on the frame
they are drawn, they own no copy of anything, and every button produces a
Command. The countdown is a PICTURE — a client whose clock runs out simply
stops responding; the command that ends the window comes from the authority.

The rules tab grew three rows using the SAME named-option machinery the
variants tab uses, so a rule variant is a labelled choice rather than a second
kind of row. The choice travels menu → `SessionConfig`, and lobby → wire →
`from_dict` → `SessionConfig` for an online table, with an unknown id falling
back to the default rather than crashing.

### Limitations
- THE TOWER-BREAKUP ORDERING IS A JUDGEMENT CALL. See the warning above.
- Ice Block's button refuses with an explanation rather than doing nothing.
  The ability is reactive and there is no check on the table to refuse when it
  is pressed.
- A refusal locks checking until the pawns separate. If no effect ever
  separates them the hunters cannot check again — which is the card's printed
  rule, but worth knowing.

## Stage 40a — Four bugs the screenshots exposed, and why the tests missed them
**Date:** 2026-08-12

Stage 40 shipped with a green suite and four real defects. Two screenshots from
the running game found all four in minutes. The lesson is recorded here because
it generalises: EVERY ONE OF THEM LIVED IN THE GAP BETWEEN A PASSING TEST AND A
RUNNING FRAME.

### Bug 1 — a whole sentence inside the stepper's well (screenshot 1)
`CHECK_VARIANTS` / `VICTORY_VARIANTS` held ONE string per variant — "Wariant 1
— nieudany check nic nie zmienia" — and the rules tab passed it as both the
name and the description. The well is a ~100px box between -1 and +1, so
`fit_text` shrank the sentence to its floor and drew it 243px wide: 65px over
each button, exactly as the screenshot shows.

Now `id -> (name, description)`. The well holds "Wariant 1"; the sentence goes
on the row's help line, which is where `is_choice` rows already put it. Nothing
about the drawing changed — the data was wrong, not the layout.

MEASURED, not assumed: the new test renders both strings through the real
`fit_text` and asserts the drawn rect clears both buttons. Against the old
strings it fails by those same 65px.

### Bug 2 — "Wariant 2, then -1" sat still
`SettingsTab.bump` clamped to the TAB's `low`/`high`. The rules tab mixes a
1..30 timer with a 0..1 variant, so index 1 minus one gave 0, which was below
`low` of 1, and snapped straight back. The same bounds let +1 walk the stored
index to 30 while the display stopped at the last variant — a row could look
unchanged and be far out of range.

`bounds_for` gives a CHOICE row its own list's bounds and a counting row the
tab's, unchanged. The variants tab benefits too: a card with two variants on a
tab whose longest list is four could previously drift past its own end.

### Bug 3 — the ghost tower (screenshot 2)
The breakup PLACES pawns rather than walking them, so it emitted neither
`TokenWalked` nor `TokenMoved` — and those two were the only things that ever
wrote `board_view.visual`. The engine had every pawn on its new field and the
x2 badges (which count the authoritative stack) moved at once, while the pawns
stayed drawn in a tower on the old field. Screenshot 2 is precisely that: three
badges in the right places and six pawns stacked in the wrong one.

Fixed by subscribing to `TowerGroupPlaced` and settling those pawns onto the
positions the ENGINE already computed — the missing propagation, not a redraw
hack and not hiding the old tower. `_settle_pawns` is now shared with the
restack reaction, which had the identical problem in stage 38a. THAT IS TWICE
NOW: any new operation that moves pawns without walking them must tell the view.

### Bug 4 — the confirmation dialog crashed on its first draw
`theme.text_body` does not exist. Nothing had ever reached that line: the panel
tests drove the buttons and the engine, and the dialog is only PAINTED once a
refusal has been started. One click in the running screen raised
`AttributeError` immediately.

### And the geometry was wrong
`breakup_positions` started one field BEHIND the tower, so every group moved
and the original field emptied. The owner's rule and the board screenshot agree:
the bottom pair stays put, group *i* falls *i* fields back. A six-pawn tower on
field 4 occupies 4, 3 and 2, with 2 the doubled row Piotrek picks a field on.
Fixed in the one function that decides where a group goes.

### Verified in the RUNNING GAME, not just in tests
Driven through a real `GameScreen` + `LocalSession` against SDL's dummy driver,
clicking real rects and ticking the real host clock:

1. Ice Block window opens on a check — panel active, countdown running.
2. Click REFUSE → confirmation appears; confirm → check cancelled, use 1→0,
   `eliminated_pawns` empty, no breakup armed, separation lock set.
3. Click ALLOW → use stays at 1, check resolves, colour crossed off, breakup
   armed under variant 2.
4. Six-pawn tower on field 4 → groups on 4, 3, 2; scatter panel offers 3a/3b;
   clicking the SECOND field puts the top pair there (not the default).
5. Host tick at +2.5s resolves it; all six pawns drawn exactly where the engine
   says (distance < 1px). With the subscription disabled, four of six read
   GHOST — the screenshot reproduced and then fixed.
6. Five-pawn tower (real HIDDEN status) → 2/2/1, top pawn alone, no ghost.
7. Variant 1 → nothing armed, all six pawns still on one field after the tick.
8. Victory variant 1 → another pawn at the finish wins nothing; variant 2 →
   `declare_victory`, and the verdict still names the HIDDEN colour under both.
9. Both selectors stepped +1/+1/-1/-1: they walk both ways and stop at both
   ends; the timer row still counts 1..30.

### Verification
- **1779 pass, 0 fail.** Stage 40 ended at 1769.
- 10 new tests, and all three engine/UI fixes were MUTATION-TESTED: reverting
  the per-row bounds fails 2, the geometry fails 3, the ghost fix fails 1.
- Two stage-40 tests corrected — they encoded the old, wrong geometry.
- The ghost test drives the SESSION rather than applying the command to the
  state, because applying it directly moves the pawns and tells the view
  nothing, which is the bug rather than a way of testing it.

### Limitations
- Still no human has played this. "Running game" here means the real screen,
  real layout rects and real host clock under a dummy video driver; it is not
  a person looking at a monitor.
- Ice Block only appears if Piotrek is DEALT the Ice Block skill, which is
  random. That is the existing skill-deal design, not something this stage
  changed, but it means a given match may never show the window.

## Stage 40b — Ice Block does not stop the breakup, and the scatter is centred
**Date:** 2026-08-12

Two focused corrections. Nothing else was touched.

### 1 — the trigger is the ATTEMPT, not the answer
`_arm_tower_breakup` was called from `_eliminate_pawn` only. A refusal
deliberately eliminates nothing, so no breakup was ever armed — which encoded
"no check, no breakup". That is one reading of the rule and not the one the
game wants.

It is now called from `_refuse_check` as well. Ice Block stops the CHECK — no
identity compared, no colour crossed off, nothing revealed — but it does not
stop the tower being pulled apart by the attempt. Same delay, same grouping,
same 2a/2b choice. Under variant 1 there is no breakup to inherit, so a
refusal there still leaves the tower standing.

`checked` is now allowed to be a colour that is not in the tower's stack only
when it is empty, so the elimination path keeps its "not a tower check" guard
while the refusal path passes the colour it was asked about.

### 2 — the scatter is centred on the tower, not walked backwards from it
`breakup_positions` stepped -1, -2, -3, so a tower on field 4 landed on 4, 3
and 2. The rule is symmetric: the bottom group stays, the next takes the field
immediately BEFORE, the next the field immediately AFTER. A tower on 4 occupies
3, 4 and 5.

`breakup_offsets` states that as `0, -1, +1, -2, +2, ...` — a rule rather than
three literals, because a rule cannot be wrong about a case nobody tried. Six
pawns is the most the game can stack, so a fourth group is unreachable today.

Positions are still the board's own logical steps, so a doubled row is ONE
position holding two fields and counts as a single step in either direction —
which is what keeps Piotrek's 2a/2b choice meaningful. A group with nowhere to
go (a tower on the first field, or at the far end) stays on the tower's field
rather than reflecting to the other side, which would silently make the
breakup asymmetric.

### Two things found while fixing those
* THE BOTTOM GROUP WAS BEING RE-PLACED ON `tiles[0]`, so a tower standing on 4b
  shuffled across to 4a on its way to standing still. It now keeps the exact
  tile it was already on.
* THE 2a/2b QUESTION WAS ASKED ABOUT THE LAST GROUP. With the scatter centred,
  the doubled row can be the field before the tower as easily as the one after,
  and the group that does NOT move should never be asked about at all. The
  choice now goes to the first MOVING group whose destination is doubled.

### A harness trap, recorded because it cost real time
`GameScreen.update` ticks the session from the REAL clock. A test that draws a
frame and then calls `session.tick(now=200.0)` has already had `opened_at`
stamped with a monotonic timestamp, so its made-up `now` is in the past and the
deadline never passes. Two manual runs looked as though the breakup had stalled
and one test failed only outside its own file. Both now read the deadline the
session actually recorded and step past that. NOT A GAME BUG — but a shape to
watch for in every future timing test.

### Verified in the running game
Real `GameScreen` + `LocalSession`, real layout rects, real clicks:

1. Variant 2, ordinary failed check, tower on 4 → groups on 4, 3 and 5; board
   and state agree; nothing on field 2.
2. Variant 2 + Ice Block REFUSE (clicked through the confirmation) → use 1→0,
   `eliminated_pawns` empty, breakup armed, tower scatters onto 4, 3, 5, no
   ghosts.
3. Variant 1 + Ice Block REFUSE → use spent, no breakup, all six pawns still on
   field 4.
4. Five-pawn tower (real HIDDEN status) → 2 / 2 / 1 with the lone pawn on the
   field AFTER the tower, no ghosts.
5. Doubled row → the panel offers 4a and 4b, clicking the second puts that
   group on tile 7 (not the default), and the bottom group keeps tile 8.

### Verification
- **1788 pass, 0 fail.** Stage 40a ended at 1779.
- 8 new tests; both fixes MUTATION-TESTED — reverting the centred scatter fails
  9, reverting the refusal arming fails 2.
- 6 stage-40 tests updated: five for the corrected geometry, one because
  "a refused check breaks no tower" is now exactly backwards.
- The stage-40 suite was run six times over to confirm the timing fix removed
  the flake.

### Limitations
- A group with nowhere to go stays on the tower's field, so a tower on the
  first field puts two groups on one square. Rare, and better than inventing a
  field, but it is a silent collapse rather than an announced one.
- If BOTH neighbours are doubled rows, only the first moving group gets the
  choice; the other takes its first field. The existing choice mechanism holds
  one question at a time and was deliberately not extended.

## Stage 41 — Movement undo, Dług u Tomasza, and Liskowy Konkurs
**Date:** 2026-08-13

Three features around ONE idea: the window between a player finishing a turn
and the next player playing a card. Undo lives in it and so does Liskowy
Konkurs, so they are offered and withdrawn by the same fact rather than by two
rules that could drift.

### Undo: a photograph, not a rewind script
`engine/undo.py`. A `TurnCheckpoint` is taken in `_play_card` AFTER the
questions and the refusals and BEFORE anything moves — earlier would photograph
a table that was never reached, later would photograph the change.

IT DOES NOT DEEP-COPY THE STATE. `copy.deepcopy` works and takes 9ms, but the
pieces it produces are new objects, and `board_renderer` caches the board while
the interface holds cards; swapping them out leaves half the screen drawing a
game nobody is playing. So the checkpoint stores POSITIONS AND MEMBERSHIP — by
card uid and tile index — and puts the EXISTING objects back. Card identity
survives a rewind, which is what keeps `find_discarded(uid) is card` meaningful.

Restored: pawn positions, every tower's order, hands, both piles of every deck,
ability charges, all statuses, the RNG state, and the plain turn values
(`_SCALARS`, listed rather than discovered so a new field is a decision).

### The card that came back, and the card that goes back
The played card returns to the hand because the hand and the discard pile are
both restored by membership. The card DRAWN at the end of the turn goes back to
the top of the draw pile for the same reason — the pile's ORDER is restored, so
the drawn card is wherever it was, which is on top.

§6's harder requirement falls out of the same fact: because the order is exact,
the corrective turn draws THAT SAME CARD again. It is not implemented as a rule
anywhere, and that is precisely why it can be relied on.

### The window
`_open_turn_window` both takes the checkpoint and closes the previous player's,
because the next card played is what ends the previous window. `can_undo` is
the engine's answer; the button being hidden is a convenience, not the rule, so
a stale or forged `UndoMove` is refused rather than obeyed.

### Dług u Tomasza
Adjacency is counted in board POSITIONS, not tiles, so a doubled row is one
place: 3a and 3b are the same field for this rule and neither is a gap from 2
or 4. That is what makes it read identically on single and double rows.

If the pair is already too close the ability separates them AS IT LANDS: the
pawn further ahead steps forward until a whole field is clear, the one behind
does not move. The stacked case is not a second branch — a tower is two pawns
on the same position, so it needs two fields, and the tower order breaks the
tie so the pawn on top is the one that steps off.

Enforcement is ONE question asked in ONE place: `_op_move_pawn`, which every
movement in the game lands through, calls `effects.separation_blocks`. No card
knows the ban exists, and a card written next year inherits it. The offending
movement is CANCELLED rather than trimmed — shortening it would invent a
distance the card never had — and other pawns' movements are untouched.

Manual dragging cancels it, alongside the freeze and the Radar link, through
the same `_thaw_dragged`: all three are promises about where pawns stand, and
hand-placing one is exactly what makes such a promise unkeepable.

### Liskowy Konkurs — TWO different things
Conflating them is the mistake the brief warns about, so the effect branches on
one question: does he hold the turn right now?

* BEFORE his move — an extra CARD dealt immediately and an extra PLAY owed.
  `_after_play` spends the owed play INSTEAD of ending the turn, and does not
  refill (the extra card was already dealt; refilling would hand out a third).
* AFTER a move — the turn comes back whole. The card stays played, stays
  discarded, and the card drawn at the end of that turn stays in his hand.
  Nothing is rewound: this is not undo and must not behave like it.

Taking the extra turn CLOSES the undo window, which is §22's choice made
concrete — he picks one or the other, never both.

### Changed
- `engine/undo.py` — new.
- `engine/game_state.py` — `turn_window`, `extra_plays`, `_open_turn_window`,
  `_close_turn_window`, `can_undo`, `_undo_move`, `extra_play_pending`,
  `_grant_extra_play`, `_op_grant_extra_play`, `_op_grant_extra_turn`,
  `_adjacency_ban`; the ban enforced in `_op_move_pawn`; `_after_play` spends
  an owed play; `_thaw_dragged` also drops the ban.
- `engine/effects.py` — `forbidden_pairs`, `separation_ok`,
  `separation_blocks`, `_closer_pair_order`; `_forbid_adjacency` and
  `_grant_extra_turn` rewritten; `GrantExtraPlay`/`GrantExtraTurn`.
- `engine/commands.py`, `engine/events.py` — `UndoMove`; `MoveUndone`,
  `ExtraPlayUsed`, `ExtraTurnGranted`.

### Verification
- **1824 pass, 0 fail.** Stage 40b ended at 1788.
- 36 new tests in `tests/test_stage41_undo.py`, and the three core mechanisms
  were MUTATION-TESTED: making undo forget the deck fails 3, leaving the window
  open for ever fails 2, and dropping the adjacency check fails 1.
- Everything was also driven by hand against a real game before the tests were
  written: undo restoring positions/hand/discard/top-of-deck and refusing a
  second time or a foreign seat; the corrective turn drawing the same card; the
  same-field, stacked and adjacent corrections; a card fizzling against the ban
  while an unrelated pawn still moved; the drag cancelling it; and both halves
  of Liskowy Konkurs including the closed window.
- 4 existing tests updated: three Atencjusz tests (the ability no longer grants
  an `EXTRA_TURN` status) and one UI test that asserted that status.

### Limitations
- NO INTERFACE. The undo button and a Liskowy Konkurs prompt are not drawn;
  `can_undo` and `extra_play_pending` are the state a panel would read, and the
  commands exist and are authoritative, but nothing is on screen yet.
- `StatusKind.EXTRA_TURN` is now unused — Liskowy Konkurs does its work
  directly rather than leaving a status for the turn loop to notice. Left in
  place rather than removed, in case another ability wants it.
- A checkpoint is taken for every played card, including Chest and Mod cards
  that do not end a turn. Undoing one of those is possible and rewinds
  correctly, but "the window closes when the next player plays" is a looser
  promise there than for a movement card.
- The ban cancels a whole movement operation when it would breach the gap. A
  card that moves several pawns in one operation therefore loses all of them,
  not just the offender.

## Stage 41a — The undo button, and the bug it exposed
**Date:** 2026-08-13

Stage 41 shipped the engine with no interface. This is the interface, and
building it found a real defect in the rule it was drawing.

### THE BUG: the offer belongs to the window, not to the view
`can_undo` was first written as `state.can_undo(self.view_seat)`. That reads
correctly and is wrong: `view_seat` FOLLOWS THE ACTIVE PLAYER, so on a hot-seat
table it moves to the next player the instant a card is played — which is the
exact moment the previous player earns the undo. The button vanished precisely
when it should have appeared, and every engine-level test passed throughout,
because none of them had a view.

`undo_seat` now asks the WINDOW who owns it and `may_control` whether this
machine may act for that seat — the same question the rest of the screen asks,
so the two cannot drift. Mutation-tested: putting `view_seat` back fails two
tests.

### The button
`Layout.undo_button_rect` puts it under the turn bar rather than in the right
column: it is about the turn that just happened, not about the card panel, and
it appears and vanishes, so a place of its own keeps it from shoving a
permanent control around every time a card is played. Drawn ONLY while the
offer stands, so its absence is the rule rather than a greyed-out hint.

Liskowy Konkurs needed no new control at all: the existing ability button
already submits `UseAbility` for the seat this client is showing, and the
engine decides which of the ability's two halves applies. The window it uses is
the same one the button reads.

### Three traps this cost, all recorded because they will recur
* A CARD LANDING ON A WIDENED ROW ASKS WHICH HALF. An unanswered question
  resolves nothing and opens no window, so a test that plays a card and walks
  on measures the wrong thing. `_play_card_fully` answers whatever is asked.
* A CARD AIMED AT A PAWN STILL IN THE CAMP REFUSES, and a refused card opens no
  window either. The helper places everybody first.
* `_hot_seat` ALREADY EXISTED in `test_ui.py` with a different signature. The
  new helper shadowed it and broke six unrelated tests — which is how it was
  caught. Renamed to `_allow_every_seat`.

### Changed
- `ui/layout.py` — `undo_button_rect`.
- `ui/game_screen.py` — `undo_seat`, `can_undo`, `_undo_click`,
  `_draw_undo_button`, and the click routing.

### Verification
- **1829 pass, 0 fail.** Stage 41 ended at 1824.
- 5 new tests in `tests/test_ui.py`: the button appearing for the player who
  just moved and NOT for the view; the window MOVING to the next player rather
  than closing, so each earns their own undo while the first player's becomes
  unreachable; clicking it rewinding the card, the pawn and the turn; the rect
  not overlapping the end-turn button; and the ability button taking the extra
  turn from inside the window on somebody else's turn.

### Limitations
- The undo button says nothing about WHAT it would undo. The checkpoint knows
  the card, so a caption naming it is a small change if the table wants one.
- There is no separate prompt for Liskowy Konkurs — it shares the ability
  button, so a player has to know the window exists. A hint in the status bar
  when the window opens would be the natural next step.
- Still no human has played this. Everything here is the real screen, real
  layout rects and real clicks under a dummy video driver.

## Stage 41b — Undo redraws the board, and the button moves onto it
**Date:** 2026-08-13

### THE BUG, AND WHY IT IS THE THIRD TIME
`board_view.visual` is the board's own copy of where each pawn is DRAWN, and
until now only the movement reactions ever wrote it. Undo emits `MoveUndone`,
walks nobody, and nothing was listening — so the engine restored the pawn and
the screen went on drawing it where the card had put it.

This is the same shape as stage 38a (restack) and stage 40a (tower breakup).
Three times is a pattern, so the fix this time is GENERAL rather than another
subscriber that settles one event's pawns:

`BoardView.resync()` rebuilds every drawn position from the engine and throws
away the animation state that could contradict it — the in-flight walks, the
tweens, a drag, the route preview, an expanded tile. It SNAPS rather than
glides, because a rewind is not a journey anybody made and animating pawns
backwards would invent a movement the game does not contain.

`_on_move_undone` simply calls it. Deliberately not "move the pawn back": a
checkpoint may have restored several pawns, a tower's order, or nothing
visible, and the view cannot know which — asking the engine for every position
is the only answer that is right for every undoable action rather than for the
one that happened to be tested.

Everything else on screen is derived per frame — towers, the x2 badges, the
highlights, the counters, the hand, the last-played card — so what is CACHED is
exactly what is reset, and nothing else needed touching.

### An in-flight walk had to be cancelled too
Clicking undo mid-animation is exactly what a real player does. A surviving
walk keeps writing its old destination into `visual` AFTER the resync has
corrected it — the same bug arriving a frame later. `Animator.clear()` and
`walks.clear()` are part of the resync for that reason, and the regression test
clicks while the pawn is provably still walking.

### The button moved onto the board
`undo_button_rect` is now anchored to `board_viewport` — the same rect the
board is drawn into — at a scaled inset from its top-left. It therefore keeps
the same RELATIVE place at every resolution rather than a fixed pixel offset:
measured at 1280×760, 1600×900, 1920×1080 and 2560×1440 it sits at 0.9–1.2% of
the board's width from the left and 2.1–2.5% from the top.

It covers no field. Checked against the real tile geometry at three window
sizes × three board lengths (18, 24 and 40 cells): the track is laid out from
the viewport's centre outwards, so the top-left corner is empty in every case,
including START.

It does not follow the camera, so panning or zooming slides the road under a
control that stays where the player left it — which is what an overlay should
do.

### AND IT HAD TO MOVE IN THE EVENT CHAIN TOO
Putting the button over the map broke it silently: `board_view.handle_event`
claims any click offered to it as a map drag, so the button drew correctly and
did nothing. It now sits with the card-library and end-turn buttons in the
group checked BEFORE the board, guarded by `can_undo` so it does not swallow
drags over an empty corner when no offer stands. The manual scenarios caught
this; the first run showed "card back=False" on every undo.

### Changed
- `ui/board_view.py` — `resync`, `_on_move_undone`, subscribed to `MoveUndone`.
- `ui/layout.py` — `undo_button_rect` anchored to the board viewport.
- `ui/game_screen.py` — the button drawn immediately after the board, and
  handled before it.

### Verified in the running game
Real screen, real layout rects, real clicks: two undoable moves in a row, each
rewound with the pawn drawn where the engine says (0 ghosts); a move that
carried a six-pawn TOWER, rewound with the tower's order intact and no ghosts;
and the Ice Block + variant 2 breakup path re-run end to end — window opens,
refusal accepted, breakup still armed, groups land on 3/4/5, no ghosts.

### Verification
- **1841 pass, 0 fail.** Stage 41a ended at 1829.
- 12 new tests in `tests/test_ui.py`, and all three fixes MUTATION-TESTED:
  removing the `MoveUndone` subscription fails 3, removing the animation cancel
  fails 1, and putting the button back on the turn bar fails 4.
- The in-flight test was strengthened after the first mutation run passed it —
  it had asserted "a walk OR a tween exists", which was true of an unrelated
  tween. It now names the pawn's own walk.

### Limitations
- `resync` is a blunt instrument by design: it discards an expanded tile and a
  route preview along with everything else, so an undo clicked mid-inspection
  closes the fan. Correct, but abrupt.
- The button still does not name the card it would undo.
- Still no human has played this. "Running game" means the real screen and real
  clicks under a dummy video driver.

## Stage 42 — Herold: the character, the Mod, and one copy mechanism
**Date:** 2026-08-13

Two entities that do the same thing, sharing all of it: a new CHARACTER whose
ability is Messenger, and a new MODY PATUSA card called Herold.

### One effect, two doors
`copy_ability` is the whole mechanism, and neither Herold has any code of its
own. The character resolves that spec through the ordinary `UseAbility` path;
the mod resolves the SAME spec through a new `CopyAbility` command. That is
what makes "shared" a fact rather than an intention — and the tests compare the
two by running the same copy through both doors and diffing the resulting
board, because "shared" is a claim about BEHAVIOUR, not about which file the
code sits in.

### How the copy works
The chosen character's ability spec is handed to its own handler with HEROLD'S
context — his seat, his choices. Everything else follows from not touching the
spec:

* the ability acts for Herold ("copy the effect, not the ownership");
* its target rules are untouched — Jazdy still names Piotrek, because that is
  written in the spec and not inferred from who was holding the card;
* its own questions reach Herold, because the inner handler asks `ctx.choice`
  for its own keys and the pipeline carries the answers back. Granny Costume
  still asks which pawn; Radar still asks for an ordered pair. NOTHING is
  flattened into a single click;
* the VARIANT in play is the table's, because the card object is read live and
  `with_variant` has already been applied to it. There is no Herold-specific
  variant and no code that could produce one;
* all of the borrowed ability's validation runs, because it is the same handler
  doing the same refusing.

`COPY_CHOICE_KEY` is `"ability"`, deliberately unlike every key a borrowed
ability uses, so the outer and inner questions can never collide.

### Eligibility is asked of the table
Any character a player actually HOLDS, with an activated ability, not the
borrower's own seat, and not already spent. A character nobody drew is not in
the game and is not offered. Messenger is excluded by ability TYPE rather than
by title, so the recursion is unreachable even with two Herolds at the table.

### Uses
Herold's one Messenger use is spent only by a SUCCESSFUL copy: opening the
picker costs nothing, an inner Choice costs nothing, and an inner refusal costs
nothing. Whether the ORIGINAL owner also pays is a lobby setting —
`copy_consumes_use`, defaulting to Glockboy's "Where are you Marcus?" — matched
on the skill name and applied by a `SpendAbilityUse` operation against the card
that player is holding. The printed definition is never touched; it is a
per-match rule and nothing assumes Glockboy is the only entry.

### The mod
A mod is "played" by reaching the rack, so `_arm_mod` is the hook — the same
one Paczka and Obóz Harcerski use. It cannot ask its question there (arming
happens deep inside resolving a selection, with no way to carry an answer
back), so it RAISES the question and waits: `pending_ability_copy`, exactly the
shape the Ice Block window and the movement decision already use. The card then
stays racked, which is the ordinary mod lifecycle.

The chooser is the seat that owns the slot the card landed in.

### Changed
- `data/characters.json` — Herold / Messenger, one use.
- `data/cards.json` — the Herold mod, count 1.
- `config/settings.py` — `copy_consumes_use`.
- `engine/effects.py` — `COPY_ABILITY`, `COPY_CHOICE_KEY`,
  `copyable_abilities`, `consumes_original_use`, `_copy_ability`,
  `SpendAbilityUse`.
- `engine/game_state.py` — `PendingAbilityCopy`, `_open_ability_copy`,
  `_mod_owner_seat`, `_copy_ability`, `_op_spend_ability_use`, the arm hook and
  the snapshot field.
- `engine/commands.py`, `engine/events.py` — `CopyAbility`;
  `AbilityCopyOpened`, `AbilityCopied`.

### Verification
- **1873 pass, 0 fail.** Stage 41b ended at 1841.
- 33 new tests in `tests/test_stage42_herold.py`, and the four core rules were
  MUTATION-TESTED: ignoring the exception list fails 3, letting Messenger offer
  itself fails 1, spending Messenger on a refused copy fails 2, and never
  raising the mod's question fails 9.
- Two of the new tests were strengthened after writing: one had no control (it
  now proves the refusal was the START rule by making the same copy succeed
  afterwards), and one could silently skip half of itself.
- 8 existing tests updated: the printed mods deck is 14 cards over 9 titles,
  the character deck 11, and "every mod declares a rule" now accepts an ABILITY
  as a rule — Herold is the first mod whose rule is not a passive.

### Limitations
- NO INTERFACE. The picker is a `ChoiceRequired` of kind ``ability`` carrying
  the character name, skill, text and an art key in ``data`` — everything an
  Ability Library card needs — but nothing draws it yet, so today it renders
  through the generic option list. The mod's `pending_ability_copy` has no
  panel either.
- No art files were added. `Messenger` and `Herold` are independent lookup
  names in different decks, so neither can pick up the other's image, but both
  currently resolve to nothing.
- Copying Liskowy Konkurs gives HEROLD the extra turn, and copying Przerwanie
  Systemowe gives Herold the veto. That follows from "the copier is the actor"
  and is almost certainly right, but it is the class of ability where the brief
  warns about identity-dependent semantics and is worth a ruling.
- The mod's chooser is the slot's faction, defaulting to the first hunter when
  no hunter holds the turn. A table that wants a specific hunter to decide has
  no way to say so.

## Stage 42a — The Herold lobby tab
**Date:** 2026-08-13

Stage 42 shipped `copy_consumes_use` as a `SessionConfig` field with no way to
set it. The brief's §4 is mandatory language — "the lobby MUST contain a
configuration determining which character abilities consume the original
character's use" — so a field only a test could reach did not meet it.

### The tab
A new `copy` tab labelled "Herold": one row per borrowable ability, each a
two-answer choice (Zachowuje / Traci). THE SAME named-option machinery the
variants and rules tabs use, so bump, clamp, reset and merge all keep working
on an int and this is not a third kind of row. It also inherits stage 40a's
per-row bounds, so the rows walk both ways and stop at both ends.

THE ROWS ARE BUILT FROM THE CARDS, never typed out: every character and skill
that has an activated ability, minus Messenger itself, which is not borrowable
and so has nothing to decide about. A tenth character appears here the day it
is added.

The default marks Glockboy and is asserted to equal `SessionConfig()`'s, so the
lobby and the config cannot disagree about what a table that never opens the
tab is playing.

### Wired the whole way
`GameSettingsPanel.copy_consumes_use` translates indices to skill NAMES at the
boundary — the same translation every other choice row does, for the same
reason: a number would break the moment somebody reordered the tab. From there
into `SessionConfig` (hot seat), into `LobbyState` and back out of it (online).
An unknown name from another build matches nothing rather than raising.

### What was already covered
§18's other two items needed no work, which is what "integrate it into the
current lobby configuration system" should look like: Herold's PRESENCE is the
Umiejętności tab (uses 0 leaves him out) and the CARD QUANTITY is the Mody
Patusa tab. Both listed him the moment he was added to the data.

### Verification
- **1883 pass, 0 fail.** Stage 42 ended at 1873.
- 10 new tests, and both halves of the plumbing MUTATION-TESTED: cutting the
  panel out of the menu's config fails 1, and hard-coding the tab's answer
  fails 2. The end-to-end test drives the real MenuScreen, clicks the rows and
  captures the `SessionConfig` the Start button builds.
- 2 existing tab inventories updated.

### Still not done
- THE ABILITY PICKER STILL HAS NO UI. §2 and §9 ask for the Ability Library's
  card-art language; the choice carries the character name, skill, text and an
  art key, but nothing draws them, so it renders as the generic option list.
  That is the one remaining gap in this feature.

## Stage 43 — Herold is a Chest card, and only a Chest card
**Date:** 2026-08-13

A design decision reversed after play-testing. Stage 42 built Herold as a
CHARACTER with a Messenger ability, plus a Mody Patusa card carrying the same
effect. Both are gone. Herold is now one thing:

    Herold → Chest card → the existing card-play pipeline → the same effect

**THIS SUPERSEDES STAGE 42 AND 42a WHEREVER THEY DESCRIBE HEROLD AS A
CHARACTER OR A MOD.** Those entries are kept as history, not as description.

### Removed as a character
Out of `characters.json` entirely — no card, no `Messenger` skill, no ability
definition. Nothing in character selection, the lobby's Umiejętności tab, turn
order or the character panel had a Herold-specific branch to remove, because
Herold was only ever a row in that file; deleting the row removed him from all
of them at once. The Herold lobby tab's own row list is built from the cards,
so Messenger disappeared from it without being named anywhere.

### Removed as a Patus Mod
Out of the mods deck in `cards.json`, and with it the entire mod-arrival
machinery that existed only for it: `PendingAbilityCopy`, `_open_ability_copy`,
`_mod_owner_seat`, the `CopyAbility` command, the `AbilityCopyOpened` and
`AbilityCopied` events, the snapshot field and the `_arm_mod` hook. A mod is
played by reaching the rack and cannot ask a question there, which is why all
of that existed; a Chest card is played from a hand and asks questions the
ordinary way, so none of it is needed. Deleted rather than left dead.

### THE EFFECT WAS NOT REWRITTEN
`copy_ability`, `copyable_abilities`, `consumes_original_use` and
`SpendAbilityUse` are the stage 42 code, unchanged in behaviour. What changed
is how it is REACHED: an `effect` on a Chest card, resolved by `_play_card`
through the same pipeline as Dzieckorolka or Gejtos. Its questions travel the
ordinary ``choices`` road, so the borrowed ability's own multi-step selection
works with no code at all — Radar still asks for its ordered pair.

### One thing DID have to be adapted
The global "no abilities while a pawn is on START" rule used to arrive free:
Herold was a character, so `_use_ability` asked `ability_refusal` before the
handler ran. `_play_card` asks no such thing and should not, because most cards
are not abilities. So the gate moved INTO `_copy_ability`, which is the only
place that knows an ability is being borrowed.

FOUND BY TESTING THE REAL FLOW, not by reading the code: the first end-to-end
run played Herold with a pawn still in the camp and the copy went through.

### One behaviour deliberately preserved rather than reasoned afresh
The player's OWN character is still excluded from the list. That rule existed
because Herold was a character and must not copy himself; as a card it means
you cannot double your own ability. Arguably it should now change — but that
would be a gameplay change, not a refactor, so it stays as it behaved and is
flagged here for a ruling.

### Kept, because it is not character-specific
`copy_consumes_use` and its Herold lobby tab: which abilities cost their OWNER
a use when copied. That is a property of the EFFECT, not of what carries it.

### The guard that looks dead but is not
`copyable_abilities` still skips a character whose ability is `copy_ability`.
Nothing carries that as an ability any more, so it never fires — it is kept as
the one line between a future second copier and infinite recursion, and a test
asserts no card anywhere carries it, which is what makes the guard's silence
meaningful rather than accidental.

### Changed
- `data/characters.json` — Herold removed.
- `data/cards.json` — removed from `mods`, added to `chest` (count 1).
- `engine/game_state.py` — the mod-arrival machinery deleted.
- `engine/commands.py`, `engine/events.py` — `CopyAbility`,
  `AbilityCopyOpened`, `AbilityCopied` deleted.
- `engine/effects.py` — the START gate added; comments re-framed.
- `ui/settings_panel.py` — one comment re-framed.

### Verification
- **1877 pass, 0 fail.** Stage 42a ended at 1883; the drop is the 42-era tests
  that described a character and a mod, replaced by 36 in
  `tests/test_stage43_herold_card.py`.
- Driven through the REAL UI: the lobby lists Herold under Karty Skrzyni and
  not under Mody Patusa, its count steps up and clamps at 0 like any other
  Chest card; in game it is dealt into a hand, appears in that seat's chest
  cards, is played, asks which ability to copy, runs it (Piotrek skipped by a
  borrowed Jazdy), leaves the hand and reaches the discard pile.
- 11 existing deck-inventory tests updated in BOTH directions: the mods deck
  went back to 13 cards over 8 titles and the characters deck to 10, while the
  chest deck went to 18.
- New tests assert the ABSENCE of the old design directly: not a character, not
  a mod, exactly one definition, no card carrying `copy_ability` as an ability,
  and no `CopyAbility` command or `pending_ability_copy` on the state.

### Limitations
- Still no bespoke picker UI. The choice carries the character name, skill,
  text and an art key, and now renders through the ordinary Chest card choice
  prompt — which is at least the same prompt every other Chest card uses.
- The card art key is `Herold` in the chest deck. No art file was added.

---

## Stage 44 — The modal stack: one order for painting and for input
**Date:** 2026-08-14

### The report
In round 7 the Mod Patusa selection and a Chest hand-limit prompt were on
screen together. The Mod window was drawn on top; the Chest window answered
the clicks.

### Reproduced first, in the real screen
```
pending_mod_selection: True     pending_chest_choice: (3, [30014, 30013, 30011])
mod_choice.active: True         chest_choice.active: True
mod card rect <rect(602,367,225,322)>  chest panel <rect(534,292,852,497)>  overlap: True
click on the Mod card →  mod slot changed?  False
                         chest keep changed? True
```
Round 7 is where it shows because `_begin_round` calls `_open_mod_selection()`
and then `_distribute_chest_card()` in the same breath, and by round 7 the
hands are reliably at the chest limit. It is not a round 7 bug.

### ROOT CAUSE — two lists, nothing keeping them in step
`GameScreen` held the running order of its dialogs **twice**: as the chain of
`if ....active:` tests at the top of `handle_event`, and as the sequence of
`.draw()` calls at the bottom of `draw`. Neither referred to the other, so
they drifted. Mod-vs-chest was the reported drift; it was not the only one —
the Card Library was drawn *below* the movement/check decisions and the two
endings while taking input *before* all of them, and `reveal` was drawn above
`choice_prompt` while `pending_choice` took input first.

A drift between those two lists is never cosmetic. It is exactly "a window is
painted on top and the window underneath answers the click".

### THE FIX: `ui/modals.py`
One ordered list, `ModalStack`, registered in `GameScreen._register_modals`.
**Registration order IS priority IS paint order.** `draw()` walks it upwards;
`handle_event` offers the event to the topmost active modal and to no other.

    INVARIANT: the visually topmost ACTIVE modal receives input, and no other
               modal receives any.

Bottom to top: reveal · pending choice · **chest limit · MOD PATUSA SELECTION**
· card picker · Paczka · card library · movement / breakup / check decisions ·
match start · victory · pause menu.

Rules the stack enforces:
- A lower modal is **pending**: still drawn, still holding its state, not
  actionable. That is what turns two clickable windows into a queue.
- `blocking` decides whether a modal swallows everything (chest limit, picker,
  library, endings) or lets navigation through (mod vote, pending choice) —
  the pause was never meant to be a blindfold and still is not.
- A non-blocking modal lets the **wheel and middle-drag** past but never a
  **click** onto another window: a press inside its own panel, or inside any
  pending modal beneath it, stops there.
- `blocks_keyboard` is separate, so S/F/Tab keep working under the dialogs
  that always allowed them.

Two orderings collapsed into one, so two things moved: `reveal` now paints
below `choice_prompt` (a question belongs above an animation), and the
connection banner paints above the endings — "connection lost" is the one
message that must never be under a window the player can no longer resolve.

### PACZKA: a secondary window must not interrupt the phase it depends on
`_arm_mod` fired `ChestCardsRevealed` the moment **one** faction settled,
while four hunters still had to vote. Now it is deferred **in the engine**, so
every replica behaves the same:
- `_MOD_FOLLOWUP_PASSIVES` names the passives whose arrival opens a *window*.
  A mod that only changes the board (Shady) or the statuses (Sesja na PG
  variant 2) is not one and still lands immediately.
- While a selection is open the arrival is recorded on
  `ModSelection.followup_uids`; `_finish_mod_selection` emits
  `ModSelectionFinished` **first** and then rebuilds the window **from the
  rack** — a mod chosen and then replaced before the pause lifted owes nothing.
- Outside a selection (PlaceMod, Thunderfuck, Rage Quit) nothing changed.
- `followup_uids` is in the snapshot: two machines that disagree about it
  disagree about what happens the instant the pause lifts.
- `GameScreen` holds a `ChestCardsRevealed` that arrives while a selection is
  open anyway. That is the replica-side net for L19 (a reconnecting client
  replaying an old log), and it is a net, not the mechanism.

### ESC: resolve, never cancel
Esc now **answers** the active window with a valid random choice and closes
it; the queue then advances. The valid set is always the one the interface
itself would have accepted a click on — the three mods actually dealt, the
cards actually in the prompt, `choice.options` / `card_options`, the breakup
tiles, Block only while the card still has its use, Refuse only while Ice
Block has one. One option means that option; zero means Esc keeps its old
meaning rather than inventing an answer.

- The modal's **own** handler sees Esc first, so the meanings that already
  existed survive (backing out of a Block confirmation, closing the Library).
- `victory` and `pause_menu` declare no resolver. "Quit the application" is
  not a choice a die gets to make, and neither is blocking anything.
- **Cancelling is now the right button only.** `_handle_choice_event` and the
  card picker have always had it; Esc and right-click simply no longer mean
  the same thing.
- The fallback draws from a **UI-local RNG**, deliberately outside R4. It runs
  on one machine at a moment no other machine knows about; pulling from the
  shared seeded stream would leave every other replica one number behind. What
  travels is the **Command** it produces, exactly as a mouse click does.

### Changed
- `ui/modals.py` — NEW. `Modal`, `ModalStack`.
- `ui/game_screen.py` — `_register_modals` and the modal adapters; the input
  chain and the paint block replaced by the stack; `_answer_mod_choice` and
  `_choose_identity` extracted so Esc and the click share one road; the Esc
  resolvers; the held-Paczka net; `_handle_key`'s two cancel branches removed.
- `engine/game_state.py` — `ModSelection.followup_uids`, `_arm_mod` split into
  immediate and follow-up, `_mod_followup_events`, `_finish_mod_selection`
  flush, snapshot field.
- `tests/test_stage44_modal_stack.py` — NEW, 29 tests.
- `tests/test_ui.py` — three Esc tests re-pointed at the new contract, and
  three added beside them for what Esc does now.

### Verification
- **1908 pass, 0 fail** (stage 43 ended at 1877). No test was deleted; the
  three that asserted "Esc cancels" now assert "right-click cancels" and are
  joined by three asserting "Esc resolves".
- The reproduction above, re-run: the Mod slot fills and `chest_choice.keep`
  does not move.
- The round 7 scenario driven end to end: both windows up → Mod window owns
  input → chest window unclickable and still pending → Mods finish → chest
  discard becomes the owner → completed by hand → nothing left active.
- Paczka driven end to end: chosen by Piotrek, no window, all four hunters
  vote, window appears only after the phase closes, dismissed with OK.
- Esc driven across the mod selection, the chest discard, a six-option pawn
  selection (two chained questions, two presses), a one-option window, a
  multi-pending stack and an empty screen.
- **Checked in PIXELS, not flags.** Both windows reported `active` and both
  were right; the bug was invisible to a flag. The two windows are painted to
  scratch surfaces in each order and the real frame matched against them:
  6204/6204 differing pixels in the overlap match Mod-over-chest.

### Limitations
- Esc resolves the mod selection ONE step at a time — this seat's pick, or the
  hunter whose turn it is at this keyboard. That is deliberate: it is the
  active interaction, and one player's Esc must not vote for the rest of the
  table.
- `reveal` moving below `choice_prompt` and the connection banner moving above
  the endings are behaviour changes, small but real, and fall out of there
  being only one order now.

---

## Stage 45 — The board size means the meta
**Date:** 2026-08-14

### Reported problem
A board asked for 24 fields in the lobby finished on 19. The doubled positions
were eating the board's length: with `double_frequency` at 30% and seed 42 the
generator produced

    1, 2, 3a, 3b, 4, 5, 6a, 6b, 7, 8, 9, 10a, 10b, 11, 12a, 12b, 13,
    14a, 14b, 15, 16, 17, 18, 19

— twenty-four *fields*, nineteen *positions*, meta on 19.

### Cause
`make_rows()` budgeted FIELDS, not positions:

```python
cell_num += 2 if row_type == "double" else 1
```

A widened row spent two units of the board-size budget while producing one
logical position, so each 4a/4b pair shortened the board by one. `_place_tiles()`
enforced the same field budget a second time (`if len(self.tiles) >=
self.cell_count: break`), which is why the truncation survived any change to the
row list alone. Since `victory.is_at_finish()` and movement both read
`last_position`, the short board was the real board — not a labelling slip.

### Fixed
`cell_count` is now a count of POSITIONS and therefore the number the meta
stands on. `make_rows()` emits exactly one row per position and the field budget
in `_place_tiles()` is gone. `double_frequency` now changes how many FIELDS the
board holds and never how long it is:

    24 @ 0.0 → 24 tiles, 24 positions, meta 24
    24 @ 0.3 → 31 tiles, 24 positions, meta 24
    24 @ 1.0 → 47 tiles, 24 positions, meta 24
    20 @ 0.3 → 25 tiles, 20 positions, meta 20

No post-hoc `+N` correction: the budget itself carries the meaning, which is why
it holds for every size and every frequency rather than for the cases somebody
remembered to offset.

The final row is still forced to `single`, so the meta is never half of a pair —
checked to frequency 1.0, where every other row widens. The frequency draw
happens on every row including the last, so the RNG stream depends only on the
board size and not on where the doubles fell; determinism is preserved.

### Changed
- `board/board.py` — `make_rows()` loops over positions instead of accumulating
  a field budget; the `len(self.tiles) >= self.cell_count` truncation removed
  from `_place_tiles()`; `cell_count` documented as the logical length; new
  `meta_number` property ("which number is the meta" was the question the lobby
  setting answers, and it used to have a different answer from the one chosen).
- `tests/test_board.py` — four assertions re-pointed from tiles to positions;
  new block "logical positions vs physical fields" (7 tests, incl. the reported
  4a/4b + 14a/14b case and a 5×3 size/frequency sweep).
- `tests/test_engine.py` — `test_positions_and_fields_are_different_counts`
  re-pointed; lobby-size-to-meta end to end (3×3); two replicas from one config
  agreeing field for field.
- `LLM_Instructions.txt` — "POSITIONS VS FIELDS" corrected (it said `cell_count`
  counts fields) and "THE BOARD SIZE IS A LOGICAL LENGTH" added.

### Untouched deliberately
Nothing downstream needed changing, and that is the point of the positions layer
existing: movement, `route_between()`, victory, the a/b choice prompt, rendering
and undo already worked in positions or in tile indices. Multiplayer needed
nothing either — the board still reduces to `(board_cells, seed,
double_frequency)` and both peers rebuild it locally. Boards are now physically
longer for the same lobby number, which is correct and was the ask.

### Verification
- **1940 pass, 0 fail** (stage 44 ended at 1909; +31 tests, none deleted).
- `verify_spacing()` is `None` across the size × frequency sweep, so the longer
  roads did not reintroduce the stage-2 overlap.

### One pre-existing test bug found and fixed
`test_nothing_grows_inside_the_starting_camp` hand-copied the camp keep-out box
as ±80/±90 while `camp_bounds` promises ±86/±86 — it asserted four pixels of
clearance nobody guaranteed. On the pre-stage-45 code **58 of 300 seeds already
violate it**; the four hard-coded seeds simply missed the gap. The longer boards
shifted the decor RNG and landed seed 42 on it. The test now asserts
`model.camp_bounds` — the generator's own contract — verified clean across 400
seeds. The generator was NOT changed for this: it was a test asserting a promise
that was never made.

---

## Stage 46 — Kingmaker: the role changes seats
**Date:** 2026-08-14

### Goal
Give Gamechanger's hunter face a rule. `Kingmaker` had a title, a text and a
presentation since stage 28 and `"effect": {"type": "manual"}` underneath —
it announced itself, said what the table should do by hand, and did nothing.

Its printed text:

> Oprawca i Piotrek zamieniają się rolami, tożsamość Piotrka jest odkryta

The hunter who plays it and the current Piotrek **exchange roles**. The new
Piotrek then picks a **new** colour; the old one is published on the way past.

### The short version
Almost nothing was built. Alter Ego (stage 28) had already built the hard half
— the machinery for handing the hidden identity back — and stage 45's brief
asked, correctly, that it be reused rather than duplicated. What was missing
was moving the ROLE, and the role turned out to be one field.

New code, in total: one Operation, one handler, one executor, one event.

### Implemented

**1. `swap_roles`, and where it lives.**
`cards.json`, inside the Gamechanger presentation's `hunter` variant:

```json
"hunter": {
  "title": "Kingmaker",
  "text": "Oprawca i Piotrek zamieniają się rolami, ...",
  "effect": { "type": "swap_roles" }
}
```

Three lines changed in the data file, and that is the whole of "Kingmaker is
the hunter-side variant". `_after_draw` already transformed the card by role
(`presentation.type == "role_reveal"`) and `_variant_effect` already carried a
variant's own effect across — the stage-28 comment there says in as many words
that when Alter Ego and Kingmaker get a rule it will be a JSON entry and not a
change in that method. It was.

Piotrek's copy still transforms into Alter Ego with `swap_identity`. Neither
face knows the other exists.

**2. The role IS the character card.**
`Player.role` is derived from `character.is_piotrek`; `players/roles.py` exists
precisely so that "is this player Piotrek" is asked once. So the exchange is
literally:

```python
piotrek.character, challenger.character = challenger.character, piotrek.character
```

and everything that reads the role moves with it, none of it touched:
`piotrek_seat`, `seat_order` (the every-third-slot cadence), the chest limit,
the Mod Patusa factions, the win conditions, `swap_identity`'s own "only
Piotrek" refusal, the right-hand panel, the ability button and the notepad.
**There is deliberately no second "who is Piotrek" flag to fall out of step.**

**3. The identity question is Alter Ego's, unmodified.**
The plan is two operations:

```python
Plan((SwapPiotrekRole(hunter_seat=actor.index), RequestIdentitySwap()), ...)
```

and then the existing five steps run untouched: the colourless flag stops the
table → the authority answers through `victory.review` with `RevealIdentity` →
the notepad is wiped down to the revealed colour → the room re-sends
`identity_required` to one peer → `set_identity` → `FinishIdentitySwap`.

Not one line of that flow changed. The new Piotrek is asked with the same
message, the same overlay and the same shortened pawn list Alter Ego produced,
because the room asks `state.piotrek_seat` — and by then that is him.

**THE ORDER OF THE TWO OPERATIONS IS THE DESIGN.** The role moves first. Raise
the pause first and `victory.review` answers it about the man who is about to
stop being Piotrek, and the room asks the wrong peer for a colour.

**4. The secret is moved without being read.**
`SwapPiotrekRole` swaps `secret_pawn` between the two seats. On a replica that
moves `None` onto `None`; on the authority and on the outgoing Piotrek's own
machine it moves the real colour. The operation never names one, so it builds
identically everywhere (N72), and the colour it moved is given up a moment
later by `RevealIdentity` and replaced by the new Piotrek's own choice. **It is
never inherited** — that is the shortcut the whole shape of this exists to
avoid.

**5. What travels with the role, and what does not.**

| | Travels | Why |
|---|---|---|
| `character` | yes | it *is* the role |
| `skill` (Umiejętność Piotrka) | yes | belongs to the Piotrek seat; the hunter had none, so the outgoing Piotrek correctly ends with none |
| `secret_pawn` | moved, then given up | see above |
| `marks` (the notepad) | **no** | it is the player's own working-out |

The notepad staying put is a rule, not an omission. Swapping it would put one
player's private deductions on another player's screen — from the one card in
the game whose entire job is to move hidden information about carefully.

Ability CHARGES travel because they are counted on the physical card
(`cards/base_card.py`: "hand it to somebody else and the remaining uses go with
it"). Kingmaker is deliberately not an exception to a rule the project already
states. The new Piotrek may exchange the skill through the ordinary
Umiejętności deck on his panel, like any Piotrek.

**6. No UI was written.** `CharacterPanel` has always branched on
`player.is_piotrek`, so the identity badge, the skill card, the Umiejętności
deck section, the ability button's source and the hunter pawn grid all follow
the role by themselves. Six UI tests exist to prove that nothing was
special-cased.

### Two safety holes closed on the way past

**`piotrek_seat` is now in the snapshot.** Before this stage the role could not
move, so nothing needed to notice it moving — and nothing did. `piotrek_name`
is a character TITLE and the set of titles in play is identical before and
after an exchange; `has_character` is true on both seats either way;
`ability_uses` is keyed by seat. Two machines that disagreed about who Piotrek
is would therefore have agreed about **every field in the fingerprint** while
disagreeing about the turn order, the win condition and every "only Piotrek"
rule in the game. A seat number, never a colour.

**`can_undo` now refuses while `awaiting_identity`.** A checkpoint records
hands, piles, charges and a listed set of scalars — it does not record who
holds which character card, and `secret_pawn` was never in it either. `UndoMove`
is exempt from `_phase_refusal`, so without this an exchange could be torn in
half from underneath. **This also closes the same pre-existing hole under Alter
Ego**, where an undo mid-swap already lost a secret that had been given up.
`SwapPiotrekRole` additionally closes the window outright, the way
`GrantExtraTurn` does — Kingmaker is not undoable, and says so in one place.

### A conflict in the brief, decided and recorded
The brief's HIDDEN INFORMATION section says the former Piotrek's old identity
"must not become visible merely because the role changed". The card's own
printed text — preserved verbatim in the same brief — says
*tożsamość Piotrka jest odkryta*: it **is** revealed.

Resolved in favour of the card text, for two reasons. The reveal is a RULE of
this card rather than a side effect of the mechanism, and it is exactly what
the wording describes. And suppressing it is not cheap: `RevealIdentity` is
what clears the secret and what makes `swap_forbidden_pawn` (= `eliminated_pawns[-1]`)
mean anything, so a silent exchange would need a second path for giving up an
identity — the second identity-selection system the brief explicitly forbade.

The brief's requirement is honoured in the sense that matters: nothing leaks
*accidentally*. The played command carries no colour, the new colour never
travels, the outgoing Piotrek's replica holds no secret afterwards, and no
machine learns anything the card does not say out loud.

**If the intended rule is a silent exchange, this is the decision to revisit —
it is contained, and the tests name it.**

### Changed
- `pedzacy_piotrek/data/cards.json` — the Kingmaker variant's effect.
- `engine/effects.py` — `SwapPiotrekRole` operation; `@effect("swap_roles")`.
- `engine/game_state.py` — `_op_swap_piotrek_role` + registration;
  `piotrek_seat` in `snapshot()`; `can_undo` refuses during a swap.
- `engine/events.py` — `RolesSwapped(from_seat, to_seat)`.
- `tests/test_final_chest_cards.py` — `deal_gamechanger` helper (and `alter_ego`
  re-pointed at it); the obsolete `test_kingmaker_is_playable_and_does_nothing`
  replaced by two variant tests; 24 new Kingmaker tests.
- `tests/test_final_chest_cards_sync.py` — 12 new multiplayer tests.
- `tests/test_ui.py` — 6 new panel tests.
- `LLM_Instructions.txt` — "ALTER EGO..." section extended into
  "...AND KINGMAKER"; CURRENT IMPLEMENTATION and KNOWN LIMITATIONS updated.

### Untouched deliberately
`victory.py`, `server/room.py`, `net/`, `ui/hud.py`, `ui/layout.py`,
`ui/game_screen.py`, `engine/turn_order.py`, `engine/setup.py`. Kingmaker adds
**no command of its own** — it replays from the `play_card` that caused it plus
the two authority commands Alter Ego already used, which is why it cannot
desync and why a reconnecting player reaches the same table from the log.

### Verification
- **1982 pass, 0 fail** (stage 45 ended at 1940; +42 tests, one obsolete test
  replaced by two).
- Confirmed end to end against the real in-process server: role moves on every
  replica, skill follows, old colour published and cleared, only the new
  Piotrek's peer is asked, old colour excluded from his options, new colour
  absent from the command log, fingerprints identical, play resumes.

### Known limitations
- **Hand sizes do not re-deal.** Piotrek opens with five movement cards and a
  hunter with three; after an exchange each player keeps the hand they were
  holding and converges on the new size through the ordinary end-of-turn refill
  (`_refill_movement_hand` reads `setup.starting_hand_size`, which reads the
  role). Nothing is wrong, but the new Piotrek is briefly short. Left alone
  because a forced re-deal mid-match would move cards between hands and piles
  for reasons no card printed.
- **Two Kingmakers cannot meet.** One copy is printed, and a second exchange
  while one is running is refused (`identity_swap` is already set). Sequential
  exchanges are fine and tested.
- **The notepad does not come back.** A player who is Piotrek and later becomes
  a hunter again keeps the marks he had before — they were never cleared — but
  he made no notes while he was Piotrek. Correct, and worth knowing.

---

## Stage 47 — Card backs are artwork
**Date:** 2026-08-14

### Goal
Replace the code-generated card backs with five supplied pictures, and — the
larger half of the task — make the backs **asset-driven**, so that replacing
one later is a file and a line rather than a hunt through rendering code.

### What was there before
`CardRenderer.back()` painted a bound cover procedurally: a rounded gradient in
`theme.deck_colors[deck_id]`, a brass frame, four corner diamonds and a diamond
emblem. The deck's identity reached it only as a **colour**, passed down from
`ui/hud.py::_deck_section`, so the five decks differed by hue and by nothing
else.

The good news on inspection: there is exactly **one** card-back rendering path
in the whole game, and `_deck_section` is shared by the left column and the
character panel, so all five decks go through it. Hands, the card library, the
reveal overlay and the mod rack draw fronts only. No hunt was needed.

### Implemented
**The five pictures.** `assets/card_backs/`, named after their deck ids:
`movement.png` (Karty Ruchu — blue, winged shoe), `mods.png` (Mody Patusa —
green, gear and puzzle piece), `chest.png` (Karty Skrzyni — orange, treasure
chest), `piotrek_skills.png` (Umiejętności — purple sigil), `characters.png`
(the character-**exchange** deck — gold, three profiles). The uploads' Polish
filenames made the mapping unambiguous and the artwork agrees with it.

**One table, and only one.** `settings.CARD_BACKS` maps deck id to file name.
Nothing in `render/` or `ui/` names a card-back file, and a test greps both
folders to keep it that way.

**`render/card_back.py`.** `CardBackLibrary`, deliberately shaped like
`CardArtLibrary`: lazy per-deck loading, cached surfaces, injectable folder and
table, and `None` on every failure path. It is a **table, not a scan** — five
decks are five constants, and scanning would have meant a second name-folding
convention for names that already exist.

**`CardRenderer.back()` branches on its first line**, the way `face()` has
branched since stage 30: picture present → `_picture_back`, otherwise the drawn
cover. `_picture_back` cover-scales through the same `_cover` a Signature face
uses, rounds the corners to the card's radius, and adds `BACK_HOVER_LIGHT ×
brightness` for the deck-panel hover — additive, since a picture has no base
colour to `lighten`. Cached under `("picture_back", deck_id, size, step)`.

**One line in the UI.** `_deck_section` already held the deck object, so
`deck_id=deck.id` on the existing `draw_pile` call was the entire change to
`ui/`.

### Architecture notes
`card_art.py`'s private `_load` was promoted to a module-level `load_image` and
is now shared by both libraries. One loader, one failure policy — a picture
cannot become able to crash the game by arriving through a different door.
`CardArtLibrary._load` delegates to it; its behaviour is unchanged.

The two systems stay strictly separate: different folders, different modules,
different addressing (a card art file is found by the CARD's name, a back
belongs to a DECK), and a test asserts neither library can resolve through the
other's directory. `DECK_CHARACTERS` is a **deck**, not a role — nothing about
hidden information is touched, and a picture on a face-up pile could not carry
any in the first place.

The drawn back survives as the **fallback**: a missing or corrupt file, and a
clean checkout with no binary assets, both get the bound cover in the deck's
theme colour. That is the project's standing rule for `assets/`, and it is why
the `color` parameter stays on `back()`.

### Changed
- `pedzacy_piotrek/assets/card_backs/` — five new pictures + `README.md`.
- `pedzacy_piotrek/config/settings.py` — `CARD_BACK_DIR`, `CARD_BACKS`.
- `pedzacy_piotrek/render/card_back.py` — new; `CardBackLibrary`.
- `pedzacy_piotrek/render/card_art.py` — `_load` promoted to `load_image`.
- `pedzacy_piotrek/render/card_renderer.py` — `backs` library injected;
  `back()` branches; `_picture_back`; `BACK_HOVER_LIGHT`; `deck_id` threaded
  through `draw_back` and `draw_pile`.
- `pedzacy_piotrek/ui/hud.py` — one line: `deck_id=deck.id`.
- `pedzacy_piotrek/assets/README.md` — the `card_backs/` row and the
  front-versus-back distinction.
- `tests/test_card_backs.py` — new, 25 tests.
- `tests/test_visual_style.py` — the themed-colour test renamed to say it now
  covers the fallback; a new test that the decks on screen show the artwork.
- `LLM_Instructions.txt` — "CARD BACKS" section; module map; CURRENT
  IMPLEMENTATION.

### Untouched deliberately
Every deck mechanic, `cards/deck.py`, `engine/`, `net/`, `ui/layout.py`, the
card library, the hand fan, and the whole Signature Card path. This stage moves
no card and changes no rule; `deck_id` was already available at the one call
site that needed it, which is why the UI change is a single argument.

### Verification
- **2008 pass, 0 fail** (stage 46 ended at 1982; +26 tests — 25 in
  `test_card_backs.py`, 1 in `test_visual_style.py`).
- Rendered headlessly and inspected: all three table decks and both of
  Piotrek's piles show their own back, five distinct pictures, correct
  silhouette and rounded corners, card fronts unchanged.

### Known limitations
- **The files are large.** ~2.7 MB each, ~13.5 MB for the set, at ~1060×1490 —
  the same class as the shipped card art, and far more resolution than a pile
  a few hundred pixels wide will ever use. Shrinking them is safe and is noted
  in `card_backs/README.md`; left alone here because the task was to use the
  supplied artwork, not to re-encode it.
- **Replacing a back needs a restart.** Pictures are loaded once and cached.
  `CardBackLibrary.refresh()` exists for a live reload; nothing calls it.
- **A discard pile shows a front, not a back.** Unchanged behaviour — the top
  discard is face up, which is what it always was.

## Stage 48 — The enlarged hover preview
**Date:** 2026-08-14

### Goal
Make an ability readable on a small screen. Hovering one must leave the card
exactly as it is and show a **larger copy beside it** carrying the title and
the description — and cards without artwork, which have the same problem in the
same kind of slot, must behave the same way.

### What was there before
Stage 30 gave every card a hover **reveal** — title lifted, description faded in
underneath a darkened picture — and defaulted it from `highlighted`, so every
panel in the game got both states without an edit. `CharacterPanel.draw` passed
`highlighted=hovered` on the ability card and inherited it.

In the hand that default is exactly right: a hand card is 200-300 px tall. In a
**slot** it is exactly wrong, because it puts *more* text in the *same* small
rectangle. At 1280×760 the ability card is 99×142 and a mod slot card is 74×107.
On an illustrated ability it was worse than useless: the reveal veiled the
artwork, so hovering an ability to read it replaced the ability with a darker
version of itself.

Inspection found one enlargement precedent worth reusing — `RecentlyPlayed`,
which grows a hovered card by `HOVER_SCALE = 2.15`, **repaints** it at the
enlarged size through `CardRenderer.quantised` rather than zooming the small
raster, and nudges it away from the screen edge. Its scale factor and its
repaint-don't-zoom discipline are both borrowed here rather than re-invented.

### Implemented
**`ui/card_preview.py`.** `CardPreview`: a one-request-per-frame collector.
A panel calls `ctx.preview_card(...)` while drawing a slot; `GameScreen.draw`
calls `card_preview.draw(ctx)` once, after every panel and before the dialogs.
**The draw consumes the request**, so a frame on which nothing asks draws
nothing and there is no state to unwind — a panel that stops asking, or stops
being drawn, stops getting a preview on the very next frame. Last caller wins;
two slots cannot be under one cursor.

**The presentation is borrowed, not rebuilt.** It draws through
`cards.draw_in(..., highlighted=True, reveal=1.0)` — the same face and the same
reveal the card-art hover has painted since stage 30. There is no second card
renderer. The size goes through `quantised`, so the face is repainted at the
size it will be seen.

**Geometry in `Layout`, like every other rectangle.** `hover_preview_bounds`
(the content area — deliberately *not* the whole window, so a preview never
reaches down over the hand fan), `hover_preview_gap`, `hover_preview_size`
(2.15× the anchor, floored, capped, then clamped by what the window can show,
card proportions kept exactly) and `hover_preview_rect` (right when it fits,
left when it does not, clamped inside the bounds when neither side has room).

**Which slots ask.** The ability card **always**, artwork or not — it pins
`reveal=0.0` and is the one place that does, because an ability is what that
panel is about. A mod-rack card **only when it has no artwork**: a Signature mod
keeps the stage-30 hover, which works and was not to be disturbed.

**`CardRenderer.has_art`.** How a panel asks. Stage 30's rule still holds —
the artwork branch lives on `face()`'s first line and `ui/` never writes
`if card.art`. This is a *different* question ("which hover affordance does this
slot offer?"), and routing it through the renderer keeps one opinion about what
counts as artwork. A test greps all of `ui/` for `.art.surface(` and
`CardArtLibrary`.

### Architecture notes
The gap between the card and its preview is **load-bearing twice over**. The
rects never touch, so moving the cursor onto the preview simply leaves the card
and ends the hover — overlapping rects would give the cursor somewhere to sit
where the hover is simultaneously on and off, which is the classic tooltip
oscillation. And the shadow is **sized from the gap**: a fixed spread reached
back across it and darkened the right-hand six pixels of the very card being
previewed by one unit per channel. Invisible to the eye, caught by the
byte-for-byte test, and a real violation of the promise the feature makes.

`HudContext.preview` is optional and every call goes through
`ctx.preview_card`, a no-op without one. Plenty of screens and tests build a
context of their own; a panel that assumed a preview was always there would
take all of them down.

The right-hand character column **always** takes the left-hand fallback — there
is nothing to its right but the window edge — so the fallback is the path that
ships, not a defensive branch nobody walks.

### Changed
- `pedzacy_piotrek/ui/card_preview.py` — new; `CardPreview`, `PreviewRequest`.
- `pedzacy_piotrek/ui/layout.py` — `HOVER_PREVIEW_*` constants;
  `hover_preview_bounds` / `_gap` / `_size` / `_rect`.
- `pedzacy_piotrek/render/card_renderer.py` — `has_art()`.
- `pedzacy_piotrek/ui/hud.py` — `HudContext.preview` + `preview_card()`; the
  ability card pins `reveal=0.0` and requests a preview; mod slots request one
  for a card without artwork.
- `pedzacy_piotrek/ui/game_screen.py` — owns the `CardPreview`, passes it in
  the context, draws it above the panels and below the dialogs.
- `tests/test_card_preview.py` — new, 44 tests.
- `LLM_Instructions.txt` — "THE ENLARGED HOVER PREVIEW"; gesture table; module
  map; CURRENT IMPLEMENTATION; N143, N144.

### Untouched deliberately
The hand fan, the card library, `_signature_face`, the reveal itself, every
engine rule, `net/`, and the mod-slot and ability-button gestures. The hand and
the library draw cards big enough to read; the preview is for slots, and a hand
of eight would otherwise put a preview beside every card in it.

### Verification
- **2052 pass, 0 fail** (stage 47 ended at 2008; +44, all in
  `test_card_preview.py`).
- `tests/test_card_preview.py`: **44 pass**.
- Rendered headlessly at 1920×1080 and 1280×760 and inspected: the ability card
  keeps its artwork and its resting title, the enlarged copy appears to its left
  fully on screen, and nothing else on the frame moves.
- Geometry asserted at 2560×1440, 1920×1080, 1600×900 and 1280×760 — the
  preview is inside the bounds, clear of the hand, clear of its own card, and at
  least 1.8× its height at every one.

### Known limitations
- **The preview can cover the turn bar or the player strip on a short window.**
  It is centred on its card and then clamped, and on a 760 px window a preview
  of the ability card is taller than the room above the board. It is transient
  and covers nothing that must be clicked to answer it, but it is not free.
- **No fade.** The preview appears and disappears on the frame the hover starts
  and ends. `RecentlyPlayed` eases its enlargement; this does not, because an
  animated size would repaint a face per step for a hover that is binary.
- **A mod with artwork still has no readable hover.** It keeps the stage-30
  reveal, which at 74×107 is no more readable than it ever was. That is the
  brief's line, not an oversight — but it is the obvious next thing to ask
  about, and moving it is one condition in `ModPanel.draw`.

---

## Stage 49 — The character has a face, and the ability has its own name
**Date:** 2026-08-17

### Goal
Give every character a PORTRAIT at the top of the right-hand panel, as an
asset system a non-programmer can fill in one file at a time — and fix the Card
Library, which had been titling every ability after its owner.

### What was there before
The character panel opened with a section heading, `Twoja Postać`, and a large
name. Nothing represented the character except the word.

The Card Library's ability tab built each cell's display card from the
**character** definition, so `Card.title` was `"Big D Randy"` and — because
`CardArtLibrary.keys_for` slugifies exactly that title — the artwork lookup
asked for `big_d_randy`. `assets/card_art/Granny Costume.png` had existed since
the artwork was drawn and was never once found. The tab compensated by printing
the ability's name in small type under the owner, which is the shape of a
workaround for a bug nobody had located.

The HUD was accidentally right: `CharacterPanel._ability_card` hand-assembled a
`CardDef` out of `skill` and `text`, so its title was the ability's — and its
artwork resolved for that reason alone, while silently dropping `art`, `badge`,
`uses` and the variants.

### Implemented
**`CardDef.ability_face`.** One derivation, shared. It renames a definition to
its `skill` and touches nothing else, and pins `base` to the printed card so
`printed` always leads back to the CHARACTER whether or not a variant was
applied first. A character card read under its other name — not a second card,
not a duplicated definition.

    Big D Randy  --ability_face-->  Granny Costume  --printed-->  Big D Randy

Both the Card Library and the HUD now go through it, so the two cannot disagree
about what an ability is called again.

**Order matters in the library.** The variant is resolved FIRST, under the
character's own title, because that is the key the match's variant settings use;
taking the ability face first would ask the state about a card called "Granny
Costume", which no deck contains.

**`LibraryEntry.title` stays the CHARACTER's.** `AdjustAbilityUses`,
`RestoreAbilityUses` and `GameState.ability_card` all address a card by its own
title. What the player reads is the ability; what the engine is told is the
card. `_draw_owner` therefore drops the small ability line — the face carries it
now, and printing it twice in two sizes is not a design.

**`render/portrait.py`.** `PortraitLibrary`: the portrait folder, scanned once
and cached, addressed by the CHARACTER's name through the same `slugify` and the
same `load_image` `card_art.py` uses. Flat — no deck scopes, because a character
name is unique. `placeholder.png` is excluded from the scan, so it cannot be
shadowed and nobody called "Placeholder" can adopt it.

**The fallback is an ASSET, not a drawing.** This is the difference from
`card_art`: a card without artwork has a parchment face to fall back on, and a
character without a portrait has nothing. `surface()` answers with the
placeholder, so the panel never branches and `tools/make_portrait_placeholder.py`
generates the shipped PNG rather than the renderer drawing it every frame.

**`CardRenderer.draw_portrait`.** The frame is DRAWN and the picture is an
ASSET — the well, the brass edge and the rounded clip are the game's, the image
is the file's. That split is what lets the placeholder be replaced by a
photograph without it needing a border baked in. Cover-scaled through the same
`_cover` a Signature face uses. `ui/` reaches portraits only through
`portrait()`, `has_portrait()` and `draw_portrait()`, and a test greps `ui/` for
`PortraitLibrary` — stage 30's one-opinion rule, extended to a third library.

**Portrait hover is stage 48's rule applied to a picture.** The portrait under
the cursor is untouched and a larger copy appears beside it, through the SAME
`CardPreview` collector. `PreviewRequest` grew a second payload rather than a
second class, because everything except the last line — one request per frame,
the placement, the shadow, consumption on draw — is identical. One cursor, one
request, last caller wins: the portrait hover and the ability hover cannot both
be open, and there is no state to unwind. `hover_preview_size` took an `aspect`
argument so an enlarged face is a face and not a face letterboxed into a card.

### Layout: what the portrait cost, and who paid
The right column was full. At 1280x760 a Piotrek panel had about 3px of slack
against stage 29's floor of 120px for a panel card, so a portrait of any useful
size could not simply be inserted.

**The `Twoja Postać` heading was retired.** A portrait with the character's name
under it says what the heading said, both of the owner's design references omit
it, and the band it occupied is most of what pays for the portrait on a small
window.

**Draw/discard piles are no longer the same size as the ability card.**
`Layout.right_piles` split from `Layout.right_cards`. A pile shows a card BACK
and a count — there is nothing on it to read — while the ability card carries a
Polish rules sentence and is what the readability floor is really about. So the
piles give ground first, via `PILE_SHARE = (1.0, 0.70)`: unchanged at the
reference display, 30% smaller at the tight end. This is why stage 29's floor
did NOT have to be weakened to fit the feature.

**The portrait's share itself moves.** `PORTRAIT_ROWS = (1.35, 0.72)`, in card
heights, divided among the same rows the cards are divided among — so the
portrait and the cards shrink together and neither can push the other off the
panel. A portrait worth 1.35 cards is handsome at 2560x1440 and would leave a
1280x760 ability card smaller than the mod slot stage 48 called unreadable, so
it decays faster than the cards do, exactly as `panel_scale` does for the side
columns. Where a card's height is capped by the column's WIDTH the leftover used
to go entirely to centring; `PORTRAIT_SLACK_SHARE` gives most of it to the
portrait instead.

Resulting ability-card heights (was → is): 1280x760 hunter 142→130, Piotrek
123→122; 1600x900 142→142 both; 1920x1080 155→155 both; 2560x1440 225→197 and
199→172, where the portrait is 251x267 and 219x233.

**Piotrek's identity badge stayed ON TOP**, above the portrait. §7 of the brief
lists the portrait first; `Portret_Piotrek.png` shows the badge first, and §17
makes the screenshots the layout reference. The badge has been the column's
first line since it existed and is the one thing on that screen that must never
be lost, so it kept its place and the portrait joined the column under it.

### Bug fixes
- The Card Library titled every character ability after its owner and looked its
  artwork up under the owner's name. `Granny Costume` now shows its own name,
  its own description and the artwork that was in the folder all along.
- `CharacterPanel._ability_card` dropped `art`, `badge`, `uses` and variants from
  the ability it displayed, and cached without regard to the selected variant.

### Tests
`tests/test_stage49_portraits.py`, 65 tests: the portrait library (own picture,
placeholder fallback, per-character pictures, adding and replacing a file with no
code change, name folding, the placeholder not being a character); missing
folder, corrupt file and non-image file, none of which may raise; the Hunter
panel and the Piotrek panel (portrait painted, name visible under it, identity
badge intact and not duplicated, ability card and decks still inside the panel);
hover (the portrait preview opens, the ORIGINAL is byte-for-byte unchanged
inside its frame, the preview never covers it at any of five resolutions, the
ability preview still works, and the two branches are told apart by the
proportions they paint); the Card Library across four characters whose ability
name differs from theirs; and regression — card art still resolves, the two
folders cannot reach each other, the portrait has no click handler, the ability
slot still discards, and the column fits at every resolution.

Pixel-measured wherever the claim is about pixels: solid-colour portrait files
are written to a tmp folder and the panel is sampled, because a test that only
checked a rect would pass with the portrait drawn blank — which is exactly what
a placeholder feature is most likely to ship.

### Notes
Four tests were ALREADY failing before this stage and still are; none of them is
touched by it. Three in `test_stage43_herold_card.py`, because
`settings.copy_consumes_use` still names `"Where are you Marcus?"` while
`characters.json` now gives Glockboy the skill `"Hunt for Marcus"` — a content
rename that was never propagated, and the same class of bug as the one fixed
here. One in `test_visual_style.py`, which samples a hand card and now finds a
Signature face because the six `Wejściówka` movement cards have artwork. Both
want a decision from the owner rather than a guess from a model.

---

## Stage 50 — One preview, four hover targets
**Date:** 2026-08-18

### What was wrong
Stage 49 gave the character portrait a hover that enlarged THE PORTRAIT, and
left the ability card sitting underneath it with a hover of its own.  Between
them they answered the question the player does not have — "what does this
character look like", which is visible at a glance — twice, and answered the
one they do have — "what does my ability do" — in a card 142 px tall.

The owner's own mock-ups had shown the answer all along: a large portrait and
NO ability card in the column.  Stage 49 read the brief's "[ability card]" line
as a requirement to keep the card and shrank the portrait to fit around it.

### 1. The portrait is a hover TARGET that resolves to a CARD
Hovering it now shows the ability card, full description and all, through the
same ``CardPreview`` every other slot uses.  The portrait itself is untouched
under the cursor (stage 48's rule) and keeps only its hover rim.

The permanently drawn ability card is GONE from the right-hand column.  It was
there to be hovered; the portrait is a better target for that, and showing the
same ability twice was the whole of the confusion.  The card still exists as
data, still fills the Card Library, still drives the ability button — it is
summoned rather than parked.

**The discard gesture moved with it.**  Left-clicking the ability card used to
issue ``DiscardTopCharacterCard``; that slot is gone, so the click went to the
portrait rather than being deleted.  The portrait is the character's
representation in this column now, and hovering still only previews.

### 2. ``ui/ability_cards.py`` — the lookup, in one place
``AbilityCards``, owned by ``GameScreen``, reached through
``HudContext.ability_of`` / ``preview_ability``:

    character name -> GameState.ability_card -> CardDef.ability_face -> Card

THREE things now ask this question (the portrait, the turn-order map, the
ability button), which is exactly the number at which a private copy inside
``CharacterPanel`` stops being acceptable.

VARIANTS COME FREE by starting from the LIVE card.  ``GameState`` pushes a
variant onto every live copy when it changes (``_reread_copies``), so a dealt
card's definition is already the reading this match is playing, and
``ability_face`` is taken last — the same order the Card Library uses, which
has to call ``variant_definition`` explicitly only because it builds its cells
from the printed content library.

Cached, keyed by title AND variant: a ``Card`` carries a uid, and building one
per frame under the cursor would churn the identity the drag and animation code
key on.

### 3. Every active Mod Patusa previews
Stage 48 deliberately excluded Signature mods, reasoning that their card-art
reveal already showed the description.  That was true of the mechanism and
false of the reading: the reveal happens inside a rack slot ~107 px tall on a
small window, which is the exact complaint the preview exists to answer.  A mod
in the rack is active on every player at once and is the card a table most
often needs to re-read mid-game.  No visibility question — the rack is face-up
and shared, so the preview shows what is already on screen, larger.

### 4. Turn-order circles preview their character's ability
Hit-tested INSIDE THE CIRCLE, not its bounding rect: these are drawn circles
sitting close together with arrows between them, and a square target would let
one steal the cursor from its neighbour.

NOTHING PRIVATE CAN COME BACK.  A hunter's fixed ability is printed on a card
the whole table sees and is listed in the Card Library.  Piotrek's character
card carries no fixed ability at all, so his circles resolve to ``None`` and
show nothing — his hand of skills stays exactly as private as it was.

### 5. Descriptions are FITTED, not clipped
The parchment face drew its body at a fixed fraction and let
``Renderer.draw_wrapped`` cut whatever ran past the bottom — invisible in a
thumbnail and glaring in the enlarged preview, where an ability with no artwork
lost the end of its own rules text.  It now goes through the SAME
``_description_font`` fitter the Signature face uses, parametrised by
``BODY_FRACTION`` so every card that already fit renders at exactly the size it
always did and only the truncated ones change.

### 6. Long titles wrap instead of being eaten
``_title_font`` shrank only until the longest WORD fit, and callers then kept
``wrap_lines(...)[:2]`` — so a long title was silently truncated mid-thought
with nothing on screen saying so.  A second shrink pass now chases LENGTH down
to ``TITLE_LENGTH_MIN_STEP``, and the line budget is ``TITLE_MAX_LINES`` (3).
Titles that already fit are untouched: both loops exit on the first iteration.

### Layout
Removing the ability card gave a whole card row back, and the portrait took it.
It is now 0.77–0.93 of the column's width (was ~0.55), which is finally what
the mock-ups showed.  ``character_panel`` no longer returns a ``"card"`` key —
a rectangle nothing paints is a target waiting to be mis-hit —
``ability_button_rect`` anchors to the name row, and ``right_cards`` survives as
the size an ability is PREVIEWED against and the unit the piles are quoted in.

Verified at 1280x760, 1600x900, 1920x1080, 1920x1200 and 2560x1440: everything
inside the panel, preview never covering its own target, never leaving the
content area.

### Removed
``PreviewRequest``'s second payload and ``CardPreview.request_portrait``.
Nothing requests a portrait preview any more, and dead code with passing tests
around it is worse than no code.  ``CardPreview`` gained ``previewed`` — WHICH
card was drawn — because with four targets feeding one preview, "a preview
appeared" is no longer the interesting question.

### Tests
``tests/test_stage50_hover_targets.py``, 45 tests, mostly NON-VISUAL by design:
resolution is where the bugs are, and a test that only checks a rectangle would
pass with the wrong card in it.  Covers the resolver (ability_face preserved,
variants resolved first, uid caching, unknown/ability-less characters), the
portrait previewing the ability rather than itself, every mod previewing and
following the slot as it changes, order circles resolving generically with
Piotrek revealing nothing, hovering mutating no state and emitting no Command,
and the text fitting for artwork-less descriptions and long titles.

UPDATED, not deleted, with the reversal documented in each docstring:
- ``test_a_slot_card_with_artwork_keeps_the_hover_it_already_had`` asserted an
  illustrated mod must NOT preview.  Reversed by requirement 2.
- stage 49's portrait-enlargement test now asserts the preview is card-shaped
  and NOT the picture.
- stage 49's "clicking the portrait does nothing" now asserts it discards,
  because the gesture moved there.
- ``ability_slot()`` in ``test_card_preview.py`` points at the portrait; every
  claim in that file is unchanged, only the rectangle moved.
- the preview's size and type-ink comparisons are made against ``right_cards``
  and an off-screen face at slot size, because the target is now a picture with
  no type on it — and the placeholder's brass bust sits within tolerance of the
  description colour, so the old on-screen comparison would have counted it.

### Notes
The same four tests were already failing before this stage and still are:
three in ``test_stage43_herold_card.py`` (``settings.copy_consumes_use`` names
``"Where are you Marcus?"``, ``characters.json`` says ``"Hunt for Marcus"`` — a
content rename never propagated) and one in ``test_visual_style.py`` (a hand
card is now a Signature face because the ``Wejściówka`` cards have artwork).
Both still want an owner's decision rather than a guess.
