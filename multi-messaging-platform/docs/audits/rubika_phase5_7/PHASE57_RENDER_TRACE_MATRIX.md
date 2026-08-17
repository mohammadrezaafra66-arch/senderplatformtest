# PHASE57_RENDER_TRACE_MATRIX

| mode | prose source | GPT batch | variation | product source | product snapshot | final_text stored | hash stored | queue text | transport text | retry text | log text | tested |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GPT OFF / Products OFF | base template after placeholders | n/a | n/a | n/a | n/a | yes | yes | persisted `final_text` | same | same | persisted | yes |
| GPT ON / Products OFF | assigned variation after placeholders | frozen `generation_batch_id` | frozen `variation_id` | n/a | n/a | yes | yes | same | same | same | same | yes |
| GPT OFF / Products ON | base prose | n/a | n/a | AfraKala fetch at prepare | frozen per message | prose + locked block | yes | same | same | same | frozen names/prices | yes |
| GPT ON / Products ON | GPT prose | frozen | frozen | prepare fetch | frozen; GPT never owns block | prose + locked block | yes | same | same | same | both traces | yes |
