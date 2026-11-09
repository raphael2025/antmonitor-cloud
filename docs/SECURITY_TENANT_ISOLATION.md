# Customer-level tenant isolation design

Status: proposed architecture; not yet implemented.

## Why a separate design is required

The current `viewer` role is read-only but global. It is an internal staff role, not a customer permission boundary. Hiding rows in the browser would not be sufficient: every API and every database read must enforce tenant scope.

A site can contain multiple customers. Therefore, `site_id` alone is not a safe tenant key. The smallest current data identity that can be assigned without merging unrelated customers is the pair:

```text
(site_id, worker)
```

Worker names are not globally unique; the same worker string at two sites must remain two distinct bindings.

## Data model

Add three tables:

- `tenants(id, name, status, created_ts, updated_ts)`
- `tenant_members(tenant_id, username, status, created_ts)`
- `tenant_worker_bindings(tenant_id, site_id, worker, created_ts, updated_ts)`

Constraints:

- `tenant_members`: `username` is globally unique; one customer login belongs to exactly one tenant unless a later requirement explicitly permits multi-tenant accounts.
- `tenant_worker_bindings`: unique `(site_id, worker)` until an administrator explicitly unbinds it, preventing one worker from being visible to two customers by accident.
- Foreign keys cascade only when an administrator deliberately removes a tenant or binding.
- Store exact normalized worker keys; display labels must not be used as authorization keys.

Customer login accounts use a distinct `customer` role and a tenant membership. Existing `admin` and global `viewer` accounts remain staff roles; they must never be handed to customers as a substitute for tenant accounts.

## API authorization contract

| API surface | Admin / staff viewer | Customer |
|---|---|---|
| `/api/overview` | Existing global view | Only the customer's assigned worker rows and customer-only totals |
| `/api/customers` | Existing global view | Only assigned `(site_id, worker)` rows |
| `/api/site/{id}` | Existing site detail | Customer-only worker rows; no site-wide totals or other workers |
| `/api/trend`, `/api/daily` | Existing site-wide history | Deny until per-customer history exists |
| `/api/public/*` | Bearer-token integration, unchanged | Not a customer session API |
| site management, token reveal/rotation, settings, update, notifications | Admin-only | Deny |
| all unknown/future data APIs | Existing role policy | Deny by default |

Every customer-scoped query must filter in the server/database layer. The server must not fetch a global result and rely on frontend filtering.

For site alert summaries, return only groups whose worker membership intersects the customer's bindings, recompute counts from the filtered worker list, and suppress site-level alerts that cannot be safely attributed to that tenant.

## Historical data limitation

Current cloud storage keeps current per-worker customer rows, but the durable daily/trend history is site-level. It cannot safely be presented as a customer's own history when multiple customers share a site.

A second phase must persist per-worker observations and derive customer-level daily rollups before customer accounts can access historical charts, energy, uptime, or delivered-TH data. Until then, those endpoints return 403 for customer sessions rather than leaking site-wide figures.

## Admin workflow

1. Create a tenant and a customer login.
2. Assign exact worker identities by selecting a site and its currently observed worker rows.
3. Preview the resulting worker list and check for unassigned/duplicate bindings.
4. Confirm the assignment; write an audit event containing tenant ID and worker keys, but never passwords or tokens.
5. Revoke membership or bindings immediately when a customer relationship ends.

No automatic mapping by worker prefix, substring, site display name, or fuzzy matching is allowed. Existing rows remain unassigned until an administrator confirms ownership.

## Migration and rollout

1. Add schema and migration tests only; no existing user is silently converted to a customer.
2. Add authorization helpers and tenant-scoped database functions.
3. Add API tests with two tenants sharing one site, plus the same worker string on two different sites.
4. Add a customer-only dashboard that consumes only scoped APIs.
5. Enable customer accounts only after every route in the authorization contract has a test.
6. Keep the global viewer role staff-only and document that it is not safe to share with customers.

## Required security tests

- Tenant A cannot read Tenant B's workers by changing a site ID, worker string, query parameter, or URL path.
- Two tenants sharing a site see disjoint worker rows and customer-only totals.
- Identical worker strings at different sites do not cross tenant boundaries.
- Customer sessions cannot access site-wide trend/daily, alerts not attributable to them, site administration, token reveal, settings, or update APIs.
- Missing tenant membership or missing binding is deny-by-default.
- Revoking a binding takes effect on the next request without requiring a process restart.
- Staff/admin access and existing ingestion/reporting behavior remain unchanged.

## Site identity clone detection

The local reporter currently persists a stable `site_id` in `cloud_site_id.txt`; cloning the whole installation directory also clones that identity. Documentation already tells operators to remove the file when cloning, but that is a manual safeguard.

A safe detection phase should:

1. Send a one-way SHA-256 host fingerprint with ingest reports. Prefer a platform hardware UUID (SMBIOS/DMI UUID on Linux/Windows); use a documented fallback only when unavailable. Never transmit raw hardware identifiers.
2. Store observations by `(site_id, fingerprint)` with first/last seen timestamps and source IP metadata.
3. If a second fingerprint reports the same `site_id` within a configurable overlap window, flag an identity conflict in the admin view and audit log.
4. Keep ingest visible but mark the site's identity as conflicted; do not silently merge, rotate, or discard historical data.
5. Require an explicit admin rebind action after checking the old host is retired. The action must be audited and should expire the old reporter credential if the credential was cloned.
6. Treat a normal hardware migration as an explicit rebind, not as an automatic conflict reset.

The overlap window and hardware fingerprint strategy must be tested against Windows reinstalls, Linux image clones, VM clones, NIC changes, and planned hardware replacement. A source-IP-only detector is insufficient because several sites can share NAT and one site can change egress IP.

## Decision gate

Do not enable customer accounts until the API and dashboard phases are complete. A partially implemented role would create a false sense of isolation.
