"""Logique de place (seat.py) avec le calibrage réel du 2026-11-08 : capteur à côté du canapé."""

import importlib.util
from pathlib import Path
import sys

_spec = importlib.util.spec_from_file_location(
    "seat", Path(__file__).parents[1] / "custom_components/heos_seat/seat.py"
)
seat = importlib.util.module_from_spec(_spec)
sys.modules["seat"] = seat
_spec.loader.exec_module(seat)

LEFT, CENTER, RIGHT = seat.Point(-45, 189), seat.Point(-34, 125), seat.Point(-5, 84)


def couch(**extra):
    spots = {
        "left": seat.Spot("left", LEFT, -5, 7),
        "center": seat.Spot("center", CENTER, 0, 0),
        "right": seat.Spot("right", RIGHT, 7, -5),
        **extra,
    }
    return seat.SeatTracker(seat.Calibration(spots))


def me(p, id=0):
    return seat.Target(id, p.x, p.y)


def hold(tr, targets, start, seconds):
    """Le radar publie la même position plusieurs fois pendant que le temps passe."""
    out = None
    for i in range(int(seconds * 2) + 1):
        out = tr.update(targets, start + i / 2)
    return out


def test_calibrated_spots_give_their_own_levels():
    tr = couch()
    assert hold(tr, [me(LEFT)], 0, 3) == (-5, 7)
    assert hold(tr, [me(CENTER)], 10, 4) == (0, 0)
    assert hold(tr, [me(RIGHT)], 20, 4) == (7, -5)


def test_between_spots_levels_are_interpolated():
    cal = couch().calibration
    halfway = seat.Point((LEFT.x + CENTER.x) / 2, (LEFT.y + CENTER.y) / 2)
    left, right = cal.interpolate(halfway)
    assert -5 < left < 0 and 0 < right < 7
    # Plus près de la gauche : plus proche de son réglage.
    near_left = seat.Point(LEFT.x + 0.25 * (CENTER.x - LEFT.x), LEFT.y + 0.25 * (CENTER.y - LEFT.y))
    assert cal.interpolate(near_left)[0] < left


def test_extra_spot_is_used():
    armchair = seat.Point(80, 150)
    tr = couch(armchair=seat.Spot("armchair", armchair, 3, 3))
    assert hold(tr, [me(armchair)], 0, 3) == (3, 3)


def test_change_needs_dwell():
    tr = couch()
    hold(tr, [me(LEFT)], 0, 3)
    tr.update([me(RIGHT)], 10)
    assert tr.update([me(RIGHT)], 11) == (-5, 7)


def test_passer_by_far_from_spots_is_ignored():
    tr = couch()
    passer = seat.Target(1, -150, 300)  # devant la TV
    assert hold(tr, [passer], 0, 3) == (0, 0)
    assert tr.locked_id is None
    assert hold(tr, [passer, me(LEFT)], 5, 3) == (-5, 7)
    assert tr.locked_id == 0 and tr.in_zone == 1


def test_second_person_in_zone_does_not_steal_tracking():
    tr = couch()
    hold(tr, [me(LEFT)], 0, 3)
    assert hold(tr, [me(LEFT), me(RIGHT, id=1)], 5, 5) == (-5, 7)
    assert tr.locked_id == 0 and tr.in_zone == 2


def test_back_to_origin_after_leaving():
    tr = couch()
    hold(tr, [me(LEFT)], 0, 3)
    # Dernière détection à t = 3 : une sortie brève ne change rien.
    assert tr.update([], 4) == (-5, 7)
    assert tr.tick(3 + seat.ABSENT_S - 1) == (-5, 7)
    assert tr.tick(3 + seat.ABSENT_S) == (0, 0)
    assert tr.position is None


def test_handover_when_followed_person_leaves():
    tr = couch()
    hold(tr, [me(LEFT)], 0, 3)
    assert hold(tr, [me(RIGHT, id=1)], 5, 3) == (7, -5)
    assert tr.locked_id == 1


def test_uncalibrated_changes_nothing():
    tr = seat.SeatTracker(seat.Calibration())
    assert hold(tr, [me(LEFT)], 0, 3) == (0, 0)


def test_calibration_roundtrip():
    cal = couch().calibration
    assert seat.Calibration.from_list(cal.to_list()) == cal
    assert not seat.Calibration.from_list(None).ready
