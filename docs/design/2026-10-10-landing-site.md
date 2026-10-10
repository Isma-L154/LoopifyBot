# Landing site

## Goal

Give people who only want to use the bot a page to land on instead of the
GitHub repository: what the bot does, the invite link, how to start, the
command list and answers to common questions. The page is indexed through
Google Search Console and shared as a link in Discord, so it must be
crawlable and unfurl with a card.

## Decisions

- **One static page, English only**, at `https://loopify.cloudils.com/`.
  Sections are anchors (`#features`, `#setup`, `#commands`, `#troubleshooting`),
  so every section can be linked and the back button behaves normally.
- **No build step, no npm dependencies.** Plain HTML, CSS and one small script.
  Nothing for Dependabot to track, and a strict CSP is easy to keep.
- **Hosted on Cloudflare**, as an assets-only Worker with a custom domain.
  GitHub Pages was rejected because it cannot send response headers.
- **Look: hi-fi hardware.** The page reads like the manual of a piece of audio
  gear, and the hero is the bot drawn as a stereo. This was chosen over three
  SaaS-style directions (gradient text, glows, bento grids) because those read
  as generated. Fonts are self-hosted so the CSP stays at `'self'`.

## Visual system

| Token | Value | Use |
|---|---|---|
| `--bg` | `#121212` | page |
| `--panel` | `#1b1b19` | step cards |
| `--line` | `#2f2e2b` | rules and borders |
| `--ink` | `#e8e4da` | text |
| `--muted` | `#9a958a` | secondary text |
| `--amber` | `#ff8a1f` | the one action colour: buttons, section labels, active keys |
| `--lcd` | `#7dff9b` on `#0b1a0f` | only inside LCD screens |

- **Chakra Petch** 500/600 for headings and labels (uppercase), **IBM Plex
  Sans** 400/500/600 for text, **IBM Plex Mono** 400 for commands, **VT323** for
  LCD screens. **Righteous** only for the LoopifyBot wordmark. All five are SIL
  Open Font License, served as woff2 from `fonts/`.
- Icons are inline SVG. No emoji as icons.
- Dark only. Text contrast at least 4.5:1 (`--muted` on `--bg` is about 6:1).
- Visible focus ring on every control; a skip link to `#main`.

## Page

1. **Header**, sticky: wordmark with a green LED, links to Features, Commands
   and FAQ, and the amber **Add to Discord** button. Under 640px only the
   wordmark and the button remain.
2. **Hero**: *A stereo for your voice channel*, one line of copy, **Add to
   Discord** and **View commands**. Below it, the deck: an LCD with track
   number, time, song, the current and previous lyric line and a VU meter;
   four transport keys; four effect knobs (Bass, Nightcore, 8D, Echo); a
   model plate, *LOOPIFY · MODEL LB-1 · STEREO VOICE RECEIVER*.
3. **01 · Spec sheet** (`#features`): six cells in a ruled grid — Sources,
   Shared queue, Eight effects, Synced lyrics, Three ways to control, Auto
   power-off.
4. **02 · Setup** (`#setup`): three steps with LCD-style numbers — add it (needs
   Manage Server), join a voice channel, type `/play`. Then the permissions it
   asks for: send messages, embed links, read message history, connect, speak,
   use voice activity.
5. **03 · Controls** (`#commands`): selector keys for Playback, Queue, Audio and
   Extras, and a search field styled as an LCD. A table of command, description
   and `!` aliases. Every command is written in the HTML, each row carrying
   `data-command="<name>"`, so the list renders without JavaScript; the script
   only adds the selector and the filter. With JS off, all four groups show
   one after another.
6. **04 · Troubleshooting** (`#troubleshooting`, linked as FAQ): native
   `<details>` entries labelled like error codes — E-01 joined but silent;
   E-02 a YouTube song fails and is skipped (try `sc:`); E-03 what data it
   keeps (none — no database); E-04 free and uptime (self-hosted, best effort);
   E-05 running your own copy (link to the repository).
7. **Footer**: *LOOPIFY · MIT LICENSE*, GitHub, Report a bug.

### The live deck

`app.js` runs a demo on the deck. Every control is a real `<button>` with an
accessible name.

- The LCD steps through a few invented songs and invented lyric lines, and the
  VU meter moves. Nothing is real lyrics.
- **⏯** pauses and resumes the demo, which also covers WCAG 2.2.2 (moving
  content can be paused). **⏮ ⏭** change the demo song. **⏹** stops it.
- Pressing a knob shows that effect on the LCD, for example
  *NIGHTCORE · 1.25× SPEED, HIGHER PITCH*.
- Under `prefers-reduced-motion: reduce` the demo starts paused and the VU
  meter is static. It also pauses while the deck is off screen
  (`IntersectionObserver`) or the tab is hidden.
- Without JavaScript the deck shows one static frame.

## Search and sharing

- **Head**: `<title>`, meta description, `<link rel="canonical"
  href="https://loopify.cloudils.com/">`, `theme-color` `#ff8a1f` (Discord uses
  it for the embed's side bar), `favicon.svg` and a 180px
  `apple-touch-icon.png`.
- **Card**: Open Graph (`og:type`, `og:url`, `og:title`, `og:description`,
  `og:image` as an absolute URL, `og:image:width`/`height`/`alt`) and
  `twitter:card` `summary_large_image`. The image is `og.png`, 1200×630.
- **Structured data**: one `<script type="application/ld+json">` block of type
  `SoftwareApplication` (name, description, URL, `applicationCategory`
  `MultimediaApplication`, `operatingSystem` Discord, a free `Offer`). It is
  data, not script, so the CSP does not block it.
- **`robots.txt`** allows everything and points to the sitemap.
- **`sitemap.xml`** lists the one URL. No `lastmod`: Google ignores it unless it
  stays accurate, and nothing would keep it accurate.
- **`404.html`** in the same style (*E-404 · NO SIGNAL*), served with a real
  404 status, so Search Console does not report soft 404s.
- **No duplicate hosts**: `workers_dev` and `preview_urls` are off, so the page
  exists only on the custom domain.
- **Search Console verification** is done by the owner with a DNS TXT record
  in Cloudflare (a Domain property). Nothing about it lives in the repository.

### One drawing for the banner and the card

`docs/brand/banner.html` is redrawn in the hi-fi style and laid out in
viewport units, so the same file renders the README banner at 1280×640 and
`site/public/og.png` at 1200×630. The README's badge colour moves from indigo
to the amber.

## Files

```
site/
├── wrangler.jsonc     # assets-only Worker, custom domain, 404 handling
└── public/            # the only directory uploaded
    ├── index.html
    ├── 404.html
    ├── styles.css
    ├── app.js
    ├── _headers
    ├── robots.txt
    ├── sitemap.xml
    ├── fonts/
    ├── favicon.svg
    ├── apple-touch-icon.png
    └── og.png
```

`wrangler.jsonc` sits outside `public/` so it is never served. It sets
`assets.directory` to `./public`, `assets.not_found_handling` to `404-page`,
`workers_dev: false`, `preview_urls: false`, and one route,
`loopify.cloudils.com` with `custom_domain: true`.

## Security headers

`site/public/_headers` applies to every path:

- `Content-Security-Policy: default-src 'self'; img-src 'self' data:;
  object-src 'none'; base-uri 'self'; form-action 'none';
  frame-ancestors 'none'` — no `unsafe-inline`, no `unsafe-eval`.
- `Strict-Transport-Security: max-age=31536000`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`

The site has no backend, no secrets and no user input that leaves the browser,
so CORS, input validation, rate limiting and RLS do not apply to it.

## Deployment

`.github/workflows/deploy-site.yml` runs on `workflow_run` when `Tests`
completes on `main`, and deploys only if it succeeded. It never runs for pull
requests. The job uses the `production` environment, `permissions: contents:
read`, its own `concurrency` group without cancellation, and
`cloudflare/wrangler-action` with a pinned Wrangler version.

It needs two repository secrets, set by the owner: `CLOUDFLARE_API_TOKEN`
(the **Edit Cloudflare Workers** template, scoped to the account and the
`cloudils.com` zone) and `CLOUDFLARE_ACCOUNT_ID`. Cloudflare creates the DNS
record and certificate for the custom domain.

## Tests

`tests/test_site.py`, in the existing `pytest` job. It parses the HTML with the
standard library's `html.parser` and `xml.etree`.

- **Command sync.** The set of `data-command` values equals the set of
  non-hidden commands the bot registers with every cog in `COGS` loaded — the
  same registry `tests/test_help.py` checks `!help` against. The effect
  shortcuts (`!nightcore`, `!8d`…) are aliases, not commands, so their rows
  carry `data-alias` instead, and that set must equal the registered aliases.
- **Nothing inline.** No `<script>` without `src` other than
  `type="application/ld+json"`, no `<style>` element, no `style` attribute and
  no `on*` attribute, in `index.html` and `404.html`. The CSP blocks all of
  them without any visible error.
- **One canonical URL.** The canonical link, `og:url`, the JSON-LD `url`, the
  sitemap's `<loc>` and the `robots.txt` sitemap line all use the custom domain
  in `wrangler.jsonc`, and `og:image` is absolute on that domain.
- **Local references exist.** Every local `href`/`src` in the HTML, and every
  `url()` in `styles.css`, points to a file in `public/`.

Before the pull request: the page is opened with Playwright at 375, 768 and
1440px, the console is checked for CSP violations, the deck is driven by
keyboard, and Lighthouse runs for accessibility, SEO and performance.

## Docs

The README links to the site, documents previewing it locally
(`npx wrangler dev` from `site/`, which applies `_headers`) and regenerating
`og.png`, and its Security section covers the site's CSP.
