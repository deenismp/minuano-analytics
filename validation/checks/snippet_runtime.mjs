/**
 * Runs snippet/minuano.js against a minimal DOM shim, so the snippet's behaviour can be
 * checked without a browser. Four page loads share one cookie jar:
 *
 *   1. fresh visitor arriving on a campaign URL   -> first_touch
 *   2. second page, no UTMs                        -> last_touch, campaign persisted
 *   3. third page with a pre-load track() queued   -> queue drains, bad param dropped
 *   4. clock advanced past the session window      -> new session, same visitor
 *
 * The shim honours cookie `max-age` against a mutable clock, which is what makes scenario 4
 * a real test of the 30-minute inactivity rule rather than a mocked one.
 *
 *   node validation/checks/snippet_runtime.mjs <endpoint>
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const SOURCE = readFileSync(join(ROOT, 'snippet', 'minuano.js'), 'utf8');
const ENDPOINT = process.argv[2] || 'http://127.0.0.1:8788/collect';

let clock = Date.now();
const jar = new Map();
const inflight = [];

const cookies = {
  read: () =>
    [...jar.entries()]
      .filter(([, entry]) => entry.expires > clock)
      .map(([name, entry]) => `${name}=${entry.value}`)
      .join('; '),
  write: (raw) => {
    const [pair, ...attrs] = raw.split(';').map((s) => s.trim());
    const index = pair.indexOf('=');
    const maxAge = attrs
      .map((a) => a.match(/^max-age=(\d+)$/i))
      .find(Boolean);
    jar.set(pair.slice(0, index), {
      value: pair.slice(index + 1),
      expires: clock + (maxAge ? Number(maxAge[1]) * 1000 : 0),
    });
  },
};

function pageLoad(url, { queue = [], title = 'minuano demo', referrer = '' } = {}) {
  const parsed = new URL(url);
  const window = {
    location: {
      href: parsed.href,
      pathname: parsed.pathname,
      search: parsed.search,
      protocol: parsed.protocol,
    },
    navigator: {
      userAgent: 'Mozilla/5.0 (shim) minuano-validation',
      language: 'pt-BR',
      // Mirrors the real API: hand the payload off and report success synchronously.
      sendBeacon: (target, blob) => {
        inflight.push(
          blob.text().then((body) =>
            fetch(target, { method: 'POST', body, headers: { 'Content-Type': 'text/plain;charset=UTF-8' } })
          )
        );
        return true;
      },
    },
    screen: { width: 1512, height: 982 },
    fetch,
    console,
    minuano: queue.length ? queue : undefined,
  };

  const document = {
    get cookie() { return cookies.read(); },
    set cookie(value) { cookies.write(value); },
    title,
    referrer,
    currentScript: { getAttribute: (name) => (name === 'data-endpoint' ? ENDPOINT : null) },
  };

  // The snippet's clock must be the shim's clock, or advancing time in scenario 4 would
  // expire the cookie while `session_id` (unix seconds at session start) stayed identical.
  class ShimDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return clock; }
  }

  vm.runInNewContext(SOURCE, {
    window, document, URLSearchParams, btoa, Blob, console, Date: ShimDate, Image: undefined,
  });
  return window;
}

// 1 -- fresh visitor on a campaign URL
pageLoad('https://example.com/pricing?utm_source=newsletter&utm_medium=email&utm_campaign=july_launch&gclid=CjwK123');

// 2 -- second page, no campaign parameters on the URL
pageLoad('https://example.com/docs');

// 3 -- a track() call made before the snippet loaded, plus a param that breaks the contract
pageLoad('https://example.com/signup', {
  queue: [['signup_completed', { plan: 'pro', seats: 3, cart: { items: 2 } }]],
});

// 4 -- 31 minutes of inactivity: the session cookie lapses, the visitor cookie does not
clock += 31 * 60 * 1000;
pageLoad('https://example.com/blog');

await Promise.all(inflight);
console.log(`snippet runtime: ${inflight.length} requests sent to ${ENDPOINT}`);
