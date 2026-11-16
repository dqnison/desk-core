---
tier: action
when:
  - paths: ['billing/']
guard:
  - ask: 'billing/'
    in: paths
    id: paid-billing
    why: billing/ calls a paid API — read the rule below first; test the case «the call does NOT happen»
---

# shop-api: paid code

`billing/` calls a paid external API: a mistake costs money.

- A test for «the call does NOT happen» is required.
- The effect on the number of paid calls — one line in the MR description.
