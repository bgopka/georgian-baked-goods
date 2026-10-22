# Georgian Baked Goods

Website for Georgian Baked Goods, 10 Plastics Avenue, Etobicoke.
Static site on Vercel (https://georgianbakedgoods.com); every push to `main` redeploys in ~30 s.

## One place for the menu and the hours

| To change… | Edit | Then |
|---|---|---|
| Menu items, prices, photos, descriptions | `data/menu.json` | `python3 tools/build.py` and commit everything it changed |
| Hours, holiday closures, phone, email, address | `data/business.json` | same |

`tools/build.py` rewrites every copy on the site:

- `index.html`: menu cards, structured data (Bakery hours/phone/address, Menu + prices), FAQ hours, every hours line
- `order.html`: the items customers can order (`var MENU`)
- `georgian-food/**`: hours lines; the build **fails** if a guide still links to an item that's no longer on the menu
- `llms.txt`: contact, hours and menu list for AI assistants
- `Georgian-Baked-Goods-Menu.pdf`: the printable menu (same layout as before)
- `sitemap.xml`: `lastmod` for pages that changed
- `n8n/*.node.js`: code for the order workflow (see below)

`python3 tools/build.py --check` changes nothing and exits 1 if any file is out of date. Run it before every commit.
Requires Python 3 with `pip install reportlab pillow`. Fonts are in `tools/fonts/` (Montserrat, OFL licence).

Generated parts of HTML pages are fenced with comments such as `<!--gbg:hours:dots-->…<!--/gbg-->` and
`<!--gbg:menu-cards-->…<!--/gbg:menu-cards-->`. Anything inside a fence is overwritten on build; edit outside freely.

### Rules for the menu

- Item numbers are what customers and staff say ("two number 31s"). To take an item off the menu, delete it from its
  section and add it to `retired` with the date. Prefer a new number for a new item; if a number is reused anyway,
  note it in `retired` (orders in the sheet before that date mean the old item).
- Photos: `menu/NN.jpg`, 462×430, JPEG. Use the next free number.
- One-line descriptions; the build shrinks the PDF text to fit and fails if it still can't.

### Hours and online ordering

Online ordering (n8n workflow **GBG — Online Orders**) reads `https://georgianbakedgoods.com/data/business.json` and
`/data/menu.json` on every page load and every order. So:

- changing hours in `data/business.json` changes when online ordering opens, the moment the commit deploys;
- prices customers are charged come from `data/menu.json`. The browser's prices are still ignored and re-priced server-side;
- a holiday or short day goes in `specialHours`, e.g. `{ "date": "2026-12-25", "closed": true, "note": "Christmas Day" }`.

The old hours source (the **Settings** tab of the "GBG Orders" Google Sheet) and the old hard-coded price list in
n8n are no longer used once the n8n nodes below are installed.

### n8n code (`n8n/`)

n8n code nodes can't import files, so the build assembles complete node bodies:

- `n8n/validate-and-price.node.js` → paste into node **Validate & Price**
- `n8n/peek-counter.node.js` → paste into node **Peek Counter**
- `n8n/site-data.js` is the shared part (loading the data, price list, hours logic); edit it here, then rebuild.

After pasting, delete the two Google Sheets nodes **Read Hours** and **Read Hours (next)** and connect
**Order In → Validate & Price** and **Next Number → Peek Counter** directly. Code changes in `n8n/` are only needed when the
*logic* changes. Menu or hours changes need no n8n edits.

## Deploying

Commits go through the n8n workflow "Georgian Baked Goods · Git Commit" (text files only) or directly with git
(needed for photos and the PDF).
