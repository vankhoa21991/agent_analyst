# Speed measurement (Task 0, step 5)

Date: 2026-10-21. Script: `scripts/measure_speed.py` (5 fixed prompts, one of
them a tool-calling prompt).

## Model and server settings

- Model file: `/home/lupin/models/Qwen3.8-27B-UD-Q4_K_XL.gguf` (17.5 GB)
- Quantisation: Q4_K_XL (Unsloth dynamic)
- Server: llama.cpp `llama-server`, version 0.7.0-dev (build 11719, commit 5aeac37a3)
- Flags: `-ngl 99 -fa on --parallel 1 -c 131072 --cache-type-k q8_0 --cache-type-v q8_0 --spec-type draft-mtp --spec-draft-n-max 2 --temp 0.7 --top-p 0.95 --top-k 20 --repeat-penalty 1.0 --jinja`
- The model is a thinking model: replies include `reasoning_content` tokens,
  which are counted in completion tokens below.

## Output

```
short            4.32s     37 tokens     8.57 tok/s
math             1.13s     39 tokens    34.60 tok/s
medium           3.21s    172 tokens    53.61 tok/s
tool_call        2.66s    127 tokens    47.76 tok/s
business         1.34s     56 tokens    41.78 tok/s
mean: 2.53s per reply, 37.26 tokens/s
```

## Planning numbers for Task 6 (eval sample sizes)

- Assume ~4 model calls per task (explore + query + answer) at ~2.5 s each,
  plus tool execution: budget ~15–30 s per task.
- 50 questions x 3 repeats = 150 tasks per config ≈ 1–1.5 h of GPU time.
- All five configs (B0, H1–H4) ≈ 5–8 h unattended. Feasible; no need to cut
  the custom suite below 50 questions on time grounds.
