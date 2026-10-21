# Median XL Capybara Editor (MXL Capybara Editor.exe)

`MXL Capybara Editor.exe` is the MXL Item Collector with an offline item creator/editor and a character editor added,
named Median XL Capybara Editor (window titles, capybara icon). It sits next to the original `MXL Item Collector.exe`
and uses the same `_internal` folder and the same settings / data folder; the original exe is untouched.

## What's new in the main window
- **No library**: the collector's library (collection of found uniques and sets) is removed. This build has no Library
  button, no "transfer to the library" button, and no library.json file.
- **Item Creator**: create a unique, set item, runeword, rare, magic, superior or normal item.
- **Edit item**: opens the selected stash item in the creator. "Replace item" writes the new version in its place.
- **Delete item**: removes the selected item and anything socketed in it. A backup is made first.
- **Value editing**: switches on the collector's own built-in sliders in the detail panel.

## Equipment and inventory next to the stash (main window)
- Between the stash grid and the detail panel: the selected character's equipment (paperdoll, Character / Mercenary,
  weapon set I / II) with the 15×10 inventory below it.
- Drag items between the stash, the inventory and the equipment slots, in any direction, and inside the character
  (inventory ↔ slots, slot ↔ slot, character ↔ mercenary). The page shown in the stash is where items land;
  while dragging from the character, hovering a page tab or the Shared / Character button switches to it.
- Slot rules: the item's body location, a two-handed weapon needs the other hand empty, mercenaries use helm,
  armor and weapons. Equipping an item whose requirements aren't met asks first.
- Click an item to see its tooltip. Red cells = requirements not met, as in the stash.
- Each move writes the files through the safe writers (game closed, verification, backup). If the second write
  fails, the first file is put back as it was. A character file changed on disk since it was read (game, Character
  window) is never overwritten: it is reloaded and the move has to be done again.

## Character window (Character button)
Laid out like the old Hero Editor (Mastadex, ZonFire99): classic grey frames, Times New Roman, and the tab row at the
bottom of the window (Stats, Inventory, Waypoints, Quests; Alt + first letter switches tab), with the Hero Editor's own
SAVE, RELOAD and CLOSE buttons (normal / hover / pressed images and the window icon, taken from Hero Editor.exe 1.04;
mxl_he_art.py) and the line "Glory to the real Heroes ZonFire99 and Mastadex". A "Dark mode" box right of the tabs switches the
Stats, Waypoints and Quests tabs and the tab row to dark colours (remembered in settings as hero_dark_mode); the Inventory
tab uses the collector's own dark theme in both modes. SAVE applies the
changes of every tab at once; CLOSE asks to save unsaved changes.
- **Stats**: Name (with "Copy as"), Class, Level, Experience, Characteristics (Strength, Dexterity, Vitality, Energy,
  stat and skill points left), Constitution (life, mana, stamina current / max), Gold (person, stash), Character Type,
  Character Difficulty, file info. Only the level can be changed: raising it works like the game (experience, 5 stat
  points and 1 skill point per level, life / mana / stamina per level from charstats); lowering it is refused.
- **Inventory**: paperdoll of the character or the mercenary (weapon sets I / II) and the 15×10 inventory. Drag items
  between slots and inventory; To stash / From stash, Edit…, Create here…, Delete.
- **Waypoints**: Normal, Nightmare and Hell side by side, each a tree "<Mode> Mode Waypoints" > Act > waypoint with
  check boxes; a mode or act box ticks everything under it. Hell also lists Median XL's Hell-only waypoints
  (Duncraig, Tran Athulua, Island of Skartara, Caldeum, Torajan Jungles, Vizjun, Silver City, Túr Dúlra).
- **Quests**: the same three trees. Ticking a quest gives its reward in that difficulty: Den of Evil and Radament
  +1 skill point each, Izual +2 skill points, Lam Esen's Tome +5 stat points, The Golden Bird +20 life (unticking a
  quest ticked here takes it back). Quests completed in game are locked (grey box) and never changed. Act
  introductions / travel flags follow, and finishing Eve of Destruction unlocks the next difficulty.
- Saves are rebuilt section by section (byte-identical when nothing changes), with the file size and checksum
  recomputed, then checked and written through the collector's safe writer (game closed, verification, backup).

## Angelic items
- "Angelic" in the quality list (above Rare) lists the 17 Angelic-tier bases of Median XL Sigma (Full Helm (Angelic),
  War Sword (Angelic), ...). They are not in the normal drop pool, so they are made as rares on those bases, with the
  rare affixes that can spawn on them, including the Angelic-only ones; item level 98+ (their base level).

## Mystic orbs and other special misc items
- Normal quality now lists every spawnable misc item under the name the game shows, including items with
  multi-line names that were missing before: unique mystic orbs ("Kara's Trinket — Mystic Orb"), Explorer's Caches,
  cube reagents, quest scrolls and the like. Items that would still share a name get their item code added.
- Mystic orbs show their effect, read from Median XL's mysticorbs table: in the list ("Mystic Orb: +5 to Strength")
  and under the preview, with the most orbs of that kind one item can take. The filter box has a "Mystic Orb" entry.
  The orb itself is stored like the game's (a simple item); its effect is applied when it is cubed into an item.
- Mystic Dyes are listed by colour ("Mystic Dye: Color: Light Blue", "Mystic Dye: Color: Rainbow"), with a
  "Mystic Dye" entry in the filter box. Other items that share a name (cycles, Scrolls of Enchantment, crafting
  scrolls, Explorer's Caches, riftstones, ...) are told apart the same way, by their in-game description, and the
  preview shows that description under the tooltip.

## Import a character from median-xl.com (Import character… button)
1. Open the character page (median-xl.com/char/<name>) in your browser while logged in and save it with
   "Save as… → Web page, HTML only" (or complete).
2. Click **Import character…**, pick the saved .html, and give the new character a name.

The new character is built from the character you have selected, or any other save in the folder, so it has the same game version.
- Class, level, base attributes (strength/dexterity/vitality/energy from the export), skill points per skill.
- Quests: every quest and waypoint of the difficulties where the export has quests done, along with their rewards.
- All items on the page, equipped, inventory, belt and stash, copied **exactly as shown** (the user's choice; this
  is the one place where the legit-only rule does not apply). Each item keeps its base, quality, unique/set/runeword,
  magic affixes, picture, ethereal flag, base defense and sockets with their socketed items, and its stats are read back from the tooltip lines
  (mxl_statparse). Socketed items' stats are taken off the parent's, so nothing counts twice. Container and shrine
  vessel counts are stored too.
- Stash items go to page 1 of the new character's stash. .key/.map/.ma files are copied from the template.

A report window lists everything that could not be copied exactly:
- quests the quest log doesn't have (Sigma's Inquisitor / Justicar);
- skill or attribute points beyond what the level and quests give (they are set as exported);
- descriptive lines that are not stats.

Not copied: mystic-orb bookkeeping (the orbs' stats are in the item), rare items' real names (the page shows only
the base name), the mercenary (not on the page).

## Legit-only rules
Every value comes from the game tables and stays inside the ranges the game rolls:
- affixes, innate (automagic) affixes, rare names and Superior bonuses are limited to what can spawn on that base at that item level;
- set and unique items have no innate affix and get the game's durability multipliers;
- runewords only go in bases with the right item type and number of sockets; their runes are socketed automatically;
- ethereal follows the game's rules: defense ×1.5 stored, durability halved, never on set items.

Every created item is read back with the collector's own reader and checked against its range rules
before it is written. Writes go through the collector's safe writer: the game must be closed, the file is checked
after writing, and a `.bak` backup is made once per session.

## Editing items outside the rules
Items are opened with Edit item exactly as they are, even when they break the game's rules (hacked or
out-of-range values, Sigma upgrades the editor does not model): every stat the item's mods don't give, or give
outside their range, is kept as it is and listed above the sliders, and the status line warns instead of blocking
"Replace item". Moving a slider changes only that value. Tempered (quality 9) items open too (name only).

## Crafted items
- "Crafted" in the quality list. The list shows every base a crafting recipe takes (Angelic bases included):
  shrine-crafted weapons and armor, amulets (alchemy oils).
- Pick the base, then the shrine (or oil) recipe ("Abandoned Shrine (10) + Oil of Intensity: +72-120% Bonus to
  Attack Rating; ..."). The item is rolled once like the cube does: a name and 4 rare affixes (1-3 prefixes,
  1-3 suffixes, weighted by the game's frequencies, one per group) and every value.
- Then everything can be changed within the game's values: the name, each affix (only affixes that can spawn on
  that base at that item level), the recipe, and every value with its slider (the recipe's ranges and the
  affixes' ranges). "Reroll item" rolls a new name, affixes and values on the same base and recipe; Max / Random /
  Min set only the values.
- Edit item works on crafted items. The recipe and the affixes are found from the item's stats (from its affix ids
  when it has them; for an imported copy they are worked out from its stats and its Prefixes / Suffixes
  counters). Whatever they don't explain (shrine blessing, corruption, other upgrades) is kept on the item as it
  is and listed above the sliders; the status line says how many stats were kept.
- The shrine / recipe list works for every recipe (before, picking any recipe other than the first one was ignored:
  the combobox callbacks shared one Python variable that later rows overwrote; every callback now keeps its own list).
- Not modeled: shrine blessing ("Shrine Blessed") and other upgrades of a crafted item, crafted jewels.

## Uniques with special text (The Collector, Azurewrath, Void-Infused..., 2026-10-03)
- The orange / grey text of a unique (The Collector's "Gematria ... +25% Bonus to Defense ... (Bonus for each
  Socketed Great/Elemental Rune)", Azurewrath's rules, Void-Infused's "Erupting Strike grants ...") is ONE text stat
  (375 / 377 / 378 / 385, param = string index) — the bonuses come from the item's hidden skill (+1 to The Collector,
  oskill 1913) and hidden stats. The import read those text lines as real stats (flat +25% defense, +10 life after
  kill, +8% spell damage) and missed the hidden skill and "Orb Effects Applied to this Item are Doubled".
- Import now: the item's unique row (picked from a first reading among same-name rows, e.g. Relics) and the cube
  upgrades the page shows give the text stats; a text whose whole text is on the page is taken as that stat (cube
  upgrades first: they replace the row's text, e.g. Relic states 385) and its lines are not read as stats; row
  stats whose lines are on the page but were not read are added (oskills, orb doubling), a skill read with another
  skill of the same name takes the row's skill; the base's innate affix is set (auto affix id, hidden innate
  element 395/242, "Innate X Damage" 415 read from its page line).
- Uniques and set items get their base's innate (automagic) affix in the Item Creator too (Innate affix list), as
  the game's do (real copies: auto affix on every Sacred / Angelic unique and set item): a created Azurewrath now
  has exactly the stats of the game's (Innate Cold Damage, Adds 160-250 Cold Damage, hidden 151 / 395 / 415...).
- Test (t2/imp_rt.py): the 1838 real uniques of the test stashes, their tooltip lines as a page, imported and
  compared: The Collector / Azurewrath identical; items with differences 1141 → 1008 (the rest: older game
  versions, charges, upgrade states the page cannot tell apart).

## D2 look (homage to Udie's Ultimate Median XL Item Editor, 2026-10-03)
- Main window: the stash and the character's equipment / inventory are drawn on Median XL Sigma's own in-game
  panels (Tartarus theme, as in game; settings key d2_theme = "classic_sigma" for the red Classic theme): stash
  panel with "Storage Page: n / 10" and the character's stash gold in the game's font, inventory panel with the gear
  frames (slot silhouettes when empty), the "I II" weapon-set tabs (clickable), the character's gold, the close
  buttons. Both panels use the same cell size, chosen so they fit the screen side by side with the detail panel.
  Items: no cell boxes, red background when the character cannot use them (like the game), outline when selected.
- Item Creator / Editor: a header on the game's stone with the title in the game's font (font30), the item in a
  gear frame and its name in its quality colour; stone-and-gold buttons.
- The art is read from the player's own game files (MPQ: data\global\themes\<theme>\game\..., fonts
  data\local\font\latin\*.dc6/.tbl, palette ACT1), decoded (mxl_d2ui_dc6.py: DC6 → PNG, pure Python) once and kept
  in <user dir>\ui_art\<theme>\ (re-extracted when the Median XL version changes); a brightness curve (gamma 0.6,
  measured against an in-game screenshot) matches the game's on-screen colours. Nothing of the game is in the exe.
- "D2 look" checkbox in the top bar (applied at the next start). Without the game folder / art, the plain layout.
- Layout values (panel pixels, 512×704, 29 px cells): mxl_d2look.py (stash grid at 53,150; inventory grid at 37,329;
  gear frames measured by template matching on a screenshot). Text with characters the game font lacks (Vietnamese)
  falls back to a normal font in the same gold.

## Item Creator window, affixes, sockets and corruption (user feedback, 2026-10-03)
- Window: sized from the screen (up to 1750×1080, never larger than the screen), three resizable columns
  (catalog | options | preview: drag the separators). All options and the variable values are in one scroll area;
  the status line and the Close / Create / Replace buttons are always visible at the bottom, however many stats the
  item has. The mouse wheel scrolls the options anywhere over them and never changes a combobox by accident.
- Affix lists show the game's own tooltip text with the ranges: "+(31 to 40) to all Attributes   [L80, #41]",
  "+(180 to 200)% Enhanced Damage" (before, only the first stat's name was shown: all-attribute affixes looked like
  "to Strength" and Enhanced Damage affixes showed no text). Same for innate affixes and crafting recipes.
- Affix groups: the game never puts two affixes of one group on an item (e.g. the 200% Enhanced Damage prefix and a
  spell damage prefix are both group 110). An affix that conflicts with another chosen one is marked "⚠ same group as
  Prefix 1" in the list; choosing it asks to replace the other one. A crafted item already holding its 4 affixes says
  so instead of breaking. When the options cannot make an item, the preview says why (it no longer just goes blank).
- Sockets: "Max sockets (n)" for every item that can have sockets (set items included: Sigma's Sacred sets carry
  sockets, 175 of 177 in the test saves have the max). New uniques, set items and crafted items start with the max,
  as the game gives them; the box stays ticked when the item level or the base changes.
- Corruption (Sacred / Angelic gear and jewelry, types under 'ssgl'): "Corruption" list with every outcome and its
  chance, from cubemain: Corrupted Crystal step (25% +100 Required Level, 28.5% no effect, 46.5% not revealed yet;
  unique rings: +50 / -10 Required Level or corrupted), then the Oil of Craft reveal table (hidden roll 1-10000,
  stat 498; 73 outcomes such as +1 to All Skills 0.45%, +20% Cast Speed 2.3%...). "Roll like the cube" draws one
  with the game's chances. Stats: 497 = Corrupted, 496 = "(Cube with Oil of Craft to Reveal)", 498 = hidden roll.
  Editing a corrupted item finds the outcome that explains its stats (the rest is kept as before); 4194 items still
  rebuild identically.

## Loading screen (mxl_splash.py, 2026-10-03)
Homage to Udie's editor: while the editor loads, Diablo II's "door opening" loading animation plays
(`data\global\ui\loading\loadingscreen.dc6`, 10 frames, palette `loading`, read from the player's game files).
- The robed figure is painted out (smooth fill of the surrounding light, feathered edges) and the program icon's
  capybara stands on the platform facing the door, darkened / rim-lit / washed by the door's glare per frame,
  with a contact shadow. "LOADING..." is replaced by "Median XL Capybara Editor" in the game's Exocet font.
- Runs as a separate process (the exe started again with `--capy-splash <pid>`), so the editor's blocking table
  load does not freeze it. Closes (fade) when the main window is built, before any question / file dialog /
  data-rebuild progress window of the start, if the editor dies, or after 90 s. Flag file in %TEMP%.
- Frames are built once (~5-8 s) into `<user dir>\ui_art\splash\door-v7-*.png`; bump `VERSION` to rebuild.
- Only when the D2 look is on and the game folder is known; otherwise no splash (plain fallback inside the
  splash process if the art can't be read).

## Values the item can store, sockets audit (2026-10-03)
- Every stat is saved in a fixed number of bits (ItemStatCost save bits / add): a mod range that asks more is narrowed
  to what can be stored (`Creator._fit_storage`, mods adding to one stat share the room). Alnair / Thelema
  "to Summon Attack Rating" 100-200 / 130-180 -> up to 155, Spire of Kehjan 250 Damage Reflected -> 155 (a real
  Sacred War Staff in a shared stash has exactly 155), Halberd skill suffix 21-33 -> 21-30 (real Halberds: 30),
  Ring of Woe's corruption roll 1-10000 -> 1-9999 (+1 from its other mod).
- Rare names: a rare prefix is saved as index + 183 in 8 bits, so Sigma's last 4 rare prefixes are not offered.
- Lists: misc items no affix can spawn on are left out of Magic / Rare, gold out of Normal, runewords without a base
  (Secret Jewelwords) out of Runeword.
- Sockets: innate "sock" (stat 194) becomes header sockets capped at the max, never a stat (Angelic +6,
  Mastercrafted +6/+3/+4/+3, qblt belts +2).

## Median XL Sigma item layouts (mxl_format_fix.py)
The collector's item reader is replaced at start-up by the same reader plus two Sigma cases it did not know, which
stopped stashes from opening ("FormatError: 'JM' expected at byte ..."):
- quality 9 items ("tempered", e.g. the "Orb Effects Applied to this Item are Doubled" / Shrine Blessed gear): only
  the two name bytes follow the header, no affix slots;
- weapons / armor whose item type is not under 'weap' / 'armo' (the Staff of Herding): no defense or durability in
  the save, as the game reads them only for those types.
The creator's encoder / decoder and the import follow the same rules.

## Languages (English, Français, Tiếng Việt)

The Language setting of the main window now covers every window of the editor: the collector's own texts and the
added windows (Item Creator / Editor, Character window, equipment panel, import). Texts are in `lang/<code>.json`
next to the exe: `en.json` (reference, all keys), `fr.json`, `vi.json`. The added windows' keys start with `cr.`,
`ch.`, `he.`, `inv.`, `im.`; `mxl_lang.T(key, english)` reads them and falls back to the English text written in the
code, so a missing key never breaks a window. Fields in braces (`{name}`, `{level}`...) must stay unchanged.
Game text (item, stat, skill, quest and waypoint names, tooltips) comes from Median XL's tables and stays in English.
To add a language: copy en.json to lang/<code>.json, set `_language` and translate the values.

## Program icon
The exe file, the taskbar and every window use the capybara icon (capybara.ico in the exe's resources; mxl_app_icon.py
for the windows). The Character window keeps the old Hero Editor's own icon.

## Files
- `mxl_creator.py`: bit-level item encoder/decoder (lossless), the legit generators and crafting recipes (cubemain).
- `mxl_creator_gui.py`: the creator window and the main-window buttons.
- `mxl_char.py`: character save reader/writer (stats, quests, waypoints, items, mercenary).
- `mxl_char_gui.py`: the character window.
- `mxl_app_icon.py`: the capybara window / taskbar icon. `rsrc.py`: puts an .ico into the exe's resources
  (`repack.py ... --icon=capybara.ico`).
- `mxl_he_art.py`: button images and window icon from Hero Editor.exe 1.04 (ZonFire99, Mastadex).
- `mxl_inv_gui.py`: the equipment and inventory panel of the main window (drag & drop with the stash).
- `mxl_statparse.py`: reads tooltip lines back into item stats (uses the collector's tooltip text as the reference).
- `mxl_format_fix.py`: the reader fix above.
- `mxl_import.py`: the character page import (page reader, item rebuilder, new character, Import button).
- `mxl_d2look.py`, `mxl_d2look_art.py`, `mxl_d2ui_dc6.py`: D2 look (layout, game art cache, DC6 decoder).
- `mxl_splash.py`: loading screen (door animation with the capybara).
- `mxl_lang.py`: translation helper of the added windows (see Languages).
- `repack.py`, `carch.py`: rebuild the PyInstaller exe with these modules (Python 3.10:
  `python repack.py "MXL Item Collector.exe" "MXL Item Collector (Creator).exe"`, plugin folder path set inside).
