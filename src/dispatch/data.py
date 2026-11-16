"""Input data: plants, hourly demand, availability profiles and investment options.

All inputs live in plain files under data/ so they can be reviewed and changed
without touching the model. Everything is validated when it is created, so a typo
in a YAML file fails loudly instead of quietly producing a wrong answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

log = logging.getLogger(__name__)


def capital_recovery_factor(rate: float, years: int) -> float:
    """Share of an upfront investment paid back each year, like a loan instalment.

    CRF = r(1+r)^n / ((1+r)^n - 1). With a zero rate it is simply 1/n.
    """
    if years <= 0:
        raise ValueError(f"years must be positive, got {years}")
    if rate == 0:
        return 1 / years
    growth = (1 + rate) ** years
    return rate * growth / (growth - 1)


@dataclass(frozen=True)
class Plant:
    """An existing power plant."""

    name: str
    technology: str
    capacity_mw: float
    variable_cost: float  # R$/MWh: fuel plus variable operation and maintenance
    emissions_t_per_mwh: float = 0.0
    min_output_mw: float = 0.0  # e.g. minimum ecological flow below a dam
    ramp_mw_per_hour: float | None = None  # max change in output from one hour to the next

    def __post_init__(self) -> None:
        if self.ramp_mw_per_hour is not None and self.ramp_mw_per_hour <= 0:
            raise ValueError(f"{self.name}: ramp_mw_per_hour must be > 0, got {self.ramp_mw_per_hour}")
        if self.capacity_mw < 0:
            raise ValueError(f"{self.name}: capacity_mw must be >= 0, got {self.capacity_mw}")
        if self.variable_cost < 0:
            raise ValueError(f"{self.name}: variable_cost must be >= 0, got {self.variable_cost}")
        if self.emissions_t_per_mwh < 0:
            raise ValueError(f"{self.name}: emissions must be >= 0, got {self.emissions_t_per_mwh}")
        if not 0 <= self.min_output_mw <= self.capacity_mw:
            raise ValueError(
                f"{self.name}: min_output_mw must be between 0 and capacity "
                f"({self.capacity_mw}), got {self.min_output_mw}"
            )


@dataclass(frozen=True)
class Candidate:
    """A type of plant the expansion model may build, in whole units."""

    name: str
    technology: str
    unit_size_mw: float
    max_units: int
    capex_per_mw: float  # R$ per MW, paid once
    lifetime_years: int
    fixed_om_per_mw_year: float  # R$ per MW per year, paid whether it runs or not
    variable_cost: float  # R$/MWh
    emissions_t_per_mwh: float = 0.0

    def __post_init__(self) -> None:
        if self.unit_size_mw <= 0:
            raise ValueError(f"{self.name}: unit_size_mw must be > 0, got {self.unit_size_mw}")
        if self.max_units < 0:
            raise ValueError(f"{self.name}: max_units must be >= 0, got {self.max_units}")
        if self.capex_per_mw < 0 or self.fixed_om_per_mw_year < 0 or self.variable_cost < 0:
            raise ValueError(f"{self.name}: costs must be >= 0")
        if self.lifetime_years <= 0:
            raise ValueError(f"{self.name}: lifetime_years must be > 0, got {self.lifetime_years}")

    def annualised_cost_per_mw(self, discount_rate: float) -> float:
        """Yearly cost of owning 1 MW: capex spread over the lifetime, plus fixed O&M."""
        crf = capital_recovery_factor(discount_rate, self.lifetime_years)
        return self.capex_per_mw * crf + self.fixed_om_per_mw_year


@dataclass(frozen=True)
class PowerSystem:
    """Everything the model needs to know about one representative day."""

    plants: tuple[Plant, ...]
    demand_mw: tuple[float, ...]
    hydro_budget_mwh: float
    value_of_lost_load: float  # R$/MWh penalty for unserved demand
    availability: dict[str, tuple[float, ...]] = field(default_factory=dict)  # by technology
    candidates: tuple[Candidate, ...] = ()
    carbon_price: float = 0.0  # R$ per tonne of CO2
    discount_rate: float = 0.10
    days_per_year: int = 365

    def __post_init__(self) -> None:
        if not self.demand_mw:
            raise ValueError("demand_mw must have at least one hour")
        if any(d < 0 for d in self.demand_mw):
            raise ValueError("demand_mw values must be >= 0")
        names = [p.name for p in self.plants] + [c.name for c in self.candidates]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate plant names: {sorted(duplicates)}")
        for technology, profile in self.availability.items():
            if len(profile) != len(self.demand_mw):
                raise ValueError(
                    f"availability profile for '{technology}' has {len(profile)} hours, "
                    f"demand has {len(self.demand_mw)}"
                )
            if any(not 0 <= a <= 1 for a in profile):
                raise ValueError(f"availability for '{technology}' must be between 0 and 1")
        if self.hydro_budget_mwh < 0:
            raise ValueError("hydro_budget_mwh must be >= 0")
        if self.value_of_lost_load <= 0:
            raise ValueError("value_of_lost_load must be > 0")

    @property
    def hours(self) -> range:
        return range(len(self.demand_mw))

    def availability_of(self, technology: str, hour: int) -> float:
        """Share of capacity usable in that hour (1.0 when there is no profile)."""
        profile = self.availability.get(technology)
        return 1.0 if profile is None else profile[hour]


def load_system(data_dir: Path | str = "data") -> PowerSystem:
    """Read system.yaml and hourly_profiles.csv from data_dir."""
    data_dir = Path(data_dir)
    config = yaml.safe_load((data_dir / "system.yaml").read_text(encoding="utf-8"))
    profiles = pd.read_csv(data_dir / "hourly_profiles.csv").sort_values("hour")

    availability = {
        column.removesuffix("_availability"): tuple(profiles[column].astype(float))
        for column in profiles.columns
        if column.endswith("_availability")
    }
    system = PowerSystem(
        plants=tuple(Plant(**p) for p in config["plants"]),
        demand_mw=tuple(profiles["demand_mw"].astype(float)),
        hydro_budget_mwh=float(config["hydro"]["daily_energy_budget_mwh"]),
        value_of_lost_load=float(config["value_of_lost_load"]),
        availability=availability,
        candidates=tuple(Candidate(**c) for c in config.get("candidates", [])),
        discount_rate=float(config.get("discount_rate", 0.10)),
    )
    log.info(
        "Loaded %d plants, %d candidates, %d hours from %s",
        len(system.plants), len(system.candidates), len(system.demand_mw), data_dir,
    )
    return system
