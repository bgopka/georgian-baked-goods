#!/usr/bin/env python3
"""Georgian Baked Goods — rebuild everything that repeats the menu or the hours.

    python3 tools/build.py            # rewrite files from data/*.json
    python3 tools/build.py --check    # change nothing; exit 1 if any file is out of date

Sources of truth (edit these, never the generated parts):
    data/menu.json       items, prices, photos, sections
    data/business.json   hours, phone, email, address

What it writes:
    index.html           menu cards, Bakery JSON-LD (hours, phone, address, Menu/Offer), FAQ hours, every hours line
    order.html           var MENU = [...]  (what customers can add to the cart)
    georgian-food/**     hours lines in the "Visit" box and footer; fails if a guide links to an item not on the menu
    llms.txt             contact/hours lines and the menu list
    Georgian-Baked-Goods-Menu.pdf   the printable menu (needs: pip install reportlab pillow)
    sitemap.xml          lastmod for every page this run changed

n8n ("GBG — Online Orders") does not need a rebuild: it reads data/menu.json and data/business.json live
from https://georgianbakedgoods.com/data/ to price orders and to open/close online ordering.

Generated regions in HTML are fenced with comments, e.g.
    <!--gbg:hours:dots-->Tue–Sun · 11 am – 8 pm · Closed Mondays<!--/gbg-->
    <!--gbg:menu-cards--> ... <!--/gbg:menu-cards-->
Text inside a fence is overwritten on every build; text outside is never touched.
"""
import datetime, glob, html, io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get('GBG_DATA', os.path.join(ROOT, 'data'))
SITE = 'https://georgianbakedgoods.com'
DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
SHORT = {d: d[:3].title() for d in DAYS}
FULL = {d: d.title() for d in DAYS}


def load(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return json.load(f)


menu = load('menu.json')
biz = load('business.json')
errors = []

# ---------------------------------------------------------------- validate
seen = {}
for s in menu['sections']:
    for it in s['items']:
        if it['id'] in seen:
            errors.append(f"menu.json: id {it['id']} used twice ({seen[it['id']]} and {it['name']})")
        seen[it['id']] = it['name']
        for k in ('name', 'price', 'photo', 'description'):
            if k not in it:
                errors.append(f"menu.json: #{it['id']} is missing '{k}'")
        if 'photo' in it and not os.path.exists(os.path.join(ROOT, it['photo'])):
            errors.append(f"menu.json: #{it['id']} photo {it['photo']} does not exist")
        if not isinstance(it.get('price'), (int, float)) or it['price'] <= 0:
            errors.append(f"menu.json: #{it['id']} has a bad price")
for d in DAYS:
    h = biz['hours'].get(d, 'missing')
    if h == 'missing':
        errors.append(f'business.json: hours.{d} is missing (use null for closed)')
    elif h is not None and not (re.fullmatch(r'\d{2}:\d{2}', h.get('open', '')) and re.fullmatch(r'\d{2}:\d{2}', h.get('close', ''))):
        errors.append(f'business.json: hours.{d} needs "open" and "close" as HH:MM')

# "available": false hides an item everywhere (site, PDF, llms.txt; n8n refuses it too) while keeping its number reserved
menu['sections'] = [dict(s, items=[it for it in s['items'] if it.get('available', True) is not False]) for s in menu['sections']]
menu['sections'] = [s for s in menu['sections'] if s['items']]
seen = {it['id']: it['name'] for s in menu['sections'] for it in s['items']}
items = [dict(it, section=s) for s in menu['sections'] for it in s['items']]


def full_name(it):
    return f"{it['name']} — {it['size']}" if it.get('size') else it['name']


def price_txt(p):
    return f'{p:.0f}' if float(p).is_integer() else f'{p:.2f}'


# ---------------------------------------------------------------- hours text
def mins(hhmm):
    h, m = map(int, hhmm.split(':'))
    return h * 60 + m


def clock(hhmm, style):
    m = mins(hhmm)
    h, mm = divmod(m, 60)
    h12 = (h + 11) % 12 + 1
    ap = 'am' if h < 12 else 'pm'
    if style == 'bare':
        return f'{h12}' + (f':{mm:02d}' if mm else '')
    if style == 'hm':
        return f'{h12}:{mm:02d} {ap}'
    return f'{h12}' + (f':{mm:02d}' if mm else '') + f' {ap}'


def groups():
    """Consecutive open days with identical hours, Monday-first."""
    out = []
    for d in DAYS:
        h = biz['hours'][d]
        if h is None:
            continue
        key = (h['open'], h['close'])
        if out and out[-1]['key'] == key and DAYS.index(out[-1]['to']) == DAYS.index(d) - 1:
            out[-1]['to'] = d
        else:
            out.append({'key': key, 'from': d, 'to': d})
    return out


def closed_days():
    return [d for d in DAYS if biz['hours'][d] is None]


def rng(g, names, sep):
    if len(groups()) == 1 and g['from'] == 'monday' and g['to'] == 'sunday':
        return 'Every day' if names is FULL else 'Daily'
    if g['from'] == g['to']:
        return names[g['from']]
    return f"{names[g['from']]}{sep}{names[g['to']]}"


def tm(g, style, sep):
    return f"{clock(g['key'][0], style)}{sep}{clock(g['key'][1], style)}"


def closed_list(short=False):
    c = closed_days()
    words = [SHORT[d] if short else FULL[d] + 's' for d in c]
    return ' and '.join(words) if len(words) < 3 else ', '.join(words[:-1]) + ' and ' + words[-1]


def hours_text(style):
    G, C = groups(), closed_days()
    if style == 'sentence':      # Tuesday to Sunday, 11 am to 8 pm
        return '; '.join(f"{rng(g, FULL, ' to ')}, {tm(g, 'h', ' to ')}" for g in G)
    if style == 'faq':           # Open Tuesday to Sunday, 11 am to 8 pm. Closed on Mondays.
        return f"Open {hours_text('sentence')}." + (f' Closed on {closed_list()}.' if C else '')
    if style == 'dots':          # Tue–Sun · 11 am – 8 pm · Closed Mondays
        return ' · '.join(f"{rng(g, SHORT, '–')} · {tm(g, 'h', ' – ')}" for g in G) + (f' · Closed {closed_list()}' if C else '')
    if style == 'visit':         # Tuesday – Sunday<br>11:00 am – 8:00 pm<br>Closed Mondays
        return '<br>'.join(f"{rng(g, FULL, ' – ')}<br>{tm(g, 'hm', ' – ')}" for g in G) + (f'<br>Closed {closed_list()}' if C else '')
    if style == 'tiny':          # Tue–Sun 11–8, closed Mon
        return '; '.join(f"{rng(g, SHORT, '–')} {tm(g, 'bare', '–')}" for g in G) + (f', closed {closed_list(True)}' if C else '')
    if style == 'guide':         # Open Tuesday–Sunday, 11 am – 8 pm; closed Mondays.
        return 'Open ' + '; '.join(f"{rng(g, FULL, '–')}, {tm(g, 'h', ' – ')}" for g in G) + (f'; closed {closed_list()}' if C else '') + '.'
    if style == 'guideFooter':   # Open Tue–Sun, 11 am – 8 pm
        return 'Open ' + '; '.join(f"{rng(g, SHORT, '–')}, {tm(g, 'h', ' – ')}" for g in G)
    if style == 'llms':          # Tuesday–Sunday, 11:00 am – 8:00 pm; closed Mondays
        return '; '.join(f"{rng(g, FULL, '–')}, {tm(g, 'hm', ' – ')}" for g in G) + (f'; closed {closed_list()}' if C else '')
    if style == 'pdf':           # Open Tuesday – Sunday, 11 am – 8 pm · Closed Mondays
        return 'Open ' + '; '.join(f"{rng(g, FULL, ' – ')}, {tm(g, 'h', ' – ')}" for g in G) + (f' · Closed {closed_list()}' if C else '')
    raise ValueError(f'unknown hours style {style!r}')


# ---------------------------------------------------------------- fences
FENCE = re.compile(r'(<!--gbg:hours:(\w+)-->)(.*?)(<!--/gbg-->)', re.S)


def fill_hours(text):
    return FENCE.sub(lambda m: m.group(1) + hours_text(m.group(2)) + m.group(4), text)


def fill_block(text, name, body, path):
    pat = re.compile(r'(<!--gbg:%s-->)(.*?)(<!--/gbg:%s-->)' % (name, name), re.S)
    if not pat.search(text):
        errors.append(f'{path}: missing <!--gbg:{name}--> fence')
        return text
    return pat.sub(lambda m: m.group(1) + body + m.group(3), text)


# ---------------------------------------------------------------- index.html
CARD = '''
<article style="background: #FFFFFF; border-radius: 18px; overflow: hidden; border: 1px solid #ECE3CB; box-shadow: 0 2px 12px rgba(60, 45, 10, 0.06); display: flex; flex-direction: column">
<div style="position: relative; height: 210px; background: #D6B25E">
<img loading="lazy" decoding="async" width="462" height="430" src="{photo}" alt="{alt}" style="width: 100%; height: 100%; object-fit: cover; display: block">
</div>
<div style="padding: 18px 20px 22px; display: flex; flex-direction: column; gap: 8px; flex-grow: 1">
<div style="display: flex; justify-content: space-between; gap: 12px; align-items: center">
<div style="display: flex; align-items: center; gap: 12px; min-width: 0">
<span aria-label="Item number {id}" style="flex: 0 0 auto; min-width: 46px; height: 46px; padding: 0 8px; box-sizing: border-box; border-radius: 12px; background: #2B2620; color: #D6B25E; font-family: 'Montserrat', 'Helvetica Neue', sans-serif; font-weight: 800; font-size: 24px; line-height: 1; display: flex; align-items: center; justify-content: center">{id}</span>
<h4 style="margin: 0; font-family: 'Montserrat', 'Helvetica Neue', sans-serif; font-weight: 700; font-size: 19px; line-height: 1.2">{name}</h4>
</div>
<span style="font-family: 'Montserrat', 'Helvetica Neue', sans-serif; font-weight: 800; font-size: 24px; color: #B3261E; white-space: nowrap">${price}</span>
</div>
<p style="margin: 0; font-size: 15px; line-height: 1.55">{desc}</p>
</div>
</article>
'''

SECTION = '''
<div style="margin-bottom: 64px">
<div style="display: flex; align-items: baseline; gap: 16px; border-bottom: 1px solid #E3D6AE; padding-bottom: 12px; margin-bottom: 28px; flex-wrap: wrap">
<h3 style="margin: 0; font-family: 'Montserrat', 'Helvetica Neue', sans-serif; font-weight: 800; font-size: 32px; letter-spacing: -0.01em">{title}</h3>
<span style="font-size: 16px">{subtitle}</span>
</div>
<div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 28px">
{cards}
</div>
</div>
'''


def menu_cards_html():
    out = []
    for s in menu['sections']:
        cards = ''.join(CARD.format(
            photo=it['photo'], id=it['id'], price=price_txt(it['price']),
            name=html.escape(full_name(it), quote=False),
            alt=html.escape(f"{full_name(it)}: {it['description'].rstrip('.')}"),
            desc=html.escape(it['description'], quote=False)) for it in s['items'])
        out.append(SECTION.format(title=html.escape(s['title'], quote=False),
                                  subtitle=html.escape(s['subtitle'], quote=False), cards=cards))
    return ''.join(out)


def opening_spec():
    return [{'@type': 'OpeningHoursSpecification',
             'dayOfWeek': [FULL[d] for d in DAYS[DAYS.index(g['from']):DAYS.index(g['to']) + 1]],
             'opens': g['key'][0], 'closes': g['key'][1]} for g in groups()]


def json_ld_menu():
    return {'@type': 'Menu', 'name': 'Georgian Baked Goods menu', 'url': f'{SITE}/#menu',
            'hasMenuSection': [{'@type': 'MenuSection', 'name': s['title'], 'hasMenuItem': [
                {'@type': 'MenuItem', 'name': f"#{it['id']} {full_name(it)}", 'description': it['description'],
                 'image': f"{SITE}/{it['photo']}",
                 'offers': {'@type': 'Offer', 'price': price_txt(it['price']) if float(it['price']).is_integer() else f"{it['price']:.2f}",
                            'priceCurrency': menu.get('currency', 'CAD')}}
                for it in s['items']]} for s in menu['sections']]}


def tel_dashed():
    d = re.sub(r'\D', '', biz['phone']['e164'])[-10:]
    return f'+1-{d[:3]}-{d[3:6]}-{d[6:]}'


LD = re.compile(r'(<script type="application/ld\+json">)(.*?)(</script>)', re.S)


def fix_ld(text):
    def one(m):
        d = json.loads(m.group(2))
        if not isinstance(d, dict):
            return m.group(0)
        if d.get('@type') == 'Bakery' and 'address' in d:
            a = biz['address']
            d['telephone'] = tel_dashed()
            d['email'] = biz['email']
            d['address'].update({'streetAddress': a['street'], 'addressLocality': a['locality'],
                                 'addressRegion': a['region'], 'postalCode': a['postalCode']})
            d['openingHoursSpecification'] = opening_spec()
            d['hasMenu'] = json_ld_menu()
        if d.get('@type') == 'FAQPage':
            for q in d.get('mainEntity', []):
                if q.get('name') == 'What are your hours?':
                    q['acceptedAnswer']['text'] = hours_text('faq')
        return m.group(1) + json.dumps(d, ensure_ascii=False, separators=(',', ':')) + m.group(3)
    return LD.sub(one, text)


# ---------------------------------------------------------------- order.html
def order_menu_js():
    data = [{'title': s['title'], 'items': [{'id': it['id'], 'name': full_name(it), 'price': float(it['price']),
                                             'img': it['photo']} for it in s['items']]} for s in menu['sections']]
    return 'var MENU = ' + json.dumps(data, ensure_ascii=False) + ';'


# ---------------------------------------------------------------- llms.txt
def llms(text):
    a = biz['address']
    text = re.sub(r'^- Address: .*$', f"- Address: {a['street']}, {a['locality']}, {a['region']} {a['postalCode']}, Canada", text, flags=re.M)
    text = re.sub(r'^- Phone: .*$', f"- Phone: {biz['phone']['display']} ({tel_dashed()})", text, flags=re.M)
    text = re.sub(r'^- Email: .*$', f"- Email: {biz['email']}", text, flags=re.M)
    text = re.sub(r'^- Hours: .*$', f"- Hours: {hours_text('llms')}", text, flags=re.M)
    lines = '\n'.join(f"- #{it['id']} {full_name(it)} — ${price_txt(it['price'])} CAD. {it['description']}" for it in items)
    new, n = re.subn(r'(## Menu \(prices in CAD, before tax\)\n)(?:- .*\n)+', lambda m: m.group(1) + lines + '\n', text)
    if n != 1:
        errors.append('llms.txt: could not find the "## Menu (prices in CAD, before tax)" list')
    return new


# ---------------------------------------------------------------- guides: links to items
def check_item_refs(path, text):
    for m in re.finditer(r'#(\d\d) ([A-Z][^<\n"]{1,40}?)(?=<|\n|"|,| and)', text):
        n, nm = int(m.group(1)), m.group(2).strip()
        if n not in seen:
            errors.append(f'{path}: mentions #{n} {nm}, which is not on the menu')
        elif nm.split()[0].lower() != seen[n].split()[0].lower():   # "#11 Imeruli Large" is fine shorthand
            errors.append(f'{path}: mentions #{n} {nm}, but #{n} is now {seen[n]}')


# ---------------------------------------------------------------- PDF
def build_pdf():
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from PIL import Image, ImageDraw

    fd = os.path.join(ROOT, 'tools', 'fonts')
    for w in ('Regular', 'Medium', 'SemiBold', 'Bold', 'ExtraBold'):
        pdfmetrics.registerFont(TTFont(f'M-{w}', os.path.join(fd, f'Montserrat-{w}.ttf')))
    BLACK, INK, GOLD, CREAM = HexColor('#111111'), HexColor('#1F1B16'), HexColor('#D6B25E'), HexColor('#FFF8D6')
    LINE, RED, MUTED, WHITE = HexColor('#D9CCA6'), HexColor('#B3261E'), HexColor('#4A4238'), HexColor('#FFFFFF')
    W, H = letter
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter, pageCompression=1)
    c.setTitle('Georgian Baked Goods — Menu')
    c.setAuthor(biz['name'])
    c.setCreator('tools/build.py')

    # header
    HB = 104
    c.setFillColor(BLACK); c.rect(0, H - HB, W, HB, stroke=0, fill=1)
    c.setFillColor(GOLD); c.rect(0, H - HB - 3, W, 3, stroke=0, fill=1)
    c.drawImage(ImageReader(os.path.join(ROOT, 'logo.png')), 26, H - 96, 88, 88 * 616 / 640, mask='auto')
    c.setFillColor(GOLD); c.setFont('M-ExtraBold', 20.5); c.drawString(130, H - 42, biz['name'].upper())
    c.setFillColor(CREAM); c.setFont('M-SemiBold', 11); c.drawString(130, H - 60, biz['tagline'])
    a = biz['address']
    c.setFont('M-Regular', 8.6)
    c.drawString(130, H - 76, f"{a['street']}, {a['locality']}, {a['region']} {a['postalCode']}")
    c.drawString(130, H - 89, hours_text('pdf'))
    bx, bw, bh = W - 180, 150, 60
    c.setFillColor(GOLD); c.roundRect(bx, H - 82, bw, bh, 9, stroke=0, fill=1)
    c.setFillColor(BLACK); c.setFont('M-Bold', 7.5); c.drawCentredString(bx + bw / 2, H - 37, 'CALL TO ORDER')
    c.setFont('M-ExtraBold', 19.5); c.drawCentredString(bx + bw / 2, H - 64, biz['phone']['display'])

    # footer
    FB = 44
    c.setFillColor(BLACK); c.rect(0, 0, W, FB, stroke=0, fill=1)
    c.setFillColor(CREAM); c.setFont('M-SemiBold', 9.5)
    c.drawCentredString(W / 2, 17, f"Order by number — call {biz['phone']['display']} and pick up fresh   ·   {biz['email']}")

    # layout: fit sections between header and footer
    M, COLS, GAP = 30, 3, 9.5
    CW = (W - 2 * M - GAP * (COLS - 1)) / COLS
    rows = sum(-(-len(s['items']) // COLS) for s in menu['sections'])
    top, bottom = H - HB - 3 - 16, FB + 10
    SH = 30                                   # section header height
    avail = top - bottom - SH * len(menu['sections']) - 6 * (len(menu['sections']) - 1)
    CH = min(66.0, (avail - (rows - 1) * 6.5) / rows)
    if CH < 54:
        errors.append(f'PDF: {rows} rows of items do not fit on one page; shorten the menu or add a page layout')
    RG = 6.5 if CH >= 66 else max(4.0, (avail - rows * CH) / max(rows - 1, 1))

    def rounded_photo(path, size_pt):
        px = int(size_pt * 4)
        im = Image.open(os.path.join(ROOT, path)).convert('RGB')
        w, h = im.size; s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((px, px), Image.LANCZOS)
        mask = Image.new('L', (px * 2, px), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, px * 2 - 1, px - 1), radius=int(7 * 4), fill=255)
        im.putalpha(mask.crop((0, 0, px, px)))
        return ImageReader(im)

    def wrap(text, font, size, width):
        words, lines, cur = text.split(), [], ''
        for w_ in words:
            t = (cur + ' ' + w_).strip()
            if pdfmetrics.stringWidth(t, font, size) <= width:
                cur = t
            else:
                lines.append(cur); cur = w_
        if cur:
            lines.append(cur)
        return lines

    y = top
    for si, s in enumerate(menu['sections']):
        c.setFillColor(INK); c.setFont('M-ExtraBold', 15.5); c.drawString(M, y - 14, s['title'].upper())
        tw = pdfmetrics.stringWidth(s['title'].upper(), 'M-ExtraBold', 15.5)
        c.setFont('M-Medium', 8.2); c.setFillColor(MUTED); c.drawString(M + tw + 10, y - 14, s['subtitle'])
        c.setStrokeColor(GOLD); c.setLineWidth(1.5); c.line(M, y - 21, W - M, y - 21)
        y -= SH
        for i, it in enumerate(s['items']):
            col, row = i % COLS, i // COLS
            x = M + col * (CW + GAP)
            cy = y - row * (CH + RG) - CH
            c.setFillColor(WHITE); c.setStrokeColor(LINE); c.setLineWidth(0.8)
            c.roundRect(x, cy, CW, CH, 7, stroke=1, fill=1)
            c.drawImage(rounded_photo(it['photo'], CH), x, cy, CH, CH, mask='auto')
            tx, tr = x + CH + 8, x + CW - 8
            # number badge
            num = str(it['id'])
            c.setFillColor(HexColor('#2B2620')); c.roundRect(tx, cy + CH - 25, 21, 17, 4, stroke=0, fill=1)
            c.setFillColor(GOLD); c.setFont('M-ExtraBold', 10.5); c.drawCentredString(tx + 10.5, cy + CH - 20.5, num)
            if it.get('size'):
                c.setFillColor(INK); c.setFont('M-SemiBold', 7.6); c.drawString(tx + 27, cy + CH - 20, it['size'].upper())
            c.setFillColor(RED); c.setFont('M-ExtraBold', 12.5); c.drawRightString(tr, cy + CH - 21, '$' + price_txt(it['price']))
            c.setFillColor(INK); c.setFont('M-Bold', 8.6); c.drawString(tx, cy + CH - 35, it['name'])
            fs = 6.2
            while True:                              # shrink the description until it fits the card
                lead = fs * 1.16
                lines = wrap(it['description'], 'M-Regular', fs, tr - tx)
                if 45 + (len(lines) - 1) * lead <= CH - 5 or fs <= 5.2:
                    break
                fs -= 0.2
            if 45 + (len(lines) - 1) * lead > CH - 5:
                errors.append(f"PDF: description of #{it['id']} is too long for its card; shorten it")
            c.setFillColor(MUTED); c.setFont('M-Regular', fs)
            for li, ln in enumerate(lines):
                c.drawString(tx, cy + CH - 45 - li * lead, ln)
        y -= -(-len(s['items']) // COLS) * (CH + RG) - RG + 6
    c.showPage(); c.save()
    return buf.getvalue()


# ---------------------------------------------------------------- run
def main():
    check = '--check' in sys.argv
    if errors:
        print('\n'.join('ERROR ' + e for e in errors)); sys.exit(2)
    changed, pending = [], {}

    def put(rel, new, binary=False):
        p = os.path.join(ROOT, rel)
        old = open(p, 'rb').read() if os.path.exists(p) else None
        data = new if binary else new.encode('utf-8')
        if old != data:
            changed.append(rel)
            pending[p] = data          # written only if the whole build succeeds

    def read(rel):
        return open(os.path.join(ROOT, rel), encoding='utf-8').read()

    # index.html
    t = read('index.html')
    t = fill_block(t, 'menu-cards', menu_cards_html(), 'index.html')
    t = fill_hours(fix_ld(t))
    check_item_refs('index.html', re.sub(r'<!--gbg:menu-cards-->.*?<!--/gbg:menu-cards-->', '', t, flags=re.S))
    put('index.html', t)

    # order.html
    t = read('order.html')
    t, n = re.subn(r'var MENU = \[.*?\];', lambda m: order_menu_js(), t, count=1, flags=re.S)
    if n != 1:
        errors.append('order.html: could not find "var MENU = [...];"')
    put('order.html', fill_hours(t))

    # guides
    for g in sorted(glob.glob(os.path.join(ROOT, 'georgian-food', '**', 'index.html'), recursive=True)):
        rel = os.path.relpath(g, ROOT)
        t = fill_hours(read(rel))
        check_item_refs(rel, t)
        put(rel, t)

    put('llms.txt', llms(read('llms.txt')))

    # n8n node code: paste the shared site-data.js block under each node's marker
    shared = read('n8n/site-data.js')
    for f in sorted(glob.glob(os.path.join(ROOT, 'n8n', '*.node.js'))):
        rel = os.path.relpath(f, ROOT)
        t = read(rel)
        mark = t.find('/* @@SITE_DATA@@')
        if mark < 0:
            errors.append(f'{rel}: missing the /* @@SITE_DATA@@ */ marker')
            continue
        end = t.index('*/', mark) + 2
        put(rel, t[:end] + '\n\n' + shared)

    # PDF: only rewrite when its inputs changed (ReportLab output is not byte-stable across runs)
    stamp_path = os.path.join(ROOT, 'data', '.pdf-inputs.sha256')
    import hashlib
    h = hashlib.sha256()
    for f in ['data/menu.json', 'data/business.json', 'tools/build.py', 'logo.png'] + sorted({it['photo'] for it in items}):
        h.update(open(os.path.join(ROOT, f) if not f.startswith('data/') else os.path.join(DATA, f[5:]), 'rb').read())
    digest = h.hexdigest()
    old_digest = open(stamp_path).read().strip() if os.path.exists(stamp_path) else ''
    if digest != old_digest:
        changed.append('Georgian-Baked-Goods-Menu.pdf')
        if not check:
            pending[os.path.join(ROOT, 'Georgian-Baked-Goods-Menu.pdf')] = build_pdf()
            pending[stamp_path] = (digest + '\n').encode()

    # sitemap lastmod
    urlmap = {'index.html': f'{SITE}/', 'order.html': f'{SITE}/order.html',
              'Georgian-Baked-Goods-Menu.pdf': f'{SITE}/Georgian-Baked-Goods-Menu.pdf'}
    for g in glob.glob(os.path.join(ROOT, 'georgian-food', '**', 'index.html'), recursive=True):
        rel = os.path.relpath(g, ROOT)
        urlmap[rel] = f"{SITE}/{os.path.dirname(rel)}/"
    today = datetime.date.today().isoformat()
    sm = read('sitemap.xml')
    for rel in changed:
        if rel in urlmap:
            sm = re.sub(r'(<loc>%s</loc><lastmod>)[^<]*' % re.escape(urlmap[rel]), r'\g<1>' + today, sm)
    put('sitemap.xml', sm)

    if errors:
        print('\n'.join('ERROR ' + e for e in errors)); print('Nothing was written.'); sys.exit(2)
    if not check:
        for p, data in pending.items():
            open(p, 'wb').write(data)
    if check:
        print('Out of date: ' + ', '.join(changed) if changed else 'Everything matches data/*.json')
        sys.exit(1 if changed else 0)
    print('Updated: ' + ', '.join(changed) if changed else 'Nothing to change')


if __name__ == '__main__':
    main()
