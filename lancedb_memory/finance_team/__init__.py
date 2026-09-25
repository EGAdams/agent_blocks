"""A 6-agent team for vendor payment-pattern analysis over rol_finances.

5 agents (map-reduce) answer: "which vendors are paid monthly, and did one get
missed or spike this month?" 4 map agents each own one disjoint slice of the
`expenses` table (partitioned by the existing `method` enum: CASH/CARD/BANK/
OTHER), so they can run fully in parallel with no shared state. The 5th
(synthesizer) reduces their four JSON outputs into one flagged report.

A 6th, independent agent scours the receipt image directories for files that
don't match any expenses row or don't match the vendor/date/amount filename
convention -- orphaned or mislabeled receipts.

See ROLES in roles.py for each agent's exact brief, and orchestrate.py for
the composition root that runs them.
"""
