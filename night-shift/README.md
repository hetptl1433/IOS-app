# SPARC Night Shift

[Open the hosted simulator](https://sparc-nightshift-lab-het.hetptl1433.chatgpt.site).

The complete browser-based EV charging simulator from SPARC's ChatGPT Site, including the historical ComEd price dataset. Race charging strategies, take manual control, and compare results across historical nights. The simulator runs locally in your browser and does not control a real charger.

## Run locally

From the root of this Git repository:

```sh
python3 -m http.server 8000 --bind 127.0.0.1 --directory night-shift/dist
```

Open **http://127.0.0.1:8000** in your browser. Stop the server with **Ctrl+C**. Python 3 is the only tool needed to serve it; there is no build step or package installation. The replay data is included, so running the simulator does not require ComEd or Tesla API credentials.

## Edit the website

| File | Purpose |
| --- | --- |
| `dist/index.html` | Homepage served at `/`. |
| `dist/simulation.html` | The same page at `/simulation.html`, used by internal home links. |
| `dist/simulation.css` | Layout, vehicle artwork styling, and animation. |
| `dist/simulation.js` | Controls, replay UI, manual challenge, and historical comparisons. |
| `dist/simulation-engine.js` | Charging strategies, planning, and billing calculations. |
| `dist/simulation-charge-target.js` | Battery target and energy calculations. |
| `dist/simulation-view.js` | Replay progress and battery presentation helpers. |
| `dist/simulation-data.js` | Bundled historical ComEd data and study metadata. |
| `dist/simulation-strategies.md` | Strategy comparison notes and study assumptions. |
| `dist/LICENSE` | Original MIT license. |
| `.openai/hosting.json` | Original ChatGPT Sites project and static directory configuration. |

The two HTML entrypoints currently match. After editing `simulation.html`, update the homepage too:

```sh
cp night-shift/dist/simulation.html night-shift/dist/index.html
```

## Source and publishing

Exported on October 29, 2026 from **SPARC Night Shift**, Sites project `appgprj_6ad8c3874df08191aeff361f759f5b95`, source commit `eaf258ba5296ee195a4c2f4388e504075d61dd7e`.

The 11 original tracked site files are preserved byte for byte. This GitHub folder is a source snapshot: GitHub commits do not automatically publish to the hosted ChatGPT Site, and later changes made through Sites need to be copied back here to keep the two in sync. For another static host, publish the contents of `night-shift/dist`.
