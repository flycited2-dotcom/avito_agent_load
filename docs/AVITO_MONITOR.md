# Main account monitoring — 2026-09-20

Account 78179175 only. No automatic advertising purchases, customer replies,
balance top-ups, or forced autoload runs. Telegram messages use the existing
private owner chat from the stock-report service's server-side .env. The owner
corrected the destination to bot ID **8650114784** (`@b2b_asist_me_bot`): its token
is read from `B2B_ASSISTANT_TELEGRAM_BOT_TOKEN` in `/var/www/climat-simf.ru/.env`.
The previous bot 8673052162 is not used by Avito monitoring. No secret is copied
into this repository. Do not poll getUpdates (the bots have their own menus).

## Running services

- `avito-manual-stop-sync.timer`: five-minute live status observation. Shared
  publication lock, stable explicit-ID autoload lookup, durable stop list, then
  backup and atomic filtering of the public XML. The former implementation only
  saved a list and did not remove stopped items from the already-public XML.
- `avito-monitor-poll.timer`: checks unread incoming chats, calls without a
  conversation, autoload report problems and service health every five minutes.
  Balance and spending are read hourly. Messages are not marked read.
- `avito-monitor-daily.timer`: 09:00 Europe/Moscow, previous-day product report.
  Stats describe the account, not just XML entries; contacts are not sales.
- A snapshot service drop-in starts the I-T-P gate after a successful fresh
  snapshot. Existing four-hour schedules remain; this does NOT query the
  supplier every five minutes. The gate changes only its nine mapped IDs.
- Full legacy `avito-bridge.timer` stays disabled: do not enable without proving
  the curated public feed can be reproduced and each source is fresh.

## Decisions and limitations

- `removed` and `active -> old` transitions are held, with `archive_hold` for the
  latter. `old` can also mean expiry; its cause is not called a manual removal.
  Restoration requires an explicit owner decision and removal of the stop entry.
- Existing unrelated old ads are not bulk-imported as stops. On September 20,
  the owner specifically confirmed three unavailable microwaves and five
  previously archived appliances; all eight were excluded with backups.
- The API's account-wide report returns overlapping pagination. Stop sync queries
  explicit feed IDs in batches of 50 with `perPage=100`; missing live statuses
  never fall back to a historical upload status. Unpublished IDs may have no
  live item in the list API and are not interpreted as zero stock/removals.
- The public XML exclusion takes effect on Avito's next ingestion. Polling is
  not a webhook and cannot guarantee capturing a manual removal that Avito
  reactivates between polls. No promise of an instantaneous or infallible signal.
- Default notification-only thresholds: balance below 500 RUB; daily spending
  at least 1000 RUB. Item analytics money metrics are kopecks; the spendings
  endpoint and wallet are rubles. Missing metrics are not presented as zero.
- Chat polling reports a new last incoming message in an unread chat. It is not
  a complete message archive; messages already read/replied between polls may
  not notify. Calls with talkDuration=0 are POSSIBLE missed calls, not proof.
- First run seeds historic chats/calls. Durable outbox retries on the next run.
  Delivery is at-least-once: an ambiguous network response/crash can duplicate
  a notification. Telegram failures never expose request URLs/tokens in logs.
- Promotion suggestions are a transparent >=30 views and >=3 contacts/day
  shortlist; availability and margin still need confirmation. No paid writes.
- I-T-P stock is not used to remove goods belonging to BytTechOpt or owner's
  warehouse. The current BytTechOpt XLSX on the server is dated September 1;
  it cannot establish current physical stock. Fresh data/mappings remain needed
  for automatic availability control of other suppliers.

## State and checks

`state/manual-stop-main.json`: durable exclusions. `state/avito-status-main.json`:
observed live statuses. `state/monitor-main/monitor.json`: cursors, conditions,
delivery outbox and last successful check timestamps. `autoload.json`: scoped
report diagnostics. `reports/YYYY-MM-DD.json`: per-item analytics and summary.
Private monitor files use mode 0600. Services share a monitor lock.

Read status using systemctl/journalctl; `scripts/verify_monitor_deployment.py`
prints only counts, timestamps, no secrets. Backups are under
`state/monitor-deploy-*`, `state/microwaves-owner-stop-20260920`, and
`state/archive-owner-stop-20260920`. Restoring a whole old XML can re-enable sold
goods; never do it without reapplying the current stop list and stock rules.

Official API definitions inspected: `https://www.avito.ru/web/1/openapi/info/item`,
`autoload`, `messenger`, `calltracking`, `user` (same base path). Note that
`POST /autoload/v1/upload` bypasses configured publication limits; it is not
called by this monitoring package.
