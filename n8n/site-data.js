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
