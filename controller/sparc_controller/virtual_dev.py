"""Manual simulated-car calibration; measured electricity is never fabricated."""
from copy import deepcopy
from datetime import datetime, timezone
import math

from .app_test import AppTestControl


def _state(settings):
    model = AppTestControl.virtual_state(settings)
    saved = settings.get('developerState') or {}
    if not isinstance(saved, dict) or type(saved.get('revision', 0)) is not int or saved.get('revision', 0) < 0:
        raise ValueError('Invalid saved developer state.')
    return model, saved


def view(settings, now=None):
    model, saved = _state(settings)
    return {'enabled':AppTestControl.virtual_enabled(settings), 'simulated':True,
            'batteryPercent':model['batteryPercent'], 'pluggedIn':model['pluggedIn'],
            'revision':saved.get('revision', 0), 'updatedAt':saved.get('updatedAt'),
            'manualCalibration':bool(saved.get('lastAdjustment'))}


def updated(settings, body, now):
    """Change only SOC/plug state, retaining counters, schedules and manual intent.

    The service archives the old session first, then commits this value and
    replans the remaining real-price policy. Changing SOC itself consumes no kWh.
    """
    if not AppTestControl.virtual_enabled(settings):
        raise ValueError('Turn on the virtual car before changing its state.')
    if not isinstance(body, dict) or set(body) != {'batteryPercent', 'pluggedIn'}:
        raise ValueError('Choose a battery percentage and plugged-in state.')
    battery, plugged = body['batteryPercent'], body['pluggedIn']
    if (type(battery) not in (int, float) or not math.isfinite(battery) or not 0 <= battery <= 100
            or type(plugged) is not bool):
        raise ValueError('Choose a battery percentage from 0 to 100 and a plugged-in state.')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Use a timezone-aware adjustment time.')
    model, saved = _state(settings)
    if model['batteryPercent'] == battery and model['pluggedIn'] == plugged:
        return deepcopy(settings)
    result = deepcopy(settings)
    before = {'batteryPercent':model['batteryPercent'], 'pluggedIn':model['pluggedIn']}
    after = {'batteryPercent':float(battery), 'pluggedIn':plugged}
    result['virtualCarState'].update(after)
    when = now.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    config = settings.get('overnightRun') or {}
    annotation = {'kind':'manual_calibration', 'updatedAt':when, 'from':before, 'to':after,
                  'campaignId':config.get('campaignId'), 'sessionId':config.get('sessionId'), 'start':config.get('start')}
    developer = deepcopy(saved)
    adjustments = developer.get('adjustments', [])
    if not isinstance(adjustments, list):
        raise ValueError('Invalid saved calibration history.')
    developer.update(revision=saved.get('revision', 0)+1, updatedAt=when, lastAdjustment=annotation,
                     adjustments=adjustments+[annotation])
    result['developerState'] = developer
    # Retain appTestControl exactly: reconnecting does not cancel a user's Pause.
    result.pop('smartChargePlan', None)
    return result
