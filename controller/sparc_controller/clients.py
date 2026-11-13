"""Remember authenticated app installations without replacing the Pi identity."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

MAX_CLIENTS=64

def clean_client(raw):
    if raw is None: return None  # Older paired apps remain compatible.
    if not isinstance(raw,dict) or set(raw)-{'id','name','appVersion','build'}:
        raise ValueError('Send a valid app identity.')
    try: ident=str(uuid.UUID(raw['id']))
    except (ValueError,TypeError,KeyError,AttributeError):
        raise ValueError('Send a valid app identity.') from None
    name=raw.get('name','')
    if not isinstance(name,str): raise ValueError('Choose a short app name.')
    name=name or 'SPARC '+ident[:4].upper()
    if not isinstance(name,str) or not name.strip() or len(name.encode())>128 or any(ord(c)<32 for c in name):
        raise ValueError('Choose a short app name.')
    result={'id':ident,'name':name.strip()}
    for key in ('appVersion','build'):
        value=raw.get(key,'')
        if not isinstance(value,str) or len(value)>32 or any(ord(c)<32 for c in value):
            raise ValueError('Send a valid app version.')
        result[key]=value
    return result

class ClientRegistry:
    def __init__(self,state_dir,save,clock):
        self.path=Path(state_dir)/'paired-apps.json'
        self.save,self.clock=save,clock
        self.clients={}
        if self.path.exists():
            raw=json.loads(self.path.read_text())
            if not isinstance(raw,dict) or raw.get('schemaVersion')!=1 or not isinstance(raw.get('clients'),dict) or len(raw['clients'])>MAX_CLIENTS:
                raise ValueError('The saved app registry needs review; existing entries were not replaced.')
            for key,value in raw['clients'].items():
                client=clean_client({k:value.get(k) for k in ('id','name','appVersion','build')})
                if client['id']!=key: raise ValueError('Invalid saved app identity.')
                self.clients[key]=dict(value,**client)

    def touch(self,client,transport,*,paired=False):
        if client is None: return None
        now=self.clock().astimezone(timezone.utc)
        previous=self.clients.get(client['id'])
        if previous is None and len(self.clients)>=MAX_CLIENTS:
            raise ValueError('This Pi has reached its saved-app limit.')
        entry=dict(previous or {},**client,lastSeenAt=now.isoformat(),transport=transport)
        entry.setdefault('pairedAt',now.isoformat())
        if paired: entry['pairedAt']=previous.get('pairedAt',entry['pairedAt']) if previous else entry['pairedAt']
        changed=previous is None or any(previous.get(k)!=entry.get(k) for k in ('name','appVersion','build','transport'))
        if previous:
            try: changed=changed or (now-datetime.fromisoformat(previous['lastSeenAt'])).total_seconds()>=60
            except (ValueError,TypeError,KeyError): changed=True
        if changed:
            candidate={**self.clients,client['id']:entry}
            self.save(self.path,{'schemaVersion':1,'clients':candidate})
            self.clients=candidate
        return client

    def summary(self,client=None):
        return {'pairedCount':len(self.clients),'currentClientId':client.get('id') if client else None}

    def public(self):
        return {'clients':[deepcopy(self.clients[key]) for key in sorted(self.clients)],'pairedCount':len(self.clients)}

def control_state(settings):
    return dict(settings.get('controlState') or {'sequence':0,'operation':None,'updatedAt':None,'clientId':None,'clientName':None})

def command_settings(settings,candidate,operation,transport,client,now):
    result=dict(candidate)
    previous=control_state(settings)
    result['controlState']={'sequence':previous['sequence']+1,'operation':operation,
                            'updatedAt':now.astimezone(timezone.utc).isoformat(),
                            'clientId':client.get('id') if client else None,
                            'clientName':client.get('name') if client else ('Pi screen' if transport=='display' else 'Paired app')}
    return result
