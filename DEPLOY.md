# Deploying this Flask blog to Vercel

This app was originally written for a host with a **persistent disk** (Render).
Vercel runs it as a serverless function, so three things had to change.

## What changed

| Change | Why |
| --- | --- |
| `static/` → `public/static/` | Vercel serves static files from the CDN and explicitly says *not* to use Flask's `static_folder`. `url_for('static', ...)` still emits `/static/...`, which resolves to `public/static/...` — so no template URLs changed. |
| Images now go to Telegram | Vercel's filesystem is **read-only** (only `/tmp`, wiped between requests). The old `m_img.save(...)` calls would crash. |
| Secrets read from environment | They were hardcoded in `app.py` and had leaked publicly. |
| `SECRET_KEY` from environment | `os.urandom(24)` regenerated on every cold start, which silently broke sessions and flash messages. |
| `pymongo` 4.3.3 → 4.6.3, `MarkupSafe` 2.1.2 → 2.1.5 | The old versions have no Python 3.12 wheels, so Vercel would try to compile them from source. |
| `requirements.txt` re-saved as UTF-8 | It was UTF-16, which Vercel's build parser cannot read. |

`app.py` itself is already a valid Vercel entrypoint — Vercel looks for a Flask
instance named `app` in `app.py`, so no `api/index.py` shim is needed.

## 1. Create the Telegram image store

1. Talk to **@BotFather** on Telegram → `/newbot` → copy the token.
2. Send your new bot any message (a bot cannot initiate a chat).
3. Create a channel or use your own chat, then find the chat id:

   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

   Look for `"chat":{"id":...}`. For a channel, the id looks like `-1001234567890`.

   **If `getUpdates` returns `409 Conflict`**, the bot already has a webhook
   registered — Telegram forbids `getUpdates` while one is set. Check with:

   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getWebhookInfo
   ```

   Either use a dedicated bot for the blog (recommended), or read the chat id
   from whichever service owns the webhook. Deleting a live webhook can lose
   updates that service was expecting, so don't do it casually.

   Use a **separate bot per concern**. `sendDocument` and `getFile` do not touch
   the webhook, so a bot whose webhook belongs to another service can still be
   used here — but mixing means one token rotation breaks everything.

## 2. Set environment variables

In the Vercel dashboard: **Project → Settings → Environment Variables**.

| Variable | Value |
| --- | --- |
| `MONGO_URI` | your MongoDB Atlas connection string |
| `MAIL_USERNAME` | Gmail address used by Flask-Mail |
| `MAIL_PASSWORD` | Gmail **app password** (not your account password) |
| `SECRET_KEY` | a long random string, e.g. `python -c "import secrets; print(secrets.token_hex(32))"` |
| `TELEGRAM_BOT_TOKEN` | from BotFather |
| `TELEGRAM_CHAT_ID` | from `getUpdates` above |
| `ADMIN_USERNAME` | username for the sign-in page at `/login` |
| `ADMIN_PASSWORD` | password for the same page — use a long random string |
| `SITE_URL` | public address of the site, `https://blog.rohadas.in`. Used for canonical links, Open Graph tags and `sitemap.xml`. Optional — falls back to the request host. |
| `TWITTER_HANDLE` | your X/Twitter handle without the `@`, for `twitter:creator`. Optional. |

Editing is gated behind a session login at **`/login`**. If `ADMIN_USERNAME` /
`ADMIN_PASSWORD` are unset the admin routes return **503** rather than running
unauthenticated, so they fail closed.

## Editing the site

Sign in at **`/login`** and a black toolbar appears at the bottom of every page.

### Editing a post in place

Open any post and press **Edit this page**. The page itself becomes editable:

- **Text** — the title, the intro and every paragraph turn into `contenteditable`
  regions. Click and type.
- **Images** — drag an image file from your desktop straight onto the page, or
  use **+ Image**. It uploads to Telegram as you drop it.
- **Reorder** — every block has a drag handle on the left. Drag it up or down to
  move that block; a red line shows where it will land.
- **Delete** — the &times; button on the right removes a block.
- **Add** — **+ Paragraph** appends a new one.
- **Save** — the bar at the bottom, or Ctrl/Cmd&nbsp;+&nbsp;S. It shows whether
  there are unsaved changes and warns you before leaving the page.

### Creating a post

**+ Travel post** / **+ Work post** in the toolbar. There is **no form** — it
creates a blank post and opens it already in edit mode with the title selected,
so you just start typing. The first time you save, the URL is generated from the
title (`My First Post` → `/blog/my-first-post`, with `-2`, `-3` appended if that
is taken).

### Managing posts

**Manage posts** (`/admin`) lists everything and lets you reorder (&uarr;/&darr;),
edit, view or delete posts. **Edit** opens the post in the inline editor;
**Settings** opens the form, which still covers the fields the inline editor does
not — URL slug, date and category.

## SEO

| What | Where |
| --- | --- |
| Per-page `<title>` + meta description | `seo()` in `app.py`, rendered by `templates/seo_head.html` |
| Canonical URL | same |
| Open Graph + Twitter card | same |
| JSON-LD structured data | `BlogPosting` on posts, `WebSite` elsewhere |
| `robots.txt` | `/robots.txt` — allows everything except `/admin`, `/login`, `/logout`, `/add_blog` |
| `sitemap.xml` | `/sitemap.xml` — every static page plus every post, with `lastmod` |
| Semantic HTML | `<article>`, `<header>`, `<time>`; one `<h1>` per page |
| Image `alt` text | thumbnails use the post heading |

Set **`SITE_URL`** so the canonical, Open Graph and sitemap URLs are absolute and
stable. Without it they fall back to the request host, which is correct on your
production domain but not on preview deployments.

After deploying, submit `https://blog.rohadas.in/sitemap.xml` in
[Google Search Console](https://search.google.com/search-console).


### How content is stored

A post's body is an ordered list of **blocks**:

```json
[{"type": "text",  "html": "<p>…</p>"},
 {"type": "image", "ref": "tg:…"}]
```

Posts written before this existed have no `blocks` field, so `blocks_from_post()`
rebuilds the list from `body1` + `img1` + `img2` in the order the old template
rendered them. **Existing posts look exactly as they did and need no migration** —
they convert to blocks the first time you save from the inline editor.

Writes are protected by a per-session CSRF token (sent as `X-CSRF-Token` for the
`fetch()` calls), and the session cookie is `HttpOnly` + `SameSite=Lax`.
Anonymous visitors receive no cookie at all, which keeps the public HTML
cacheable at the edge.

Locally, put the same values in `.env` (git-ignored) — see `.env.example`.

> **Rotate the credentials that were committed.** The old MongoDB password,
> the Gmail app password and the Atlas URI are in this repo's git history and
> must be considered public.

## 3. Deploy

```bash
npm i -g vercel     # or: npx vercel
vercel              # first run links the project
vercel --prod       # production deploy
```

Or push to GitHub and import the repo at <https://vercel.com/new>. Vercel
detects Flask automatically from `requirements.txt` + `app.py`.

Run it locally exactly as before:

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows
pip install -r requirements.txt
python app.py               # http://127.0.0.1:5000
```

## Pointing blog.rohadas.in at Vercel

`rohadas.in` is registered at **Hostinger** and currently uses its nameservers
(`pixel.dns-parking.com`, `byte.dns-parking.com`). There are two ways to point
the subdomain at Vercel — pick **one**, not both.

### Where the subdomain is created: always Vercel

**Add `blog.rohadas.in` under Project → Settings → Domains → Add.** Vercel then
owns the hostname and creates the DNS record for it. Nothing needs to be created
at Hostinger first.

Do **not** use Hostinger's **Domains → Subdomains** screen. That feature points a
subdomain at *Hostinger* hosting — it provisions a folder and an `A` record aimed
at a Hostinger IP. With the site running on Vercel that record conflicts and
surfaces as **Invalid Configuration** on Vercel's Domains screen. Only the *DNS
Zone Editor* is ever relevant at Hostinger, and only for option A below.

### Option A — CNAME (least invasive)

Keep Hostinger's nameservers and add a single record in Hostinger's **DNS Zone
Editor**:

| Type | Name | Value |
| --- | --- | --- |
| `CNAME` | `blog` | the unique target Vercel shows for this project |

**Each project gets its own unique CNAME target** (something like
`d1d4fc829fe7bc7c.vercel-dns-017.com`) — copy it from that Domains screen rather
than reusing a value seen elsewhere.

Subdomains use a **CNAME**; only apex domains use an **A** record. Because `blog`
is a subdomain, this adds one record and leaves everything else on `rohadas.in`
alone — including any **MX records for email**.

### Option B — Vercel nameservers (the whole zone moves to Vercel)

At Hostinger: **DNS → rohadas.in → DNS / Nameservers tab → Change Nameservers →
"Change nameservers"**, then enter:

```
ns1.vercel-dns.com
ns2.vercel-dns.com
```

This delegates the **entire zone** to Vercel, so:

- No CNAME is needed — apex and first-level subdomain records are created
  automatically, and wildcard domains become possible.
- **Every existing record must also exist in Vercel.** MX and TXT records are
  *not* copied across; on a domain with email, mail would stop arriving.
  `rohadas.in` has **no MX and no TXT records**
  (`nslookup -type=MX rohadas.in` returns only the SOA), so nothing is lost here
  — but re-check this before doing it on any other domain.
- The apex parking page (`2.57.91.91`) stops resolving — see "redirect the apex
  domain" below.
- All later DNS edits happen in **Vercel → Settings → Domains → DNS Records**.
  Hostinger's DNS editor becomes inert.
- Hostinger advises allowing up to 24 hours to propagate; a few hours is typical,
  and the site can be unreachable during the move.

### Then, either way

1. **Set `SITE_URL`** to `https://blog.rohadas.in` in Vercel's environment
   variables, so canonical links, Open Graph tags and the sitemap all use the
   real domain. It is already set in the local `.env`, but `.env` is
   git-ignored — Vercel will not see it until you add it there too.

2. **Wait for the certificate.** Vercel issues TLS automatically once DNS
   resolves. Usually a few minutes; DNS propagation can take longer.

3. **Check it:**
   - `https://blog.rohadas.in/robots.txt` → should say
     `Sitemap: https://blog.rohadas.in/sitemap.xml`
   - `https://blog.rohadas.in/sitemap.xml` → every `<loc>` should start with
     `https://blog.rohadas.in`
   - The page source should show
     `<link rel="canonical" href="https://blog.rohadas.in/...">`

4. **Submit the sitemap** in
   [Google Search Console](https://search.google.com/search-console), creating
   a property for `blog.rohadas.in`.

### Optional: redirect the apex domain

If you want `rohadas.in` to send visitors to the blog, add it to the same Vercel
project and set it to **redirect** to `blog.rohadas.in`
(Settings → Domains → Edit → Redirect).

Under **option A** this is purely optional — `rohadas.in` keeps its current
parking page, because a `blog` CNAME does not touch the apex. Under **option B**
it is effectively required, since Vercel now controls the apex and the old
parking page (`2.57.91.91`) no longer resolves.


## Limits you need to know

- **4.5 MB per request and per response.** Vercel caps a function's body at
  4.5 MB, which is far tighter than Telegram's own 20 MB download limit. This
  means an uploaded image must be under ~4 MB, and an image served through
  `/media/<file_id>` must also stay under 4.5 MB. Uploads larger than
  `MAX_UPLOAD_BYTES` (4 MB) are rejected with a clear error instead of failing
  with a confusing `413 FUNCTION_PAYLOAD_TOO_LARGE`.
- **Images served through `/media` cost one function invocation** on a cache
  miss. The route sends `Cache-Control: public, max-age=31536000, immutable`,
  so Vercel's CDN serves repeat views without touching the function.
- **Legacy images are unaffected** — they still live in
  `public/static/images/post_images/` and are served straight from the CDN.
  `media_url()` picks the right source based on the `tg:` prefix.
- **Cold starts.** The first request after idle pays Python + Mongo + TLS
  startup. `maxDuration` is set to 60s, well inside Hobby's 300s ceiling.

## Tests

```bash
python -m unittest discover -s tests -v
```

71 tests covering the Telegram upload/serve path with the HTTP calls mocked,
including that the bot token is never exposed to the browser. They run against an
in-memory `FakeDb`, so no MongoDB connection is needed and the whole suite
finishes in well under a second.

There is also a **live** check that talks to the real Telegram API — it catches
a wrong token, a wrong chat id, or Telegram's actual response headers:

```bash
python tests/live_telegram_check.py
```

It uploads one image to your chat each run and does not touch MongoDB.

### Gotcha the live check exists to catch

Telegram serves every stored document as **`Content-Type: application/octet-stream`
with `Content-Disposition: attachment`**. Trusting those headers makes browsers
download images instead of rendering them. `/media` therefore detects the real
type from the file's magic bytes and forces `Content-Disposition: inline`.
Mocked tests did not catch this — only a real request did.


## Pre-existing issues

Fixed during the Vercel migration:

- **`/add_blog` had no authentication** and emailed every subscriber on each
  post. Editing now requires a signed-in session.
- **Images with no extension or a non-`.jpg` one 404'd**, because templates
  appended `.jpg` to the stored stem. `media_url()` now resolves the real file
  on disk (e.g. `taarit_api_1`, `Fundlet..png`).
- **The error page always said "Page Not Found"**, even for server errors, and
  its title was literally `Document`. It now takes a heading and message, and
  links back home.
- **`about_us.html` had unclosed `<div>`s**, no navigation, no footer, and all
  its text in a single element — so it rendered as one wall of text. Rebuilt on
  the site theme with real paragraphs and nav.
- **Every page had an empty `description`/`author`** and a generic title. Blog
  posts now get a per-post title and description.
- **All images had empty `alt` attributes**; thumbnails now use the post
  heading and load lazily.
- **`img2` was rendered but never stored.** The new editor uploads it properly.
- **The legacy add form's description field was named `description`** while
  `app.py` read `discription`, so it was silently dropped. Renamed.

Still outstanding:

- The legacy form at `/add_blog` still reads its `img2` input with
  `request.form.get` and never saves it. Use `/admin/new` instead, which handles
  all three images.
- Stray files in `public/static/images/post_images/` —
  `post_imagesindex21.html`, `post_imagesGaming_5000x3125.jpg`,
  `post_imagespune-shanivar-vada.jpg` — look like artifacts of a filename that
  contained a path. They are unused.
- `Gaming_5000x3125.jpg` is 6.7 MB — fine as a CDN-served static file, but it
  could not be uploaded through the editor under the 4.5 MB limit.
- The committed `env/` virtualenv and `purblind/Blog.zip` are still tracked in
  git. `.vercelignore` keeps them out of deployments, but removing them would
  be cleaner.



