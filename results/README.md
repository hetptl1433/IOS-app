# SPARC results pages

The headline results, written to be easy to read. Open `index.html` in a browser (double-click it, or
`open results/index.html` on a Mac), or use the menu in SPARC itself (`./start.command`).

| Page | What it shows |
|---|---|
| `index.html` | What SPARC saves for an average, a moderate and a heavy driver, what a year of charging costs each way, and the five things the studies found |
| `accuracy.html` | How far off each price forecast was (RMS error), from the day-ahead price to six AI models |
| `how-it-works.html` | How a kWh is priced, how a night's cost and a year's saving are worked out, one real night added up hour by hour, and how the simulation works, step by step |
| `details.html` | Every study with its error bars, for reviewers |
| Test models | In SPARC only (`./start.command`, then the home page): test AI forecasters yourself with one click |

How the files fit together:
- `data.js` holds every number on the pages, each block labelled with the results file in `experiments/` it
  comes from. Nothing is computed on the pages beyond simple sums and ratios.
- `example.js` holds the night on the How it works page. `experiments/scenarios/worked_example.py` writes it
  with the same simulation as every result.
- `pages.js` draws the simple pages, and `style.css` styles them. `details.html` is self-contained and draws
  its charts with d3, so it needs an internet connection.
- `model-tests/` collects the tests run from SPARC's Test models page, as Markdown to read and JSON for the
  numbers. Commit them to share them.
