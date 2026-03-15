# Model Comparison Summary - March 15, 2026

**Date**: 2026-03-15

This is a summary of data from multiple benchmarks:

- [Frontier 5 Models (2026-03-14)](./history/frontier_5_models_20260314_204516.md)
- [Results 2026-03-15](./history/results_20260315_041151.md)
- [Results 2026-03-11](./history/results_20260311_210836.md)

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

Codex and qwen-next-80B-thinking tended to ask for additional context, which is why they didn't perform well in the benchmarks.

Opus occasionally ignored instructions when it thought it knew better (for example pulling unrelated runbooks despite explicit instructions not to).

Sonnet sometimes followed instructions too literally. For example, when told to "look at all logs", it would respond with "I looked at all the logs" rather than explaining what it found.
