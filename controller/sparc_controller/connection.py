"""Persisted setup choices, separate from verified hardware activation/secrets."""
from copy import deepcopy
from datetime import datetime, timezone
import math

from .shelly import DEVICE_NAME, MODEL, local_address
from .virtual_dev import updated as calibrate_virtual

MODES = ('virtual', 'tesla', 'smart_plug')
VIRTUAL_MODELS = ('cybertruck', 'model_x', 'model_3')
DEFAULT = {'schemaVersion':1, 'revision':0, 'mode':'virtual', 'virtual':{'model':'cybertruck'},
           'smartPlug':{'address':None, 'vehicleSource':'virtual', 'followVirtual':False}, 'updatedAt':None}


def normalize(value=None):
    if value is None:
        return deepcopy(DEFAULT)
    if not isinstance(value,dict) or set(value)-set(DEFAULT):
        raise ValueError('Invalid saved connection settings.')
    result = deepcopy(DEFAULT)
    result.update(deepcopy(value))
    if isinstance(result.get('smartPlug'),dict) and 'followVirtual' not in result['smartPlug']:
        result['smartPlug']['followVirtual'] = False
    if (type(result['schemaVersion']) is not int or result['schemaVersion'] != 1
            or type(result['revision']) is not int or result['revision'] < 0 or result['mode'] not in MODES):
        raise ValueError('Invalid saved connection settings.')
    virtual, plug = result['virtual'], result['smartPlug']
    if not isinstance(virtual,dict) or set(virtual) != {'model'} or virtual['model'] not in VIRTUAL_MODELS:
        raise ValueError('Choose Cybertruck, Model X or Model 3 for the virtual profile.')
    if (not isinstance(plug,dict) or set(plug) != {'address','vehicleSource','followVirtual'}
            or plug['vehicleSource'] not in ('virtual','tesla') or type(plug['followVirtual']) is not bool):
        raise ValueError('Choose a virtual or Tesla vehicle for the smart plug.')
    if plug['address'] not in (None, ''):
        plug['address'] = local_address(plug['address'])
    else:
        plug['address'] = None
    if result['updatedAt'] is not None:
        try:
            parsed = datetime.fromisoformat(result['updatedAt'].replace('Z','+00:00'))
            if parsed.tzinfo is None:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise ValueError('Invalid saved connection timestamp.') from None
    return result


def updated(settings, body, now, *, last_writer_wins=False):
    """Validate an explicit selection/calibration without activating hardware.

    Profile changes retain the current campaign's capacity/rated power. Omit SOC
    and plug fields on profile-only edits to preserve moving live observations.
    """
    if (not isinstance(body,dict) or set(body)-{'mode','virtual','smartPlug','expectedRevision'}
            or not {'mode','expectedRevision'} <= set(body)):
        raise ValueError('Send a connection mode and its saved revision.')
    previous = normalize(settings.get('connection'))
    expected = body['expectedRevision']
    if (type(expected) is not int or expected < 0 or expected > previous['revision']
            or (not last_writer_wins and expected != previous['revision'])):
        raise ValueError('Connection settings changed. Reload them before saving.')
    if body['mode'] not in MODES:
        raise ValueError('Choose Smart Plug, Tesla or Virtual.')
    if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Use a timezone-aware setup time.')
    chosen = deepcopy(previous)
    chosen['mode'] = body['mode']
    result = deepcopy(settings)
    if 'virtual' in body:
        virtual = body['virtual']
        if not isinstance(virtual,dict) or set(virtual)-{'model','batteryPercent','pluggedIn'}:
            raise ValueError('Choose a valid virtual vehicle profile and state.')
        if 'model' in virtual:
            if virtual['model'] not in VIRTUAL_MODELS:
                raise ValueError('Choose Cybertruck, Model X or Model 3 for the virtual profile.')
            chosen['virtual']['model'] = virtual['model']
        if {'batteryPercent','pluggedIn'} & set(virtual):
            model = settings.get('virtualCarState') or {}
            state = {'batteryPercent':virtual.get('batteryPercent',model.get('batteryPercent')),
                     'pluggedIn':virtual.get('pluggedIn',model.get('pluggedIn'))}
            result = calibrate_virtual(result,state,now)
    if 'smartPlug' in body:
        plug = body['smartPlug']
        if not isinstance(plug,dict) or set(plug)-{'address','vehicleSource','followVirtual'}:
            raise ValueError('Choose a local smart plug address and its vehicle source.')
        if 'address' in plug:
            address = plug['address']
            chosen['smartPlug']['address'] = None if address in (None,'') else local_address(address)
        if 'vehicleSource' in plug:
            if plug['vehicleSource'] not in ('virtual','tesla'):
                raise ValueError('Choose a virtual or Tesla vehicle for the smart plug.')
            chosen['smartPlug']['vehicleSource'] = plug['vehicleSource']
        if 'followVirtual' in plug:
            if type(plug['followVirtual']) is not bool:
                raise ValueError('Choose On or Off for the virtual car demo.')
            chosen['smartPlug']['followVirtual'] = plug['followVirtual']
    if chosen == previous and result == settings:
        return result
    chosen.update(revision=previous['revision']+1, updatedAt=now.astimezone(timezone.utc).isoformat().replace('+00:00','Z'))
    result['connection'] = normalize(chosen)
    return result


def view(settings, runtime=None, now=None):
    """Public setup/readiness view. Runtime is trusted adapter state, never secrets."""
    config = normalize(settings.get('connection'))
    runtime = runtime if isinstance(runtime,dict) else {}
    model = settings.get('virtualCarState') or {}
    plug_state = runtime.get('smartPlug') or {}
    tesla_state = runtime.get('tesla') or {}
    selected_address = config['smartPlug']['address']
    # A changed IP must not inherit the previous plug's verified state.
    same_plug = selected_address is not None and plug_state.get('address') == selected_address
    fresh = False
    try:
        checked = datetime.fromisoformat(plug_state['lastCheckedAt'].replace('Z','+00:00'))
        if checked.tzinfo is not None:
            fresh = -5 <= ((now or datetime.now(timezone.utc))-checked).total_seconds() <= 120
    except (KeyError,TypeError,ValueError,AttributeError):
        pass
    verified = same_plug and fresh and plug_state.get('model') == MODEL and plug_state.get('metering') is True and plug_state.get('status') == 'connected'
    status = 'connected' if verified else 'not_configured' if selected_address is None else 'unverified'
    if same_plug and not verified:
        if plug_state.get('status') == 'connected' and not fresh:
            status = 'stale'
        elif plug_state.get('status') in ('auth_required','unavailable','unsupported','error','unverified'):
            status = plug_state['status']
    plug = {'address':selected_address, 'vehicleSource':config['smartPlug']['vehicleSource'],
            'followVirtual':config['smartPlug']['followVirtual'],
            'status':status,
            'lastCheckedAt':plug_state.get('lastCheckedAt') if same_plug else None,
            'powerW':plug_state.get('powerW') if verified else None,
            'error':plug_state.get('error') if same_plug else None,
            'model':MODEL, 'deviceName':DEVICE_NAME, 'verified':verified}
    for key in ('voltageV','switchOn','deviceId','authRequired'):
        if verified and key in plug_state:
            plug[key] = plug_state[key]
    tesla = {'configured':tesla_state.get('configured') is True, 'authorized':tesla_state.get('authorized') is True,
             'status':tesla_state.get('status','not_configured'), 'vehicleName':tesla_state.get('vehicleName'),
             'error':tesla_state.get('error'), 'commandsReady':tesla_state.get('commandsReady') is True}
    for key in ('vehicles','vehicleId','accounts','accountId','keyPairingURL','chargeState'):
        if key in tesla_state:
            tesla[key] = deepcopy(tesla_state[key])
    if isinstance(tesla.get('chargeState'),dict):
        try:
            observed = datetime.fromisoformat(tesla['chargeState']['updatedAt'].replace('Z','+00:00'))
            tesla['chargeState']['stale'] = (tesla['chargeState'].get('readFailed') is True
                or not -5 <= ((now or datetime.now(timezone.utc))-observed).total_seconds() <= 120)
        except (KeyError,TypeError,ValueError,AttributeError):
            tesla['chargeState']['stale'] = True
    source = config['smartPlug']['vehicleSource'] if config['mode'] == 'smart_plug' else config['mode']
    ready = (config['mode'] == 'virtual' or (config['mode'] == 'tesla' and tesla['authorized'] and tesla['commandsReady'])
             or (config['mode'] == 'smart_plug' and verified and (source == 'virtual' or tesla['authorized'])))
    return {'schemaVersion':1, 'revision':config['revision'], 'mode':config['mode'],
            'activeMode':runtime.get('activeMode'), 'ready':ready,
            'virtual':{'model':config['virtual']['model'], 'batteryPercent':model.get('batteryPercent'),
                       'pluggedIn':model.get('pluggedIn'), 'profileOnly':True},
            'smartPlug':plug, 'tesla':tesla, 'updatedAt':config['updatedAt']}
