"""Calendar-owned virtual charging windows. No real-car commands or battery resets."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import re


def instant(value):
    if not isinstance(value,str):raise ValueError("Send an absolute sleep-event timestamp.")
    try: result=datetime.fromisoformat(value.replace("Z","+00:00"))
    except ValueError:raise ValueError("Send an absolute sleep-event timestamp.") from None
    if result.tzinfo is None:raise ValueError("Sleep events need a timezone.")
    return result.astimezone(timezone.utc)


def normalized(body,now):
    if not isinstance(body,dict) or set(body)!={"enabled","windows","targetPercent"} or type(body['enabled']) is not bool:
        raise ValueError("Send a complete Calendar sleep-window update.")
    if type(body['targetPercent']) is not int or not 50<=body['targetPercent']<=100:raise ValueError("Choose a target between 50 and 100 percent.")
    windows=body['windows']
    if not isinstance(windows,list) or len(windows)>14:raise ValueError("Sync up to 14 sleep events.")
    result=[]
    for window in windows:
        if not isinstance(window,dict) or set(window)!={"id","start","departure"} or not isinstance(window['id'],str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,96}',window['id']):raise ValueError("Choose valid sleep events.")
        start,end=instant(window['start']),instant(window['departure'])
        if not timedelta(minutes=30)<=end-start<=timedelta(hours=24) or end<=now or start<now-timedelta(days=1) or end>now+timedelta(days=32):raise ValueError("Choose future overnight sleep events within the next month.")
        result.append(dict(id=window['id'],start=start.isoformat(),departure=end.isoformat()))
    result.sort(key=lambda w:w['start'])
    if len({w['id'] for w in result})!=len(result) or any(instant(b['start'])<instant(a['departure']) for a,b in zip(result,result[1:])):raise ValueError("Sleep events must not overlap or repeat.")
    return dict(enabled=body['enabled'],windows=result,targetPercent=body['targetPercent'],updatedAt=now.isoformat())


def updated(settings,body,now,client_id):
    value=normalized(body,now)
    old=settings.get('sleepCalendar') or {}
    if old.get('enabled') and old.get('ownerClientId') not in (None,client_id):raise ValueError("Calendar sync is controlled by another phone. Disconnect it there first.")
    result=deepcopy(settings)
    if value['enabled'] and not old.get('enabled'):
        result['calendarPreviousWeekly']=deepcopy(settings['weekly'])
    value['ownerClientId']=client_id
    result['sleepCalendar']=value
    if value['enabled']:
        result['weekly']=dict(result['weekly'],enabled=False)
        if result.get('campaign'):result['campaign']=dict(result['campaign'],enabled=False)
    elif old.get('enabled'):
        result['weekly']=result.pop('calendarPreviousWeekly',result['weekly'])
        if (result.get('overnightRun') or {}).get('calendarOwned'):
            result.pop('overnightRun',None);result['today']=None;result.pop('smartChargePlan',None)
            result['appTestControl']={'manualState':'waiting','manualUntil':None}
    return result


def next_window(settings,now):
    value=settings.get('sleepCalendar') or {}
    if not value.get('enabled'):return None
    return next((w for w in value.get('windows',[]) if instant(w['departure'])>now),None)


def apply_window(settings,now):
    """Return an updated copy only when the chosen event changes; retain measured SOC."""
    value=settings.get('sleepCalendar') or {}
    if not value.get('enabled'):return settings
    window=next_window(settings,now);old=settings.get('overnightRun') or {}
    if window is None:
        if not old.get('calendarOwned'):return settings
        result=deepcopy(settings);result.pop('overnightRun',None);result.pop('smartChargePlan',None);result['today']=None
        result['appTestControl']={'manualState':'waiting','manualUntil':None}
        return result
    target=value['targetPercent']
    if old.get('calendarEventId')==window['id'] and old.get('start')==window['start'] and old.get('departure')==window['departure'] and old.get('targetBatteryPercent')==target:return settings
    result=deepcopy(settings);model=result['virtualCarState']
    same=old.get('calendarEventId')==window['id']
    if not same:
        model.update(energyKWh=0.0,cost=0.0,immediateCost=0.0,energyByHour={})
    result['overnightRun']={'calendarOwned':True,'calendarEventId':window['id'],'sessionId':'calendar-'+window['id'],
                            'start':window['start'],'departure':window['departure'],'targetBatteryPercent':target,
                            'initialBatteryPercent':old.get('initialBatteryPercent',model['batteryPercent']) if same else model['batteryPercent'],
                            'strategy':'day_ahead_live','screenSleepMinutes':5,'readyBufferMinutes':30,'updatedAt':now.isoformat()}
    result['today']={'targetPercent':target,'departure':window['departure']}
    result['weekly']=dict(result['weekly'],enabled=False,targetPercent=target)
    result.pop('smartChargePlan',None)
    result['appTestControl']={'manualState':None,'manualUntil':None}
    return result


def public(settings,now):
    value=settings.get('sleepCalendar') or {}
    window=next_window(settings,now)
    return {'enabled':bool(value.get('enabled')),'updatedAt':value.get('updatedAt'),
            'windowCount':sum(instant(w['departure'])>now for w in value.get('windows',[])),
            'nextStart':window['start'] if window else None,'nextWake':window['departure'] if window else None,
            'source':'Apple Calendar','automaticTesla':False}
