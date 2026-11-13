"""One durable nightly routine shared by the phone, screen and Pi supervisor."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import uuid

from .app_test import CHICAGO, local_instant, VIRTUAL_CAPACITY_KWH, VIRTUAL_EFFICIENCY, VIRTUAL_POWER_KW
from .campaign import validate_campaign, sessions

FIELDS = {'enabled', 'startDate', 'nights', 'startMinutes', 'departureMinutes',
          'initialBatteryPercent', 'targetBatteryPercent', 'screenSleepMinutes'}


def normalize(value):
    if not isinstance(value, dict):
        raise ValueError('No nightly routine has been configured.')
    result = validate_campaign(value)
    enabled, revision = value.get('enabled', True), value.get('revision', 1)
    if type(enabled) is not bool or type(revision) is not int or revision < 1:
        raise ValueError('Invalid saved nightly routine.')
    return dict(result, enabled=enabled, revision=revision)


def view(settings, now):
    raw = settings.get('campaign')
    if raw is None:
        return None
    config = normalize(raw)
    choices = sessions(config)
    saved = settings.get('overnightRun') or {}
    current = saved if saved.get('campaignId') == config['campaignId'] else None
    next_night = next((night for night in choices if datetime.fromisoformat(night['start']) > now), None)
    phase = 'betweenNights'
    if not config['enabled']:
        phase = 'paused'
    elif current and not current.get('finalizedAt') and now < datetime.fromisoformat(current['departure']):
        phase = 'running' if now >= datetime.fromisoformat(current['start']) else 'waiting'
    elif now >= datetime.fromisoformat(choices[-1]['departure']):
        phase = 'complete'
    elif now < datetime.fromisoformat(choices[0]['start']):
        phase = 'waiting'
    return dict(config, firstStart=choices[0]['start'], finalDeparture=choices[-1]['departure'],
                currentSessionId=current.get('sessionId') if current else None,
                current=deepcopy(current), phase=phase,
                nextStart=next_night['start'] if config['enabled'] and next_night else None)


def updated(settings, body, now, *, last_writer_wins=False):
    """Validate an edit atomically; keep the live battery and its energy ledger."""
    if not isinstance(body, dict) or set(body) != FIELDS | {'expectedRevision'}:
        raise ValueError('Send the complete nightly routine and its saved revision.')
    previous = normalize(settings['campaign']) if settings.get('campaign') else None
    expected = body['expectedRevision']
    current_revision=previous['revision'] if previous else 0
    valid_revision=(type(expected) is int and 0 <= expected <= current_revision) if last_writer_wins else (type(expected) is int and expected == current_revision)
    if not valid_revision:
        raise ValueError('The routine changed on another device. Reload it and review your changes.')
    if type(body['enabled']) is not bool or type(body['nights']) is not int or not 1 <= body['nights'] <= 90:
        raise ValueError('Choose an enabled state and between 1 and 90 nights.')
    ident = previous['campaignId'] if previous else f'campaign-{body["startDate"]}-{uuid.uuid4().hex[:8]}'
    config = normalize(dict({key: body[key] for key in FIELDS}, campaignId=ident,
                            strategy='day_ahead_live', timezone='America/Chicago',
                            revision=(previous['revision'] if previous else 0)+1))
    local_day = now.astimezone(CHICAGO).date()
    first_day = date.fromisoformat(config['startDate'])
    if first_day > local_day + timedelta(days=365):
        raise ValueError('Choose a first night within the next year.')
    if first_day < local_day and (not previous or first_day != date.fromisoformat(previous['startDate'])):
        raise ValueError('Choose today or a future first night.')
    if config['enabled']:
        needed = (config['targetBatteryPercent']-config['initialBatteryPercent']) / 100 * VIRTUAL_CAPACITY_KWH / (VIRTUAL_POWER_KW*VIRTUAL_EFFICIENCY)
        available = min((datetime.fromisoformat(n['departure'])-datetime.fromisoformat(n['start'])).total_seconds()/3600
                        for n in sessions(config))
        if needed > available + 1e-8:
            raise ValueError('This routine window is too short to reach the target. Choose an earlier start or later deadline.')
    if previous and all(config[key] == previous[key] for key in FIELDS):
        return deepcopy(settings)
    result = deepcopy(settings)
    saved = result.get('overnightRun') or {}
    unfinished = saved.get('campaignId') == ident and not saved.get('finalizedAt') \
        and now < datetime.fromisoformat(saved['departure'])
    if unfinished:
        if config['startDate'] != previous['startDate']:
            raise ValueError('Keep the first date until the current night finishes. You can pause the routine now.')
        if saved.get('nightIndex', 1) > config['nights']:
            raise ValueError('Keep the current night within the routine, or pause the routine.')
        old_start = datetime.fromisoformat(saved['start'])
        start_changed = config['startMinutes'] != previous['startMinutes']
        end_changed = config['departureMinutes'] != previous['departureMinutes']
        start = old_start
        departure = datetime.fromisoformat(saved['departure'])
        if start_changed or end_changed:
            night_day = old_start.astimezone(CHICAGO).date()
            old_start_local = old_start.astimezone(CHICAGO)
            end_local = departure.astimezone(CHICAGO)
            start_minutes = config['startMinutes'] if start_changed else old_start_local.hour*60+old_start_local.minute
            end_minutes = config['departureMinutes'] if end_changed else end_local.hour*60+end_local.minute
            start = local_instant(night_day, start_minutes)
            end_day = night_day + timedelta(days=end_minutes <= start_minutes)
            departure = local_instant(end_day, end_minutes)
        if start_changed and start < now and start != old_start:
            raise ValueError('That start time has passed. Keep the current start or choose a future time.')
        if departure <= now:
            raise ValueError('Choose a future morning deadline, or pause the routine.')
        model = result['virtualCarState']
        if config['initialBatteryPercent'] != previous['initialBatteryPercent'] and now < old_start and model['energyKWh'] <= 1e-8:
            if config['initialBatteryPercent'] != saved['initialBatteryPercent']:
                model.update(batteryPercent=float(config['initialBatteryPercent']), energyKWh=0.0,
                             cost=0.0, immediateCost=0.0, energyByHour={})
            saved['initialBatteryPercent'] = config['initialBatteryPercent']
        target = config['targetBatteryPercent'] if config['targetBatteryPercent'] != previous['targetBatteryPercent'] else saved['targetBatteryPercent']
        sleep = config['screenSleepMinutes'] if config['screenSleepMinutes'] != previous['screenSleepMinutes'] else saved['screenSleepMinutes']
        needed = max(0, target-model['batteryPercent']) / 100 * VIRTUAL_CAPACITY_KWH / (VIRTUAL_POWER_KW*VIRTUAL_EFFICIENCY)
        if config['enabled'] and needed > (departure-max(start, now)).total_seconds()/3600 + 1e-8:
            raise ValueError('This window is too short to reach the target. Choose an earlier start or later deadline.')
        changed = any((saved['start'] != start.astimezone(CHICAGO).isoformat(),
                       saved['departure'] != departure.astimezone(CHICAGO).isoformat(),
                       saved['targetBatteryPercent'] != target))
        saved.update(start=start.astimezone(CHICAGO).isoformat(), departure=departure.astimezone(CHICAGO).isoformat(),
                     targetBatteryPercent=target, screenSleepMinutes=sleep,
                     revision=saved.get('revision',0)+1, updatedAt=now.astimezone(timezone.utc).isoformat())
        result['weekly'] = dict(result['weekly'], enabled=False, targetPercent=target,
                                fixedStartMinutes=config['startMinutes'], departureMinutes=config['departureMinutes'])
        result['today'] = {'targetPercent':target, 'departure':departure.isoformat()}
        if changed or config['enabled'] != previous['enabled']:
            result['appTestControl'] = {'manualState':None, 'manualUntil':None}
            result.pop('smartChargePlan',None)
    # Sleep and default target remain visible between nights as well.
    elif saved.get('campaignId') == ident and previous and config['screenSleepMinutes'] != previous['screenSleepMinutes']:
        saved['screenSleepMinutes'] = config['screenSleepMinutes']
    result['campaign'] = config
    result['weekly'] = dict(result['weekly'], enabled=False)
    return result
