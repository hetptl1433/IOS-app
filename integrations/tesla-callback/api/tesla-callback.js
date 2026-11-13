// This bridge never exchanges or stores tokens. The paired Pi validates the
// one-time state and exchanges the code using its privately stored client key.
export default function handler(req, res) {
  res.setHeader('Cache-Control', 'no-store, max-age=0');
  res.setHeader('Pragma', 'no-cache');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'");
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).send('Open this link from SPARC.');
  }
  const input = new URL(req.url, 'https://sparc.ipodeskai.com');
  if (!input.search) return res.status(200).send('SPARC Tesla connection. Begin Connect Tesla in the SPARC app, with your paired Pi nearby.');
  const single = (key) => input.searchParams.getAll(key).length === 1 ? input.searchParams.get(key) : null;
  const state = single('state');
  const code = single('code');
  const denied = single('error');
  if (!state || !/^[a-f0-9]{64}$/.test(state)) return res.status(400).send('This connection link is invalid. Begin Connect Tesla again in SPARC.');
  const target = new URL('sparc://tesla/callback');
  target.searchParams.set('state', state);
  if (denied) {
    target.searchParams.set('error', 'authorization_cancelled');
  } else if (code && code.length <= 4096 && !/[\x00-\x20\x7f]/.test(code)) {
    target.searchParams.set('code', code);
  } else {
    return res.status(400).send('Tesla did not return a connection code. Begin Connect Tesla again in SPARC.');
  }
  res.setHeader('Location', target.toString());
  return res.status(303).send('Return to SPARC to finish connecting your Tesla.');
}
