# Recommendations

**Date**: 2026-03-15  
**Scope**: Consolidated benchmark results for model success rate, runtime, and cost.

This page summarizes data from the following benchmark runs:

- [Frontier 5 Models (2026-03-14)](./history/frontier_5_models_20260314_204516.md)
- [Results 2026-03-15](./history/results_20260315_041151.md)
- [Results 2026-03-11](./history/results_20260311_210836.md)

## At a Glance

- **Top performers:** opus-4.6 and sonnet-4.6
- **Most expensive:** opus-4.6
- **Cheapest:** deepseek-r1-reasoner and deepseek-v3.2-chat
- **Fastest:** gpt-5.3-codex
- **Slowest:** deepseek-r1-reasoner

## Rankings

| Benchmark Place | Model                  | Price Tier *(cheapest / most expensive)* | Speed Tier *(fastest / slowest)* |
| --------------- | ---------------------- | ---------------------------------------- | -------------------------------- |
| 1st             | opus-4.6               | **Most Expensive**                       | Average                          |
| 1st             | sonnet-4.6             | Expensive                                | Average                          |
| 2nd             | deepseek-r1-reasoner   | **Cheapest**                             | **Slowest**                      |
| 2nd             | gemini-3.1-pro-preview | Average                                  | Average                          |
| 3rd             | deepseek-v3.2-chat     | **Cheapest**                             | Slow                             |
| 3rd             | gpt-5.4                | Average                                  | Average                          |
| 3rd             | haiku-4.5              | Cheap                                    | Fast                             |
| 4th             | qwen-next-80B-instruct | Cheap                                    | Fast                             |
| 5th             | qwen-next-80B-thinking | Cheap                                    | Average                          |
| 6th             | gpt-5.3-codex          | Cheap                                    | **Fastest**                      |

## Notes

### Context Requirements

gpt-5.3-codex and qwen-next-80B-thinking tended to ask for additional context instead of proceeding with the investigation. This reduced their benchmark performance.

### Instruction Handling

opus-4.6 occasionally ignored explicit instructions when it believed another approach would be better. For example, it sometimes pulled unrelated runbooks despite being told not to.

### Literal Interpretation

sonnet-4.6 sometimes followed instructions too literally. For example, when told to "look at all logs", it would say that it had looked at the logs without explaining what it found.

## Benchmark Methodology

| Category        | Rule                                   | Explanation |
| --------------- | -------------------------------------- | ----------- |
| Benchmark Place | Based on **success rate ranking**      | Models are ranked by number of successful tests. Equal scores share the same placement. For example, two models with 16/16 both receive **1st place**. |
| Price Tier      | **Cheap:** ≤ $0.06                     | Models with an average cost per run of $0.06 or less. |
|                 | **Average:** $0.07 – $0.15             | Models with moderate cost per run. |
|                 | **Expensive:** ≥ $0.16                 | Models with higher cost per run. |
| Speed Tier      | **Fast:** < 30 seconds average runtime | Models that completed benchmark tasks quickly. |
|                 | **Average:** 30–60 seconds             | Models with mid-range runtime. |
|                 | **Slow:** > 60 seconds                 | Models with slower average runtime. |

## Special Labels Used

| Label              | Meaning |
| ------------------ | ------- |
| **Cheapest**       | Model(s) with the lowest average cost across the benchmark. |
| **Most Expensive** | Model with the highest average cost across the benchmark. |
| **Fastest**        | Model with the lowest average runtime. |
| **Slowest**        | Model with the highest average runtime. |