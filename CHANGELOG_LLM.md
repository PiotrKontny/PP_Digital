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
