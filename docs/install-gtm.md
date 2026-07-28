# Installing minuano with Google Tag Manager

Two tags. The first loads the snippet on every page; the second fires custom events from the
GTM UI without touching the site's code.

This is the **Custom HTML** route, which works today in any GTM container. A proper Custom
Template for the Community Gallery is on the roadmap and is not what this document describes —
see [Why the GET path exists](#why-the-get-path-exists) for what changes when it lands.

---

## First: where does `minuano.min.js` live?

**The collector does not serve the snippet.** It exposes `/collect` and `/health`, and nothing
else — so there is no URL to put in a `<script src>` unless you create one. This is the step that
stops a first install, and the answer below is deliberately the one that needs no hosting at all.

| Option | Cost | When |
|---|---|---|
| **Inline the snippet in the tag** | none | **Start here.** No hosting, no CDN account, no extra request. 2.8KB of inline JS is unremarkable for a GTM Custom HTML tag |
| Serve it from the collector | a route | Best long-term: one origin, and the snippet can never drift out of step with the collector that receives its events |
| A CDN (jsDelivr off the GitHub repo, Cloudflare) | an account, or a git tag per release | A third party in the critical path of every page load, for a 2.8KB file |

### Inline install

Paste [`gtm-tag-inline.html`](gtm-tag-inline.html) — it is the config block and the whole
minified snippet in one self-contained tag, already pointed at a live collector. Regenerate it
whenever the snippet changes:

```bash
{ printf '<script>\n  window.minuano = window.minuano || [];\n  window.minuanoConfig = { endpoint: "%s" };\n</script>\n<script>\n' "$ENDPOINT"
  cat snippet/minuano.min.js
  printf '\n</script>\n'; } > docs/gtm-tag-inline.html
```

The trade is that updating the snippet means republishing the container. With one consumer that is
a non-issue; GTM's version history makes it a two-click rollback.

---

## Tag 1 — load the snippet

**Tag type:** Custom HTML
**Trigger:** Initialization — All Pages
**Advanced settings → Tag firing options:** Once per page

Either paste `gtm-tag-inline.html` verbatim, or — once the snippet is hosted somewhere — use the
two-part form:

```html
<script>
  window.minuano = window.minuano || [];
  window.minuanoConfig = {
    endpoint: 'https://collect.example.com/collect'
    // cookieDomain: '.example.com'   // set this to share the visitor across subdomains
    // sessionMinutes: 30             // the standard default; changing it changes your session counts
  };
</script>
<script async src="https://cdn.example.com/minuano.min.js"></script>
```

Replace both URLs with your own. The first `<script>` block matters as much as the second: it
declares the **queue stub**, so a `minuano.track()` call that runs before the snippet finishes
downloading is replayed rather than lost. Without it, early events on slow connections vanish
silently. When inlining, keep the two blocks in that order — `minuanoConfig` has to exist before
the snippet reads it.

Use the **Initialization** trigger, not All Pages, so the snippet is in place before any other
tag tries to call it.

### What the snippet does on load

- Reads `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term` from the URL, plus
  `gclid` and `fbclid` into `params`.
- Persists **first touch** (`_mnu_ft`, 2 years) and **last touch** (`_mnu_lt`, 180 days) in cookies.
- Persists `anonymous_id` (`_mnu_id`, 2 years) and `session_id` (`_mnu_ses`, 30-minute sliding
  expiry — the cookie's own lifetime *is* the inactivity window).
- Fires `page_view`.

Cookies rather than localStorage, because GTM's sandboxed templates can read and write cookies.
One storage model across both install methods.

---

## Tag 2 — fire a custom event

**Tag type:** Custom HTML
**Trigger:** whatever should fire it (a click, a form submission, a Custom Event from `dataLayer`)

```html
<script>
  window.minuano = window.minuano || [];
  window.minuano.track
    ? minuano.track({{Event Name}}, { plan: {{Plan}}, seats: {{Seats}} })
    : minuano.push([{{Event Name}}, { plan: {{Plan}}, seats: {{Seats}} }]);
</script>
```

`{{Event Name}}`, `{{Plan}}` and `{{Seats}}` are GTM variables — Data Layer Variables, in the
usual case. The ternary is what makes this tag safe at any firing order: if the snippet is
already loaded it calls through, and if it is not, the event queues.

### Rules the event has to satisfy

| Field | Rule |
|---|---|
| event name | `^[a-z][a-z0-9_]{0,39}$` — lowercase, digits, underscores. Anything else is ignored with a console warning |
| reserved names | `page_view`, `session_start`, `first_visit` are derived downstream. Do not send them yourself |
| params | flat only, max 25, values must be string / number / boolean / null. A nested object is dropped with a console warning |

Sending an event that breaks these rules never loses data: the collector stores it under
`data/bad/` with the validation errors attached, so it can be fixed and replayed.

---

## Verifying the install

1. GTM **Preview** mode — confirm both tags fire, and in the right order.
2. Browser devtools → **Network**, filter on `collect`. You should see a `POST` with a
   `text/plain` content type (that is `sendBeacon`, and the reason there is no CORS preflight).
3. Browser devtools → **Application → Cookies** — `_mnu_id`, `_mnu_ses`, `_mnu_ft` should be set.
4. On the collector, `events/dt=<today>/` should have a new `.ndjson` file within the flush
   interval. If an event is missing from there, look in `bad/` before assuming it was dropped —
   the collector never drops anything.

   Against a cloud sink, that is:

   ```bash
   gcloud storage ls "gs://<bucket>/raw/events/dt=$(date -u +%F)/"   # GCS
   aws s3 ls "s3://<bucket>/raw/events/dt=$(date -u +%F)/"           # S3
   ```

   `dt` is the **ingest** date in UTC, not the event date — see error.md, TRAP-9.

---

## Cross-origin and consent

- **CORS.** The collector answers `Access-Control-Allow-Origin`, configurable with
  `MINUANO_CORS_ORIGINS`. Set it to your domains in production rather than leaving it `*`.
- **Consent.** minuano sets first-party cookies. Gate Tag 1 behind your consent trigger if your
  jurisdiction requires it; there is no cookieless mode in v0.

---

## Why the GET path exists

The collector also accepts `GET /collect?e=<base64url-encoded JSON>` and answers with a 1×1 GIF.
Nothing in this document uses it — Custom HTML tags run unsandboxed and can `POST`.

It exists for the **Custom Template** that comes later. Templates run in sandboxed JavaScript
where the outbound data API is `sendPixel`, and `sendPixel` is GET only. Building the endpoint now
means the template will not require a collector rewrite.

A **server-side GTM** tag template is also planned. It will `POST` plain JSON to the same
`/collect` endpoint and needs nothing new from the collector — the visitor's context (user agent,
language, platform) travels in the payload, because the collector derives nothing from the request
socket. That is what stops relayed events from being stamped with the container's identity.
