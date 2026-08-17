# PHASE56_GPT_PIPELINE_MATRIX

| | GPT provider called? | Product provider called? | Prose source | Product source | Recipient substitution | Persistence | Retry | Final composition | Test evidence |
|---|---|---|---|---|---|---|---|---|---|
| GPT OFF / Product OFF | No | No | base template | none | yes | RenderedMessage.final_text | unchanged | template only | test_mode_a |
| GPT ON / Product OFF | Yes (once per pool) | No | validated variation | none | after GPT | pool + variation_id in metadata | no GPT | variation after substitute | test_mode_b, test_hundred_recipients |
| GPT OFF / Product ON | No | Yes | base template | Phase 5.5 freeze | yes | product snapshot metadata | no product refetch | template + locked block | test_mode_c + Phase 5.5 |
| GPT ON / Product ON | Yes (prose only) | Yes (separate) | validated variation | Phase 5.5 freeze | after GPT, before products | gpt + product metadata | neither provider | GPT prose + locked block last | test_mode_d, test_retry |
