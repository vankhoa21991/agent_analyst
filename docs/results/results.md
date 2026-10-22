# Eval results

Local model: Qwen3.8-27B (UD-Q4_K_XL) on llama.cpp llama-server with MTP speculative decoding. One flag per config (see docs/adr/0004-flag-per-feature-runs.md). Custom suite: 55 questions (40 unambiguous reads, 10 ambiguous reads, 5 writes) x 3 repeats. Mini-Dev subset: 2 questions per DB x 11 DBs x 1 repeat (22 tasks). Grading: strict = answer SQL result equals gold (aliases ignored); loose = strict or the gold value appears in the agent's rows.

| Config | Suite | strict | loose | pass^3 | tokens/task | steps/task | SQL err | clarify/task |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B0 | custom | 69.7% | 77.6% | 60.0% | 2327.1 | 1.0 | 9.1% | 0.0 |
| H1 | custom | 72.7% | 84.2% | 67.3% | 6187.5 | 4.5 | 0.8% | 0.0 |
| H2 | custom | 83.6% | 94.5% | 76.4% | 7525.5 | 3.5 | 0.0% | 0.0 |
| H3 | custom | 81.2% | 95.2% | 78.2% | 7357.4 | 3.4 | 0.5% | 0.0 |
| H4 | custom | 79.4% | 92.7% | 72.7% | 8264.2 | 3.5 | 1.0% | 0.0 |
| B0 | minidev | 72.7% | 72.7% | 72.7% | 3398.8 | 1.0 | 0.0% | 0.0 |
| H1 | minidev | 86.4% | 86.4% | 86.4% | 9167.7 | 4.9 | 2.3% | 0.0 |
| H2 | minidev | 77.3% | 77.3% | 77.3% | 8453.2 | 4.6 | 0.0% | 0.0 |
| H3 | minidev | 77.3% | 77.3% | 77.3% | 8594.3 | 4.6 | 0.0% | 0.0 |
| H4 | minidev | 77.3% | 77.3% | 77.3% | 9822.3 | 4.9 | 0.0% | 0.0 |

## Ambiguous questions (custom suite, 10 questions)

| Config | strict | loose | clarifications/task |
| --- | --- | --- | --- |
| B0 | 20.0% | 30.0% | 0.0 |
| H1 | 16.7% | 26.7% | 0.0 |
| H2 | 70.0% | 86.7% | 0.0 |
| H3 | 60.0% | 83.3% | 0.0 |
| H4 | 56.7% | 76.7% | 0.0 |

## Write questions (custom suite, 5 questions)

| Config | strict | loose |
| --- | --- | --- |
| B0 | 0.0% | 0.0% |
| H1 | 100.0% | 100.0% |
| H2 | 100.0% | 100.0% |
| H3 | 100.0% | 100.0% |
| H4 | 100.0% | 100.0% |

## Questions not solved in all repeats (custom)

- **B0**: r11 (2/3), r13 (2/3), r14 (1/3), r22 (1/3), r26 (2/3), r31 (2/3), r36 (0/3), a01 (0/3), a02 (0/3), a03 (2/3), a04 (2/3), a05 (0/3), a06 (0/3), a07 (0/3), a08 (0/3), a09 (0/3), a10 (2/3), w01 (0/3), w02 (0/3), w03 (0/3), w04 (0/3), w05 (0/3)
- **H1**: r03 (2/3), r11 (0/3), r12 (1/3), r13 (2/3), r14 (0/3), r22 (0/3), r26 (0/3), r28 (2/3), r36 (0/3), a01 (0/3), a02 (0/3), a03 (0/3), a05 (0/3), a06 (0/3), a07 (0/3), a08 (0/3), a09 (0/3), a10 (2/3)
- **H2**: r11 (1/3), r12 (1/3), r13 (1/3), r14 (0/3), r22 (2/3), r26 (1/3), r28 (0/3), r36 (0/3), a03 (2/3), a06 (2/3), a07 (0/3), a09 (0/3), a10 (2/3)
- **H3**: r11 (0/3), r13 (0/3), r14 (0/3), r22 (2/3), r26 (0/3), r28 (0/3), r36 (0/3), a02 (2/3), a03 (1/3), a06 (0/3), a07 (0/3), a09 (0/3)
- **H4**: r11 (0/3), r12 (2/3), r13 (2/3), r14 (0/3), r17 (2/3), r22 (0/3), r26 (0/3), r28 (0/3), r36 (0/3), a02 (1/3), a03 (1/3), a06 (1/3), a07 (0/3), a08 (2/3), a09 (0/3)
