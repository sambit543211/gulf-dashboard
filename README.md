# Gulf Pulse

A phone-first daily news bulletin for Gulf Oil Lubricants India leadership. It is built
from the media-tracker email that Adfactors PR sends every morning.

## What's here

| Path | What it is |
|------|------------|
| `prototype/index.html` | Design prototype: dark, immersive feed with category tabs, full-screen stories (crux, then full story, then clipping) and an edition picker |
| `tools/ingest/ingest_msg.py` | Turns one tracker `.msg` email into an edition JSON. It also fetches each print story's full text and newspaper clipping from the media-tracker link. |

## Run the prototype locally

```bash
pip install olefile pillow
python3 tools/ingest/ingest_msg.py "Gulf_Oil_Lubricants_India_News_-_19th_November_2026.msg" prototype/data
cd prototype && python3 -m http.server 8000   # then open http://localhost:8000
```

Each run writes `prototype/data/<date>.json`, adds that day's clippings under
`prototype/data/clips/<date>/` and rebuilds `prototype/data/index.json`. The Archive
view lists every edition in `index.json`.

## Daily flow (planned)

1. Someone on the team uploads that morning's `.msg` email on an admin page.
2. The ingester parses the stories and fetches the full text and clippings.
3. The edition is stored and published. If the same date is uploaded again, it updates
   that edition and its "updated at" time.
4. Leadership opens the bulletin on their phones and can go back to any earlier edition.
