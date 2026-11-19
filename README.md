# Gulf Pulse

A phone-first daily news bulletin for Gulf Oil Lubricants India leadership, built from the
media-tracker email that Adfactors PR sends every morning.

## How it works

| Piece | What it does |
|-------|--------------|
| `public/index.html` | The bulletin: Brief (Gulf mentions + one tile per category), category lists, full-screen stories (crux → full story → clipping), clippings wall, editions calendar. Installable as an app; notifications via Web Push. |
| `public/upload.html` | The publish page: upload the `.msg`, review (hide / star lead / edit crux / change category), preview, publish. Opened with a private link: `/upload?key=…`. |
| `api/ingest.py` | Reads the `.msg`, groups stories by the email's section headings, merges duplicates, fetches full text + clipping images from the media-tracker links and photos for online stories. Returns a draft; nothing is saved. |
| `api/publish.js` | Saves the draft as the next version of that day's edition (merged by story link, new stories marked), then sends a notification to every subscribed device. |
| `supabase/schema.sql` | Tables: `editions` (latest version per day, public read), `edition_versions` (history), `push_subs`, `app_config`. Writes go through functions gated by a publish secret. |

## Environment variables (Vercel)

`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `PUBLISH_SECRET`, `UPLOAD_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`.
`public/config.js` holds the public Supabase URL, publishable key and VAPID public key.

## Daily routine

1. Save the tracker email from Outlook as `.msg`.
2. Open the publish link, drop the file in, review, tap **Publish**.
3. A later email the same day: upload it again. It becomes version 2, new stories are marked, and everyone is notified.

`tools/ingest/ingest_msg.py` and `prototype/` are the earlier design-phase tools.
