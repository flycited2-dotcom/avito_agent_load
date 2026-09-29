# I-T-P Crimea → Telegram → Avito

## Live data flow

1. `climat-simf-itp-crimea-snapshot.timer` refreshes the exact Crimea warehouse
   snapshot every 15 minutes from `nearest_logistic_center_real_qty`.
2. The atomic JSON is written to
   `/opt/avito-bridge/runtime/itp-crimea/latest.json`. Both Telegram and Avito
   consume this same file and timestamp.
3. `climat-simf-itp-crimea-report.timer` sends the owner a personal Telegram
   list plus an Excel-friendly CSV every day at 08:00 Europe/Moscow.
4. `avito-itp-crimea-cards.timer` submits supplier photos to the `kbt` content
   factory and collects approved generated cards. `profiles/itp-crimea.yaml`
   has `catalog.selected_series: ["__none__"]`, so card work cannot publish ads.
5. `avito-itp-stock-gate.timer` is installed but intentionally disabled. When
   explicitly enabled, it removes/restores only the nine mapped historical ads
   according to the fresh snapshot. It never rebuilds the rest of the feed.

## Safety properties

- A snapshot older than 30 minutes, from too far in the future, malformed, or
  missing stops the cycle. The last valid public feed remains in place.
- Only IDs listed in `profiles/itp-crimea-stock-map.yaml` can be changed by the
  live stock gate.
- Before every applied stock-gate change, the complete public feed is backed up
  under `/opt/avito-bridge/state/itp-stock-backups/`.
- Managed ads are stored verbatim in
  `/opt/avito-bridge/state/itp-crimea-managed-ads.xml`; a restock restores the
  exact title, description, price, tags and images.
- Existing Avito IDs are anchored in `profiles/itp-crimea.yaml`, preventing
  duplicate ads when generated I-T-P cards later replace the historical XML.
- Ordinary Avito goods ads do not expose a numeric stock counter. The safe
  synchronization is therefore availability: present while Crimea stock is
  positive, absent when it reaches zero.

## Current scope (2026-09-17)

- Snapshot: 15 positions, 22 units, 15/15 with supplier images, 14/15 with
  structured characteristics for content briefs.
- Existing live ads mapped to I-T-P: 9 (Beko 2, Indesit 1, DON 2, LG 3,
  Stinol 1).
- New candidates: Atlant XM-4319-101, GalaPrint cartridge, MSI PRO MP272L,
  Hyundai CH25081, Kyocera MA5500ifx and Atlant XM-4025-000.
- The monitor/printer/cartridge Avito taxonomy must be checked against the
  current Autoload tree before those three candidates can be published.
- Price policy for new candidates must be confirmed separately. The current
  candidate reads the website retail price, but no public price changes have
  been applied.

## Activation and rollback

After owner approval, initialize durable templates with one manual service run
and enable `avito-itp-stock-gate.timer`. To stop changes, disable the timer.
Rollback is an atomic restore of the latest `avito-feed-before-*.xml` backup
under the same `profile-publish.lock` used by other Avito publishing paths.
