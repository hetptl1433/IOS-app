"""Validated, one-night settings shared by the touchscreen and run recorder."""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path

from .app_test import CHICAGO, VIRTUAL_POWER_KW, VIRTUAL_EFFICIENCY, VIRTUAL_CAPACITY_KWH, local_instant


def current_config(settings):
    value=settings.get('overnightRun')
    if isinstance(value,dict): return dict(value)
    price_log=os.environ.get('SPARC_PRICE_LOG')
    if price_log:
        try:
            value=json.loads(Path(price_log).with_name('manifest.json').read_text())
            if isinstance(value,dict): return value
        except (OSError,ValueError): pass
    return None


def updated_settings(settings,body,now):
    previous=current_config(settings)
    if previous is None: raise ValueError('No overnight run is configured.')
    fields={'startMinutes':(0,1439),'departureMinutes':(0,1439),'targetPercent':(50,100),
            'initialBatteryPercent':(0,99),'screenSleepMinutes':(1,120),'dayOffset':(-1,1)}
    for key,(low,high) in fields.items():
        value=body.get(key)
        if type(value) is not int or not low<=value<=high: raise ValueError('Choose valid times, battery percentages and screen sleep minutes.')
    if body['initialBatteryPercent']>=body['targetPercent']:
        raise ValueError('The starting battery must be below the target.')
    now=now.astimezone(timezone.utc)
    day=now.astimezone(CHICAGO).date()+timedelta(days=body['dayOffset'])
    start=local_instant(day,body['startMinutes'])
    end_day=day+timedelta(days=body['departureMinutes']<=body['startMinutes'])
    departure=local_instant(end_day,body['departureMinutes'])
    old_start=datetime.fromisoformat(previous['start']).astimezone(timezone.utc)
    if start<now and start!=old_start:
        raise ValueError('Choose a future start time, or keep the current start time.')
    if not now<departure<=now+timedelta(hours=48):
        raise ValueError('Choose a morning deadline within the next 48 hours.')
    model=dict(settings['virtualCarState'])
    initial_changed=body['initialBatteryPercent']!=previous['initialBatteryPercent']
    if initial_changed:
        if now>=old_start or model['energyKWh']>1e-8:
            raise ValueError('Starting battery can only be changed before charging begins.')
        model.update(batteryPercent=float(body['initialBatteryPercent']),energyKWh=0.0,cost=0.0,immediateCost=0.0,energyByHour={})
    needed=max(0,body['targetPercent']-model['batteryPercent'])/100*VIRTUAL_CAPACITY_KWH/(VIRTUAL_POWER_KW*VIRTUAL_EFFICIENCY)
    reserve = 0 if previous.get("strategy") == "day_ahead_live" else 30
    available=(departure-timedelta(minutes=reserve)-max(now,start)).total_seconds()/3600
    if needed>available+1e-8:
        raise ValueError('This window is too short to reach the target. Start earlier or choose a later morning time.')
    config=dict(previous,start=start.astimezone(CHICAGO).isoformat(),departure=departure.astimezone(CHICAGO).isoformat(),
                initialBatteryPercent=body['initialBatteryPercent'],targetBatteryPercent=body['targetPercent'],
                screenSleepMinutes=body['screenSleepMinutes'],updatedAt=now.isoformat(),revision=previous.get('revision',0)+1)
    result=dict(settings,overnightRun=config,virtualCarState=model)
    result['weekly']=dict(settings['weekly'],enabled=False,mode='fixed',fixedStartMinutes=body['startMinutes'],
                          departureMinutes=body['departureMinutes'],targetPercent=body['targetPercent'])
    result['today']={'targetPercent':body['targetPercent'],'departure':departure.isoformat()}
    material_change = any(config[key] != previous[key] for key in ('start','departure','initialBatteryPercent','targetBatteryPercent'))
    if material_change: result['appTestControl']={'manualState':None,'manualUntil':None}
    earliest_finish=(max(now,start)+timedelta(hours=needed)).astimezone(CHICAGO).isoformat()
    if previous.get('strategy') == 'day_ahead_live':
        config.pop('expectedTargetAt',None)
        config['earliestPossibleTargetAt']=earliest_finish
    else: config['expectedTargetAt']=earliest_finish
    if material_change: result.pop('smartChargePlan',None)
    return result
