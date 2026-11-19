// POST /api/publish — { key, draft, mode: "merge" | "replace" }
// Saves a new version of the day's edition and notifies every subscribed device.
const crypto = require("crypto");
const webpush = require("web-push");

const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_ANON_KEY;

async function sb(path, init = {}) {
  const r = await fetch(`${SB_URL}/rest/v1/${path}`, {
    ...init,
    headers: { apikey: SB_KEY, Authorization: `Bearer ${SB_KEY}`, "Content-Type": "application/json", ...(init.headers || {}) },
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`Database error ${r.status}: ${text.slice(0, 200)}`);
  return text ? JSON.parse(text) : null;
}
const rpc = (fn, args) => sb(`rpc/${fn}`, { method: "POST", body: JSON.stringify(args) });

function sameKey(a, b) {
  const ka = Buffer.from(String(a || "")), kb = Buffer.from(String(b || ""));
  return ka.length === kb.length && crypto.timingSafeEqual(ka, kb);
}

function buildEdition(draft, current, mode, version, now) {
  const incoming = (draft.stories || []).filter((s) => !s.hidden);
  const prev = current ? current.data.stories : [];
  const prevByKey = new Map(prev.map((s) => [s.key || s.url, s]));
  const clean = (s) => { const { hidden, fetchError, ...rest } = s; return rest; };
  let stories;
  if (mode === "replace" || !current) {
    stories = incoming.map((s) => ({ ...clean(s), firstVersion: prevByKey.get(s.key || s.url)?.firstVersion ?? version }));
  } else {
    const byKey = new Map(prev.map((s) => [s.key || s.url, { ...s }]));
    for (const s of incoming) {
      const k = s.key || s.url, old = byKey.get(k);
      byKey.set(k, { ...clean(s), firstVersion: old ? old.firstVersion : version });
    }
    stories = [...byKey.values()];
  }
  const sections = [...new Set([...(draft.sections || []), ...stories.map((s) => s.section)])].filter((n) => stories.some((s) => s.section === n));
  // Keep stories grouped by section in section order, stable inside a section.
  stories.sort((a, b) => sections.indexOf(a.section) - sections.indexOf(b.section));
  stories.forEach((s, i) => { s.id = `${String(i + 1).padStart(2, "0")}-${(s.id || "").replace(/^\d+-/, "")}`; });
  const added = stories.filter((s) => s.firstVersion === version).length;
  return {
    date: draft.date,
    subject: draft.subject,
    version,
    publishedAt: now,
    versions: [...(current?.data.versions || []), { version, at: now, added, total: stories.length }],
    sections,
    leads: draft.leads || current?.data.leads || {},
    stories,
  };
}

async function notifyAll(edition, siteUrl) {
  webpush.setVapidDetails(siteUrl, process.env.VAPID_PUBLIC_KEY, process.env.VAPID_PRIVATE_KEY);
  const subs = await rpc("list_push_subs", { p_secret: process.env.PUBLISH_SECRET });
  const S = edition.stories, v = edition.version;
  const gulf = S.filter((s) => s.section === "Gulf in the News").length;
  const day = new Date(edition.date + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" });
  const added = edition.versions[edition.versions.length - 1].added;
  const payload = JSON.stringify({
    title: v === 1 ? `Gulf Pulse · ${day}` : `Gulf Pulse updated · ${day}`,
    body: v === 1
      ? `${S.length} stories today${gulf ? ` · Gulf in the news ${gulf}×` : ""}. Tap to read the brief.`
      : `${added} new ${added === 1 ? "story" : "stories"} added. Tap to see what's new.`,
    url: "/",
    tag: `edition-${edition.date}`,
  });
  let sent = 0, removed = 0;
  await Promise.all(subs.map(async (s) => {
    try {
      await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, payload, { TTL: 60 * 60 * 12 });
      sent++;
    } catch (e) {
      if (e.statusCode === 404 || e.statusCode === 410) {
        removed++;
        await rpc("remove_push_sub", { p_secret: process.env.PUBLISH_SECRET, p_endpoint: s.endpoint }).catch(() => {});
      }
    }
  }));
  return { sent, removed, total: subs.length };
}

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
  const { key, draft, mode = "merge", notify = true } = req.body || {};
  if (!sameKey(key, process.env.UPLOAD_KEY)) return res.status(403).json({ error: "This upload link is not valid. Use the full link you were given." });
  if (!draft || !/^\d{4}-\d{2}-\d{2}$/.test(draft.date || "") || !Array.isArray(draft.stories)) {
    return res.status(400).json({ error: "Nothing to publish. Upload the email again." });
  }
  try {
    const rows = await sb(`editions?date=eq.${draft.date}&select=version,data`);
    const current = rows[0] || null;
    const version = (current?.version || 0) + 1;
    const now = new Date().toISOString();
    const edition = buildEdition(draft, current, mode, version, now);
    if (!edition.stories.length) return res.status(400).json({ error: "Every story is hidden. Show at least one story to publish." });
    await rpc("save_edition", { p_secret: process.env.PUBLISH_SECRET, p_date: draft.date, p_version: version, p_data: edition });
    const site = `https://${req.headers["x-forwarded-host"] || req.headers.host}`;
    let push = { sent: 0, removed: 0, total: 0 };
    if (notify) push = await notifyAll(edition, site).catch((e) => ({ error: String(e.message || e).slice(0, 160), sent: 0 }));
    return res.status(200).json({ date: draft.date, version, stories: edition.stories.length, added: edition.versions.at(-1).added, push });
  } catch (e) {
    return res.status(500).json({ error: String(e.message || e).slice(0, 300) });
  }
};

module.exports.buildEdition = buildEdition;
