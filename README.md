# Handbook site

The Vintage Story in-game handbook as a small static web site, made for reading on a phone.

- **Guides**: every written guide page of the game and of each mod, grouped by mod.
- **Blocks and items**: one page per block or item with its name, written description, handbook notes,
  grid recipes and a "used to craft" list.
- **Search** over guide titles, guide text and block or item names.
- **Reading settings** (the `Aa` button): standard or dyslexia-friendly font, text size, dark or light colours.
  The choice is remembered on that device.

The site is plain HTML, CSS and JavaScript plus JSON data files. It needs no server-side code, so GitHub Pages
can host the `docs` folder as it is.

## On an Android phone

The site is tuned for Chrome on Android and was checked at 320, 360, 375 and 412 pixels wide.

- **Home screen app**: open the site in Chrome, tap the three-dot menu, then **Add to Home screen** (or
  **Install app**). It then opens full screen with its own book icon, like an app.
- **Offline**: after the first visit the guides, the index and the search work without a connection. A block or
  item page works offline once it has been opened before. When the phone is online it always loads the newest
  version first.
- **Back**: Android's back gesture and the Back button on each page return to the list at the same position.
- Tap targets are at least 48 pixels high, the search box does not zoom the page, and the status bar takes the
  colour of the chosen theme.

Installing and offline use need the published `https` address. They do not work from a file on the phone.

## Folders

| Path | What it is |
|---|---|
| `docs/` | The finished site. `index.html`, `app.js`, `prefs.js`, `style.css` are written by hand. |
| `docs/data/` | Generated: `handbook.json` (guides and mod list), `items.json` (item index), `items/` (item details), `build-report.txt`. |
| `docs/fonts/` | The dyslexia-friendly font as WOFF files and its license `OFL.txt`. |
| `tools/build_site.py` | The generator. Reads the game and the mods, writes `docs/data/`. |
| `tools/make_webfonts.py` | One-time conversion of the font files. Only needed if the font changes. |
| `tools/make_icons.py` | One-time drawing of the home-screen icons in `docs/icons/`. |
| `docs/manifest.webmanifest`, `docs/sw.js` | Home-screen install data and the offline helper. |

## Regenerate after a game or mod update

1. Start the game and join the server once, so the game downloads the new mod files and writes its log.
2. Run the generator from this folder:

```bash
python -I tools/build_site.py
```

3. Read `docs/data/build-report.txt`. It lists the guide count per mod and anything that failed to parse.
4. Look at the result locally (next section), then publish the changed `docs` folder.

The generator needs Python 3.9 or newer and nothing else. It takes about 40 seconds.

How it finds things:

- **Game folder**: two folders above `tools`, or the `VINTAGE_STORY` environment variable, or `--game <folder>`.
- **Mods and their load order**: read from the last session in the game's `client-main.log`. That log names the
  mod folders the game searched and the mods it loaded, so the site matches what the server ran. If a mod file
  exists in several versions, the newest is used.
- **Without the log**: `--no-log --mods <folder>` (repeat `--mods` for more folders) includes every mod found.

Other options: `--client-log <file>`, `--out <site folder>`, `--no-items` (guides only).

Mod files are only read. Nothing inside a mod zip is run.

## Look at it locally

Browsers do not load the data files from a page opened straight from a folder. Start a small local server:

```bash
python -m http.server 8765 --bind 127.0.0.1 --directory docs
```

Then open `http://localhost:8765/`.

## Publish with GitHub Pages

1. Create a repository on GitHub and push this folder to it.
2. In the repository: **Settings**, then **Pages**.
3. Under **Build and deployment** choose **Deploy from a branch**, branch `main`, folder `/docs`, then **Save**.
4. After a minute the site is at `https://<account>.github.io/<repository>/`.

Things to know before publishing:

- A Pages site is public to anyone who has the address, also when the repository is private. Private
  repositories can only use Pages on a paid GitHub plan.
- The site republishes handbook text written by the game's developers and by each mod's authors. That text is
  theirs. A public site is a redistribution of it. Keeping the repository private and reading the site locally
  avoids that question.
- `index.html` asks search engines not to index the site (`noindex`). That is a request, not a lock.
- The mod list in the index shows which mods the server runs.

## What is and is not in the site

Guide pages come from `assets/<domain>/config/handbook/*.json` with their text from the English language files.
They are complete for the game and for every mod that ships guides as asset files.

Not possible from asset files, and therefore missing:

- Pages a mod builds in code while the game runs. The build report names these mods.
- The Tutorials tab and the separate command handbook.
- Item icons. Item references in guide text are shown as named chips that link to the item page.

Block and item pages are rebuilt from the asset files and are an approximation of the in-game pages:

- **Included**: which blocks and items have a page, English name, written description, handbook sections,
  grid recipes with their trait requirement, a "used to craft" list, and a one-line "also made by" note for
  smithing, knapping, clay forming and barrel recipes.
- **Not included**, because the game computes it while running: item statistics and tooltips, drops, smelting,
  cooking and meal recipes, alloys, trader lists, creature drops and storage notes.
- **Simplified**: one page per block or item code. Variants that differ only by an item attribute, such as chest
  types, share one page. Recipes whose output differs only by attribute appear together on that page.
- **Skipped**: JSON patches that depend on the server's world settings. The build report counts them.
- Names that the game composes in code, such as graded ores, are shown from the item code instead.

Hotkeys are shown with the game's default key. The game shows your own binding.

## Font

The dyslexia-friendly option uses "DyslexicFont", the renamed build of OpenDyslexic 0.92 by Abbie Gonzalez that
the Vintage Story "Dyslexic Font" mod ships. It is licensed under the SIL Open Font License 1.1. The license
text is in `docs/fonts/OFL.txt` and must stay next to the font files. To rebuild the WOFF files:

```bash
python -I tools/make_webfonts.py <folder with DyslexicFont-*.otf>
```

That script needs `fonttools` (`pip install fonttools`).
