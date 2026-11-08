"""Relie le radar, la logique de place et la barre ; garde calibrage et niveaux d'origine sur disque."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
import time

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .act import ZERO_DB, BarError, read_rear, write_rear
from .const import DOMAIN
from .seat import PRESETS, Calibration, Point, SeatTracker, Spot, Target

_LOGGER = logging.getLogger(__name__)
TICK = timedelta(seconds=1)


def parse_targets(attributes: dict) -> list[Target]:
    """`targets` de l'intégration aqara_fp400 : [{id, x, y, …}] en cm."""
    out = []
    for t in attributes.get("targets") or []:
        try:
            out.append(Target(int(t["id"]), float(t["x"]), float(t["y"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


class SeatController:
    def __init__(self, hass: HomeAssistant, entry_id: str, host: str, tracker: str) -> None:
        self.hass = hass
        self.host = host
        self.tracker_entity = tracker
        self._store = Store(hass, 1, f"{DOMAIN}.{entry_id}")
        self.calibration = Calibration()
        self.seat = SeatTracker(self.calibration)
        self.enabled = False
        self.last_error: str | None = None
        # Niveaux bruts d'avant le centrage : remis à l'arrêt, même après un redémarrage de HA.
        self._base: tuple[int, int] | None = None
        self._written = (0, 0)
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

    # --- cycle de vie ---

    async def async_start(self) -> None:
        data = await self._store.async_load() or {}
        self.calibration = Calibration.from_list(data.get("spots"))
        self.seat.calibration = self.calibration
        base = data.get("base")
        self._base = tuple(base) if base else None
        if data.get("enabled"):
            await self.async_set_enabled(True)
        elif self._base is not None:
            await self._restore()

    async def async_stop(self) -> None:
        """Déchargement : on rend les niveaux d'origine mais on retient que c'était actif."""
        was_enabled = self.enabled
        await self.async_set_enabled(False)
        if was_enabled:
            await self._save(enabled=True)

    async def async_set_enabled(self, enabled: bool) -> None:
        if enabled == self.enabled:
            return
        if enabled:
            if self._base is None:
                try:
                    self._base = await read_rear(self.host)
                except BarError as e:
                    raise HomeAssistantError(str(e)) from e
            self._written = (0, 0)
            self.enabled = True
            await self._save()
            self._unsubs = [
                async_track_state_change_event(self.hass, self.tracker_entity, self._on_tracker),
                async_track_time_interval(self.hass, self._on_tick, TICK),
            ]
            self._process(self.seat.update(self._current_targets(), time.monotonic()))
        else:
            for unsub in self._unsubs:
                unsub()
            self._unsubs = []
            self.enabled = False
            await self._restore()
        self._notify()

    # --- calibrage ---

    async def async_save_spot(self, name: str, left_db: int | None, right_db: int | None) -> dict:
        """Enregistre la position actuelle de la seule personne vue par le radar."""
        targets = self._current_targets()
        if len(targets) != 1:
            raise ServiceValidationError(
                f"Le radar voit {len(targets)} personne(s) : il en faut exactement une pour le calibrage"
            )
        old = self.calibration.spots.get(name)
        default = (old.left_db, old.right_db) if old else PRESETS.get(name, (0, 0))
        at = Point(targets[0].x, targets[0].y)
        self.calibration.spots[name] = Spot(
            name, at, default[0] if left_db is None else left_db, default[1] if right_db is None else right_db
        )
        return await self._calibration_changed()

    async def async_set_spot_levels(self, name: str, left_db: int, right_db: int) -> dict:
        spot = self._spot(name)
        self.calibration.spots[name] = Spot(name, spot.at, left_db, right_db)
        return await self._calibration_changed()

    async def async_remove_spot(self, name: str) -> dict:
        self._spot(name)
        del self.calibration.spots[name]
        return await self._calibration_changed()

    async def async_clear_calibration(self) -> dict:
        self.calibration.spots.clear()
        return await self._calibration_changed()

    def _spot(self, name: str) -> Spot:
        if name not in self.calibration.spots:
            raise ServiceValidationError(f"Position « {name} » inconnue")
        return self.calibration.spots[name]

    async def _calibration_changed(self) -> dict:
        self.seat = SeatTracker(self.calibration, applied=self.seat.applied)
        await self._save()
        self._notify()
        return self.status()

    # --- état ---

    def status(self) -> dict:
        targets = self._current_targets()
        pos = self.seat.position
        return {
            "enabled": self.enabled,
            "spots": self.calibration.to_list(),
            "targets": [
                {"id": t.id, "x": t.x, "y": t.y, "followed": t.id == self.seat.locked_id} for t in targets
            ],
            "in_zone": self.seat.in_zone,
            "position": None if pos is None else {"x": pos.x, "y": pos.y},
            "offsets_db": {"left": self.seat.applied[0], "right": self.seat.applied[1]},
            "levels_db": self.levels_db(),
            "error": self.last_error,
        }

    def levels_db(self) -> dict | None:
        if self._base is None:
            return None
        left, right = (min(max(b + o, 0), 24) - ZERO_DB for b, o in zip(self._base, self._written))
        return {"left": left, "right": right}

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --- radar → barre ---

    def _current_targets(self) -> list[Target]:
        state = self.hass.states.get(self.tracker_entity)
        return parse_targets(state.attributes) if state is not None else []

    @callback
    def _on_tracker(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        targets = parse_targets(new.attributes) if new is not None else []
        self._process(self.seat.update(targets, time.monotonic()))
        self._notify()

    @callback
    def _on_tick(self, _now) -> None:
        self._process(self.seat.tick(time.monotonic()))

    @callback
    def _process(self, wanted: tuple[int, int]) -> None:
        if self.enabled and wanted != self._written:
            self.hass.async_create_task(self._write(wanted))

    async def _write(self, wanted: tuple[int, int]) -> None:
        async with self._lock:
            if not self.enabled or wanted == self._written or self._base is None:
                return
            try:
                for side, base, old, new in zip(("Left", "Right"), self._base, self._written, wanted):
                    if new != old:
                        await write_rear(self.host, side, base + new)
                self._written = wanted
                self.last_error = None
            except BarError as e:
                self.last_error = str(e)
                _LOGGER.warning("%s", e)
        self._notify()

    async def _restore(self) -> None:
        async with self._lock:
            if self._base is not None:
                try:
                    for side, raw in zip(("Left", "Right"), self._base):
                        await write_rear(self.host, side, raw)
                except BarError as e:
                    # On garde les niveaux d'origine : nouvel essai au prochain démarrage.
                    self.last_error = str(e)
                    _LOGGER.warning("niveaux d'origine non remis : %s", e)
                    await self._save()
                    return
            self._base = None
            self._written = (0, 0)
            self.last_error = None
            await self._save()

    async def _save(self, enabled: bool | None = None) -> None:
        await self._store.async_save(
            {
                "spots": self.calibration.to_list(),
                "base": list(self._base) if self._base else None,
                "enabled": self.enabled if enabled is None else enabled,
            }
        )
