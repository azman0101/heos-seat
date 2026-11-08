"""Réglage des arrières selon la position sur le canapé, sans dépendance à Home Assistant.

Le calibrage est une liste de positions nommées, relevées au radar, chacune avec son
réglage (Surround G, Surround D). Entre ces positions, le réglage est interpolé par
pondération inverse de la distance. Seules les personnes proches d'une position
calibrée comptent : le capteur peut être placé n'importe où, et un passant est ignoré.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from statistics import median

ZONE_CM = 70  # au-delà de cette distance de toute position calibrée, la personne est ignorée
EXACT_CM = 5  # assez près d'une position pour prendre son réglage tel quel
POWER = 2  # pondération inverse de la distance
WINDOW_S = 2.0  # médiane glissante des positions
DWELL_S = 2.0  # un nouveau réglage doit tenir ce temps avant d'être appliqué
ABSENT_S = 10.0  # sans personne dans la zone, retour aux niveaux d'origine

# Réglages par défaut (Surround G, Surround D) en dB : on baisse l'arrière proche.
PRESETS = {"left": (-5, 7), "center": (0, 0), "right": (7, -5)}


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def dist(self, other: Point) -> float:
        return hypot(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Spot:
    """Une position calibrée et le réglage voulu à cet endroit."""

    name: str
    at: Point
    left_db: int
    right_db: int


@dataclass
class Calibration:
    spots: dict[str, Spot] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return bool(self.spots)

    def distance(self, p: Point) -> float:
        return min((s.at.dist(p) for s in self.spots.values()), default=float("inf"))

    def interpolate(self, p: Point) -> tuple[float, float]:
        """Réglage (G, D) en dB, non arrondi, au point `p`."""
        weights = []
        for s in self.spots.values():
            d = s.at.dist(p)
            if d <= EXACT_CM:
                return float(s.left_db), float(s.right_db)
            weights.append((1 / d**POWER, s))
        total = sum(w for w, _ in weights)
        return (
            sum(w * s.left_db for w, s in weights) / total,
            sum(w * s.right_db for w, s in weights) / total,
        )

    def to_list(self) -> list[dict]:
        return [
            {"name": s.name, "x": s.at.x, "y": s.at.y, "left_db": s.left_db, "right_db": s.right_db}
            for s in self.spots.values()
        ]

    @classmethod
    def from_list(cls, items: list[dict] | None) -> Calibration:
        spots = {}
        for d in items or []:
            spots[d["name"]] = Spot(
                d["name"], Point(float(d["x"]), float(d["y"])), int(d["left_db"]), int(d["right_db"])
            )
        return cls(spots)


@dataclass
class Target:
    id: int
    x: float
    y: float


@dataclass
class SeatTracker:
    """Suit une seule personne dans la zone calibrée et en déduit des réglages stables."""

    calibration: Calibration

    locked_id: int | None = None
    position: Point | None = None  # position lissée de la personne suivie
    applied: tuple[int, int] = (0, 0)
    in_zone: int = 0
    _samples: list[tuple[float, Point]] = field(default_factory=list)
    _candidate: tuple[int, int] | None = None
    _candidate_since: float = 0.0
    _last_seen: float | None = None

    def update(self, targets: list[Target], now: float) -> tuple[int, int]:
        """Nouvelle liste de cibles (vide si personne) ; renvoie le réglage à appliquer."""
        if not self.calibration.ready:
            self.position = None
            return self._settle((0, 0), now, force=True)

        zone = {
            t.id: Point(t.x, t.y)
            for t in targets
            if self.calibration.distance(Point(t.x, t.y)) <= ZONE_CM
        }
        self.in_zone = len(zone)

        # Les autres personnes (de passage ou assises) sont ignorées : on garde celle suivie.
        if self.locked_id not in zone:
            self.locked_id = next(iter(zone), None)
            self._samples.clear()

        if self.locked_id is None:
            if self._last_seen is not None and now - self._last_seen >= ABSENT_S:
                self.position = None
                return self._settle((0, 0), now, force=True)
            return self.applied

        self._last_seen = now
        self._samples.append((now, zone[self.locked_id]))
        self._samples = [s for s in self._samples if now - s[0] <= WINDOW_S]
        self.position = Point(
            median(p.x for _, p in self._samples), median(p.y for _, p in self._samples)
        )
        left, right = self.calibration.interpolate(self.position)
        return self._settle((round(left), round(right)), now)

    def tick(self, now: float) -> tuple[int, int]:
        """À appeler régulièrement : le radar ne publie rien tant que personne ne bouge."""
        if self.locked_id is None:
            return self.update([], now)
        return self._settle(self._candidate or self.applied, now)

    def _settle(self, wanted: tuple[int, int], now: float, force: bool = False) -> tuple[int, int]:
        if wanted == self.applied or force:
            self.applied = wanted
            self._candidate = None
        elif wanted != self._candidate:
            self._candidate = wanted
            self._candidate_since = now
        elif now - self._candidate_since >= DWELL_S:
            self.applied = wanted
            self._candidate = None
        return self.applied
