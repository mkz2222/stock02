# Stockwatch dashboard on Netlify

A public, read-only, mobile-friendly activity dashboard. No login and no rule editor.

## Deploy

1. Push this repository to GitHub and import it into Netlify.
2. Use the repository root as the base directory. Leave the build command empty.
   `netlify.toml` sets `dashboard` as the publish directory and
   `netlify/functions` as the functions directory. No frontend build or package
   installation is required. Use Node.js 22 or newer for local tools.
3. Add these variables in Netlify's project environment-variable settings,
   with Functions scope (or All scopes if scope selection is unavailable):

   | Variable | Value |
   | --- | --- |
   | `SUPABASE_URL` | `https://rcmpuisjnyhbeqccokhh.supabase.co` |
   | `SUPABASE_SECRET_KEY` | A server-side `sb_secret_...` key, preferably a separate key for this dashboard |
   | `STOCKWATCH_DEVICE_ID` | `raspberrypi` |

   Set production values for a production deployment. A deploy preview needs its
   own configured context values to display live data. Never use a `VITE_` or
   `NEXT_PUBLIC_` prefix for the secret, put it in this repository, or import the
   Pi's entire environment file. Keep the Pi's existing credentials unchanged.
4. Deploy the repository, including its functions. A static folder drag-and-drop
   alone does not deploy the required API function.
5. Open `/api/activity` on the deployed domain to confirm it returns runs and
   alerts, then open the homepage. Redeploy after changing environment variables.

## Public data and credentials

The browser calls `/api/activity`; only the Netlify Function receives the secret
key. Existing Supabase grants and RLS remain unchanged. This function intentionally
has no authentication: anyone can read the latest 30 runs and 30 alerts for the
configured device. Alert message text is public, including any notes and targets
already included in those messages. There is no query against `stockwatch_rules`.

The endpoint accepts GET only, fixes the device and row limits on the server,
projects selected run fields, and replaces raw error details with a generic
message. It does not provide writes or arbitrary database queries. Supabase
secret keys retain elevated database privileges; the endpoint's read-only code
is not a read-only restriction on the key itself.

Responses can be cached for up to 60 seconds at the CDN. The page refreshes every
minute while visible, so it is an activity view rather than a live price feed.
The summary counts cover the returned records, not all-time totals. A check-in
older than 90 minutes is flagged. Failed refreshes retain the previous view.

## Local preview and tests

With Node.js 22 or newer:

```sh
node --test tests/activity.test.mjs
node scripts/dashboard-preview.mjs --demo
```

Open http://localhost:8888. Demo mode is explicitly labeled and uses synthetic
sample data only; the demo server and its fixtures are outside the deployed
publish directory. For live local data, set the three environment variables in
your local shell and omit `--demo`. Netlify's own `netlify dev` can also serve the
site and function together if the CLI is installed.

Production never falls back to sample data: missing configuration and connection
failures appear as visible errors. This repository is prepared for Netlify but
has not been deployed by this change.
