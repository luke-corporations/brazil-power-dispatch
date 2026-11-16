"""What-if scenarios: change the base system without editing it."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from .data import PowerSystem


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str = ""
    hydro_budget_factor: float = 1.0  # 0.6 = 40% less water
    demand_factor: float = 1.0  # 1.3 = 30% more demand
    carbon_price: float = 0.0  # R$ per tonne of CO2
    capacity_factor: dict[str, float] = field(default_factory=dict)  # plant name -> multiplier

    def __post_init__(self) -> None:
        if self.hydro_budget_factor < 0 or self.demand_factor < 0 or self.carbon_price < 0:
            raise ValueError(f"scenario '{self.name}': factors and prices must be >= 0")


def load_scenarios(path: Path | str) -> dict[str, Scenario]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {name: Scenario(name=name, **(values or {})) for name, values in raw.items()}


def apply_scenario(system: PowerSystem, scenario: Scenario) -> PowerSystem:
    """Return a new system with the scenario's changes. The original is untouched."""
    unknown = set(scenario.capacity_factor) - {p.name for p in system.plants}
    if unknown:
        raise ValueError(f"scenario '{scenario.name}' refers to unknown plants: {sorted(unknown)}")

    plants = tuple(
        replace(p, capacity_mw=p.capacity_mw * scenario.capacity_factor.get(p.name, 1.0))
        for p in system.plants
    )
    return replace(
        system,
        plants=plants,
        demand_mw=tuple(d * scenario.demand_factor for d in system.demand_mw),
        hydro_budget_mwh=system.hydro_budget_mwh * scenario.hydro_budget_factor,
        carbon_price=scenario.carbon_price,
    )
