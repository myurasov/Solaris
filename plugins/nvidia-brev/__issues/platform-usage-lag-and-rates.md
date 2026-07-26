# platform: usage feed lags hours; billed rates don't match advertised $/h at face value

**Target:** Brev platform/console feedback

## Observed (2026-07-25)

From `GET /api/organizations/<org>/usage` (the console's own endpoint):

- **Lag:** an instance running ~9.5h showed only ~2.3 component-hours billed at query time;
  final figures appear well after teardown. Automation verifying spend right after delete gets
  partial numbers with no "as-of" timestamp in the response.
- **Rate decomposition:** advertised $/h appears split into components at fractional rates -
  verda B300 advertised $9.49/h shows as line items at $4.75/h with doubled seconds (sum matches);
  gcp T4 advertised $0.48/h billed at $0.40/h (cheaper than advertised, unexplained). There is no
  documented mapping from catalog price to usage line items.

## Impact

Cost-tracking automation cannot reconcile catalog price x wall-clock with billed line items, and
cannot tell "final" from "still-accruing" numbers.

## Suggested fix

Add an `as_of` / completeness marker to the usage response, and document the component split
(compute vs host vs storage) against the advertised single $/h.
