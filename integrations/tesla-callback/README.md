# SPARC Tesla authorization callback

This small Vercel service owns the HTTPS callback registered with Tesla at `https://sparc.ipodeskai.com/auth/tesla/callback`. It validates the callback shape and redirects to `sparc://tesla/callback`. The paired iOS app sends the one-time code and state over the existing encrypted controller connection. The Pi validates the pending state and exchanges the code directly with Tesla.

The service contains no client secret, private key, refresh token, or token database. The key under `public/.well-known/appspecific/` is intentionally public; its private counterpart stays on the owner's Pi. Never replace this public key without coordinating the Pi's corresponding private key and Tesla registration.

Run `node test-callback.mjs` for the callback validation checks. Deploy this directory with the Vercel CLI to the existing `sparc-tesla-callback` project; the registered SPARC domain follows production deployments. Keep Vercel project-local metadata and any environment files out of Git.

The Pi's private setup helper is maintained at `scripts/tesla/prepare-private.py` in the SPARC repository. Run it on the Pi and enter credentials directly at its hidden prompt. Never pass a client secret as a command argument or put one in the iOS app, web source, repository, chat, screenshots, or diagnostic logs. Owner authorization and vehicle-key pairing are separate from developer registration.
