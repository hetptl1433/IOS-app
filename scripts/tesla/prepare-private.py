#!/usr/bin/python3
"""Run on the Pi only. Never print or return a Tesla client secret/token."""
import argparse
import getpass
import json
import os
from pathlib import Path
import tempfile
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

DOMAIN = 'sparc.ipodeskai.com'
AUDIENCE = 'https://fleet-api.prd.na.vn.cloud.tesla.com'
CLIENT = '687f9e0c-2855-4d0e-b617-780ebd52843a'
SCOPES = 'vehicle_device_data vehicle_charging_cmds'


def save(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url, payload=None, token=None):
    headers = {'Accept':'application/json'}
    if token: headers['Authorization'] = 'Bearer '+token
    data = None
    if payload is not None:
        form = url.startswith('https://fleet-auth.prd.vn.cloud.tesla.com/')
        data = urlencode(payload).encode() if form else json.dumps(payload).encode()
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    req = Request(url, data=data, headers=headers)
    try:
        with build_opener(NoRedirect).open(req, timeout=15) as response:
            raw = response.read(65537)
        if len(raw)>65536: raise ValueError()
        result = json.loads(raw)
        if not isinstance(result,dict): raise ValueError()
        return result
    except HTTPError as error:
        raise SystemExit('Tesla setup request was declined (HTTP '+str(error.code)+'). No secret was printed.') from None
    except (URLError, OSError, ValueError):
        raise SystemExit('Tesla setup could not be verified. No secret was printed.') from None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=Path.home()/'sparc-connections-private')
    parser.add_argument('--secret',action='store_true')
    parser.add_argument('--public-key',action='store_true')
    parser.add_argument('--register',action='store_true')
    args=parser.parse_args()
    folder=args.directory
    folder.mkdir(mode=0o700,parents=True,exist_ok=True)
    os.chmod(folder,0o700)
    key_path=folder/'tesla-command-key.pem'
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    if key_path.exists():
        key=serialization.load_pem_private_key(key_path.read_bytes(),password=None)
        if not isinstance(key,ec.EllipticCurvePrivateKey) or not isinstance(key.curve,ec.SECP256R1):
            raise SystemExit('Existing Tesla command key needs review; it was not replaced.')
    else:
        key=ec.generate_private_key(ec.SECP256R1())
        save(key_path,key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    save(folder/'tesla-command-public.pem',public)
    config_path=folder/'tesla-client.json'
    config=json.loads(config_path.read_text()) if config_path.exists() else {
        'clientId':CLIENT,'redirectURI':'https://'+DOMAIN+'/auth/tesla/callback','audience':AUDIENCE,'domain':DOMAIN}
    if args.secret:
        secret=getpass.getpass('Tesla client secret (private; input is hidden): ')
        if not 16<=len(secret)<=4096 or any(ord(c)<33 or ord(c)>126 for c in secret):
            raise SystemExit('The client secret was not saved. Paste its full value without spaces.')
        config['clientSecret']=secret
        save(config_path,(json.dumps(config,separators=(',',':'))+'\n').encode())
        print('Tesla client configuration saved privately on the Pi.')
    if args.public_key:
        print(public.decode(),end='')
    if args.register:
        if not config.get('clientSecret'): raise SystemExit('Save the Tesla client secret privately first.')
        # Verify the public artifact is actually hosted before asking Tesla to register it.
        from urllib.request import urlopen
        try:
            with urlopen('https://'+DOMAIN+'/.well-known/appspecific/com.tesla.3p.public-key.pem',timeout=10) as response:
                hosted=response.read(4096)
        except OSError:
            raise SystemExit('The public key is not reachable on the registered domain yet.') from None
        if hosted.strip()!=public.strip(): raise SystemExit('The hosted public key differs; registration was not attempted.')
        result=request('https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token',{
            'grant_type':'client_credentials','client_id':config['clientId'],'client_secret':config['clientSecret'],
            'scope':SCOPES,'audience':AUDIENCE})
        token=result.get('access_token')
        if not isinstance(token,str) or not token: raise SystemExit('Tesla did not issue a partner token.')
        request(AUDIENCE+'/api/1/partner_accounts',{'domain':DOMAIN},token)
        print('Tesla partner registration completed for the existing SPARC domain. No token was retained or printed.')


if __name__=='__main__': main()
