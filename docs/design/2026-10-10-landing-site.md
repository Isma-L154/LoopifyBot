# Landing site

## Goal

Give people who only want to use the bot a page to land on instead of the
GitHub repository: what the bot does, the invite link, how to start, the
command list and answers to common questions.

## Decisions

- **One static page, English only.** Sections are anchors (`#features`,
  `#start`, `#commands`, `#faq`), so every section can be linked and the back
  button behaves normally. No router.
- **No build step, no npm dependencies.** Plain HTML, CSS and one small script.
  Nothing for Dependabot to track, and a strict CSP is easy to keep.
- **Hosted on Cloudflare**, as an assets-only Worker on
  `loopify.cloudils.com`. GitHub Pages was rejected because it cannot send
  response headers: `frame-ancestors`, HSTS and `nosniff` would have to live in
  a Cloudflare rule outside the repository.
- **The brand of the README banner** (`docs/brand/banner.html`): the dark
  indigo palette, Righteous for display, Poppins for text, Lucide icons. Dark
  only. Fonts are self-hosted so the CSP stays at `'self'`.

## Page

1. **Header**, sticky: logo, links to Features, Commands and FAQ, and the green
   **Add to Discord** button. Under 640px only the logo and the button remain.
2. **Hero**: headline, one line of value, **Add to Discord** and **View
   commands** buttons, and an HTML recreation of the Now Playing card with its
   buttons, as in the banner. The card is decorative (`aria-hidden`).
3. **Features**: six cards — YouTube and SoundCloud, shared queue, eight live
   effects, synced lyrics, `/` and `!` commands with buttons, leaves on its own
   after five minutes idle.
4. **Getting started**: three numbered steps — invite, join a voice channel,
   `/play` — and what the requested permissions are for (Send Messages, Embed
   Links, Read Message History, Connect, Speak, Use Voice Activity).
5. **Commands**: tabs for Playback, Queue, Audio and Extras, and a search box
   that filters as you type. The commands are written in the HTML, each row
   carrying `data-command="<name>"`, so the list renders without JavaScript;
   the script only adds the tabs and the filter.
6. **FAQ**: native `<details>` elements, keyboard-accessible without JS.
   Silence in the voice channel; a YouTube track that fails (try `sc:`); what
   data the bot keeps (none — no database); whether it is free; uptime
   (self-hosted, best effort); hosting your own copy (link to the repository).
7. **Footer**: GitHub, Report a bug, MIT license.

Open Graph and Twitter card tags point to `og.png` (the README banner), so the
link unfurls with an image when pasted into Discord.

## Files

```
site/
├── wrangler.jsonc     # assets-only Worker, custom domain loopify.cloudils.com
└── public/            # the only directory uploaded
    ├── index.html
    ├── styles.css
    ├── app.js
    ├── _headers
    ├── fonts/         # Righteous and Poppins woff2, from the banner
    ├── favicon.svg
    └── og.png
```

`wrangler.jsonc` sits outside `public/` so it is never served.

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

It needs two repository secrets, created by the owner:

- `CLOUDFLARE_API_TOKEN` — the **Edit Cloudflare Workers** template, scoped to
  the account and the `cloudils.com` zone. Creating the Worker on the first
  deploy and attaching the custom domain both need it.
- `CLOUDFLARE_ACCOUNT_ID`.

Cloudflare creates the DNS record and certificate for the custom domain. It
refuses if a record for `loopify` already exists.

## Tests

Both live in `tests/test_site.py` and run in the existing `pytest` job. They
parse `index.html` with the standard library's `html.parser`.

- **Command sync.** The set of `data-command` values equals the set of
  non-hidden commands the bot registers with every cog in `COGS` loaded — the
  same registry `tests/test_help.py` checks `!help` against. A command added to
  the bot and not to the page, or removed from the bot and left on the page,
  fails the test.
- **Nothing inline.** No `<script>` without `src`, no `<style>` element, no
  `style` attribute and no `on*` attribute. The CSP blocks all of them without
  any visible error, so the page would break silently in production.

Before the pull request: the page is opened with Playwright at 375, 768 and
1440px, the console is checked for CSP violations, and Lighthouse runs for
accessibility and performance.

## Docs

The README links to the site, documents previewing it locally
(`npx wrangler dev` from `site/`, which applies `_headers`), and its Security
section covers the site's CSP.
