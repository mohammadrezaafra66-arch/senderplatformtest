# Scale and concurrency tests

Files:

- `tests/core_engine/test_rubika_phase6_campaign_capacity.py`
- `tests/queue_bridge/test_phase6_controlled_dispatch.py`

Coverage: 10/100/1k/10k planner; mixed accounts; bottleneck; bot_api N/A;
manual vs auto assignments; circuit start block; Redis fail-closed; quarantine
no failover; aggregation GROUP BY (not per-recipient); 10k hash stability;
fair two-campaign dispatch; pause/resume no duplicate ids; circuit OPEN no
mass SKIPPED; concurrent SKIP LOCKED + lease; in-flight TTL release; dispatch
does not call GPT/product.

Transport is mocked. No external Rubika network.
