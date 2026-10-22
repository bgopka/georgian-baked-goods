// ===== Georgian Baked Goods — validate, price and number an order =====
// n8n workflow "GBG — Online Orders" › node "Validate & Price" (Code node, Run Once for All Items).
// Paste this whole file as the node's code. It replaces the old hard-coded MENU and the
// Google Sheet "Settings" hours: prices and hours now come from data/menu.json and
// data/business.json in git (served at https://georgianbakedgoods.com/data/).
// Whatever price the browser sends is still ignored; the order is re-priced here.

const TURNSTILE_SECRET = '';      // paste Cloudflare Turnstile SECRET key here; empty = check skipped
const START_NUMBER = 1;           // first order number
const MAX_PER_ITEM = 50;
const PHONE_PER_HOUR = 3;         // more than this from one phone -> Status "Check"
const ORDERS_PER_HOUR = 30;       // more than this in total -> Status "Check"
const BIG_TOTAL = 400, BIG_ITEMS = 60;
const MIN_FILL_MS = 3000;         // faster than this = bot
const EMAIL_PER_HOUR = 3;         // max customer confirmations to one address per hour

const req = $('Order In').first().json;
const b = req.body || {};
const now = Date.now();
const sd = $getWorkflowStaticData('global');
if (typeof sd.counter !== 'number') sd.counter = START_NUMBER - 1;
sd.recent = (sd.recent || []).filter(r => now - r.t < 3600 * 1000);

const site = await loadSiteData(this.helpers, sd);
const MENU = site ? priceList(site.menu) : {};
const BIZ = site ? site.biz : { hours: {}, phone: { display: '647 479 8929' } };
const HST_RATE = Number(BIZ.hstRate ?? 0.13);
const SHOP_PHONE = (BIZ.phone && BIZ.phone.display) || '647 479 8929';

const torontoTime = new Date(now).toLocaleString('en-CA', { timeZone: 'America/Toronto', hour12: false });
const clean = (s, max) => String(s ?? '').replace(/[\u0000-\u001f<>]/g, ' ').trim().slice(0, max);

const name = clean(b.name, 60);
const notes = clean(b.notes, 300);
let email = clean(b.email, 120).toLowerCase();
if (email && !/^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(email)) email = '';   // bad email -> just ignore it, never block the order
let digits = String(b.phone ?? '').replace(/\D/g, '');
if (digits.length === 11 && digits[0] === '1') digits = digits.slice(1);
const phonePretty = digits.length === 10 ? `${digits.slice(0,3)} ${digits.slice(3,6)} ${digits.slice(6)}` : digits;

// ---- Price from the real menu ----
const lines = [];
let itemCount = 0, subtotal = 0;
for (const it of (Array.isArray(b.items) ? b.items : [])) {
  const m = MENU[String(it?.id)];
  const qty = Math.floor(Number(it?.qty));
  if (!m || !Number.isFinite(qty) || qty < 1) continue;
  const q = Math.min(qty, MAX_PER_ITEM);
  lines.push({ num: Number(it.id), name: m.name, qty: q, price: m.price, total: Math.round(q * m.price * 100) / 100 });
  itemCount += q; subtotal += q * m.price;
}
subtotal = Math.round(subtotal * 100) / 100;
const hst = Math.round(subtotal * HST_RATE * 100) / 100;
const total = Math.round((subtotal + hst) * 100) / 100;
const itemsText = lines.map(l => `${l.qty} × $${l.price.toFixed(2)} | #${l.num} ${l.name}`).join('\n');

const base = { received: torontoTime, name, phone: phonePretty, email, notes, itemsText, itemCount, subtotal, hst, total };
const spam = reason => [{ json: { ...base, verdict: 'spam', reason,
  message: `We couldn't place this order online. Please call ${SHOP_PHONE}.`,
  raw: JSON.stringify(b).slice(0, 2000) } }];
const invalid = message => [{ json: { ...base, verdict: 'invalid', message } }];

// ---- Bot checks (silent) ----
if (clean(b.website, 200) !== '') return spam('Hidden trap field filled');
const started = Number(b.startedAt);
if (!Number.isFinite(started) || now - started < MIN_FILL_MS) return spam('Submitted too fast');
if (TURNSTILE_SECRET) {
  let ok = false;
  try {
    const r = await this.helpers.httpRequest({
      method: 'POST', url: 'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      body: { secret: TURNSTILE_SECRET, response: String(b.turnstileToken || '') }, json: true,
    });
    ok = r && r.success === true;
  } catch (e) { ok = false; }
  if (!ok) return spam('Cloudflare check failed');
}

// ---- Site data must be available ----
if (!site) return [{ json: { ...base, verdict: 'invalid', closed: true,
  message: `Online ordering is unavailable right now. Please call ${SHOP_PHONE} to order.` } }];

// ---- Input checks (customer sees the message) ----
if (!/^[2-9](?!11)\d{2}[2-9]\d{6}$/.test(digits)) return invalid('Please enter a valid 10-digit phone number.');
if (/https?:\/\/|www\.|\.com\b/i.test(name + ' ' + notes)) return invalid('Please remove web links from your name or notes.');
const asked = (Array.isArray(b.items) ? b.items : []).filter(it => Number(it?.qty) >= 1);
if (asked.some(it => !MENU[String(it?.id)])) return invalid('Our menu just changed. Please refresh the page and add your items again.');
if (!lines.length) return invalid('Your order is empty.');

// ---- Business hours (data/business.json; server-side, can't be bypassed) ----
const hrs = hoursInfo(BIZ, now);
if (!hrs.open) return [{ json: { ...base, verdict: 'invalid', closed: true, message: hrs.message } }];

// ---- Soft limits -> Status "Check" ----
const flags = [];
if (sd.recent.filter(r => r.phone === digits).length >= PHONE_PER_HOUR) flags.push(`More than ${PHONE_PER_HOUR} orders from this phone in an hour`);
if (sd.recent.length >= ORDERS_PER_HOUR) flags.push(`More than ${ORDERS_PER_HOUR} orders in the last hour`);
if (total > BIG_TOTAL || itemCount > BIG_ITEMS) flags.push('Big order — call to confirm');
const status = flags.length ? 'Check' : 'New';

// ---- Number it ----
sd.counter += 1;
const orderNumber = sd.counter;
const emailsThisHour = email ? sd.recent.filter(r => r.email === email).length : 0;
const sendCustomer = !!email && status === 'New' && emailsThisHour < EMAIL_PER_HOUR;
sd.recent.push({ n: orderNumber, phone: digits, email, t: now, cancelled: false });

return [{ json: {
  ...base, verdict: 'order', orderNumber, status, flags: flags.join('; '),
  order: {
    orderNo: orderNumber, placedAt: new Date(now).toISOString(), name, phone: digits, email, notes,
    items: lines.map(l => ({ num: l.num, name: l.name, qty: l.qty, price: l.price })),
    flag: flags.join('; '), sendCustomer, hoursText: hrs.hoursText,
  },
  row: {
    'Order #': orderNumber, 'Received': torontoTime, 'Name': name, 'Phone': phonePretty,
    'Items': itemsText, 'Item count': itemCount, 'Subtotal': subtotal, 'HST': hst, 'Total': total,
    'Status': status, 'Flags': flags.join('; '), 'Notes': notes, 'Cancelled at': '', 'Email': email,
  },
} }];

/* @@SITE_DATA@@ — tools/build.py pastes n8n/site-data.js here; do not edit below this line by hand */

// ===== Shared by the n8n "Validate & Price" and "Peek Counter" code nodes =====
// Copy this block to the BOTTOM of both nodes (n8n code nodes can't import files).
// Source of truth: data/menu.json and data/business.json in GitHub bgopka/georgian-baked-goods,
// served by Vercel at https://georgianbakedgoods.com/data/. Edit those files, never this code.
//
// If the site can't be reached, the last good copy (kept in workflow static data) is used.
// If there has never been a good copy, ordering is closed and customers are asked to call.

async function loadSiteData(helpers, sd) {
  const BASE = 'https://georgianbakedgoods.com/data/';
  const get = (f) => helpers.httpRequest({ method: 'GET', url: BASE + f, json: true, timeout: 6000 });
  try {
    const [menu, biz] = await Promise.all([get('menu.json'), get('business.json')]);
    if (!menu || !Array.isArray(menu.sections) || !biz || !biz.hours) throw new Error('bad shape');
    sd.siteData = { menu, biz, at: Date.now() };
  } catch (e) {
    if (!sd.siteData) return null;
  }
  return sd.siteData;
}

// { '11': { name: 'Imeruli Khachapuri — Large', price: 19 }, ... }  — only items currently on the menu
function priceList(menu) {
  const out = {};
  for (const s of menu.sections) for (const it of s.items) {
    if (it.available === false) continue;
    out[String(it.id)] = { name: it.size ? `${it.name} — ${it.size}` : it.name, price: Number(it.price) };
  }
  return out;
}

// Business hours from data/business.json (24-hour "HH:MM", Toronto time; null = closed).
// specialHours: [{ date: 'YYYY-MM-DD', closed: true } | { date, open, close }] override that one day.
function hoursInfo(biz, nowMs) {
  const DAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const ORDER = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'];
  const cap = (d) => d[0].toUpperCase() + d.slice(1);
  const toMin = (s) => { const m = /^(\d{1,2}):(\d{2})$/.exec(String(s || '')); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
  const label = (min) => { const h = Math.floor(min / 60), m = min % 60, h12 = ((h + 11) % 12) + 1;
    return `${h12}${m ? ':' + String(m).padStart(2, '0') : ''} ${h < 12 ? 'am' : 'pm'}`; };
  const dayHours = (h) => { if (!h) return null; const o = toMin(h.open), c = toMin(h.close); return o != null && c != null && c > o ? { o, c } : null; };

  const week = {};
  for (const d of DAYS) week[d] = dayHours((biz.hours || {})[d]);

  const tz = biz.timezone || 'America/Toronto';
  const at = (ms) => {
    const p = new Intl.DateTimeFormat('en-CA', { timeZone: tz, weekday: 'long', year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ms));
    const g = (t) => p.find((x) => x.type === t).value;
    return { day: g('weekday').toLowerCase(), date: `${g('year')}-${g('month')}-${g('day')}`, mins: Number(g('hour')) * 60 + Number(g('minute')) };
  };
  const special = {};
  for (const s of biz.specialHours || []) if (s && s.date) special[s.date] = s.closed ? null : dayHours(s);
  const hoursOn = (ms) => { const a = at(ms); return a.date in special ? special[a.date] : week[a.day]; };

  const now = at(nowMs);
  const t = hoursOn(nowMs);
  const open = !!t && now.mins >= t.o && now.mins < t.c;

  // "Tue–Sun 11 am to 8 pm, closed Mon"  (same groups the website shows)
  const groups = [];
  for (const d of ORDER) { if (!week[d]) continue; const k = `${label(week[d].o)} to ${label(week[d].c)}`; const last = groups[groups.length - 1];
    if (last && last.k === k && ORDER.indexOf(last.to) === ORDER.indexOf(d) - 1) last.to = d; else groups.push({ k, from: d, to: d }); }
  const closedDays = ORDER.filter((d) => !week[d]);
  const short = (d) => cap(d).slice(0, 3);
  let hoursText = groups.length === 1 && groups[0].from === 'monday' && groups[0].to === 'sunday' ? `${groups[0].k}, 7 days a week`
    : groups.map((g) => `${short(g.from)}${g.to !== g.from ? '–' + short(g.to) : ''} ${g.k}`).join('; ');
  if (closedDays.length && closedDays.length < 7) hoursText += `, closed ${closedDays.map(short).join(', ')}`;
  if (!groups.length) hoursText = 'not available right now';

  let message = '';
  if (!open) {
    let when = '';
    if (t && now.mins < t.o) when = `later today from ${label(t.o)}`;
    else for (let i = 1; i <= 14; i++) {
      const h = hoursOn(nowMs + i * 86400000);
      if (h) { const d = at(nowMs + i * 86400000).day; when = i === 1 ? `tomorrow from ${label(h.o)}` : `on ${cap(d)} from ${label(h.o)}`; break; }
    }
    const phone = (biz.phone && biz.phone.display) || '647 479 8929';
    let todaySpecial = '';
    if (now.date in special) {
      const note = (biz.specialHours.find((s) => s.date === now.date) || {}).note || 'Special hours';
      todaySpecial = t ? `${note}: today we're open ${label(t.o)} to ${label(t.c)}. ` : `${note}: we're closed today. `;
    }
    message = `Sorry we missed you! ${todaySpecial}Online ordering is open ${hoursText}.` +
      (when ? ` We hope to see you back ${when}.` : '') + ` You can also call ${phone}.`;
  }
  return { open, message, hoursText };
}
