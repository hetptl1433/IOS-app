# Local deployment

## Laptop

Run `python3 -m sparc_lab` from the repository. Optional arguments:

```text
--host 127.0.0.1
--port 8765
--data-dir runtime
```

Use a separate virtual environment for optional model dependencies. Keep downloaded weights and local dataset imports out of Git. The `.gitignore` protects `runtime/`, `models/`, `data/` (except the schema example), `.env`, and environments.

## Raspberry Pi service

After cloning into `/home/pi/sparc-lab`, adjust the user/path to your actual account and install this unit yourself:

```ini
[Unit]
Description=SPARC model and charging simulation lab
After=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/sparc-lab
ExecStart=/usr/bin/python3 -m sparc_lab --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5
Environment=SPARC_DATA_DIR=/home/pi/sparc-lab/runtime

[Install]
WantedBy=multi-user.target
```

Use SSH port forwarding to access a remote Pi's loopback service. Optional model inference should be benchmarked on the real hardware. Core simulation and Ridge training use only Python's standard library.

## Future Tesla integration

This app deliberately has no authentication tokens, virtual keys, or command-sending endpoints. A future integration should consume the tested scheduler through a separate adapter, request the minimum Fleet API permissions, verify commands from fresh vehicle state, preserve manual overrides, and set the native vehicle charge limit first. Internet-only commands cannot guarantee an immediate stop during a connectivity outage.

Keep simulated oracle forecasts and retrospective data inaccessible to a live command path. Enable any live control only after prospective observation-mode evaluation.
