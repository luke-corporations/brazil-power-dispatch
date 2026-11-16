from pathlib import Path

import pytest

from dispatch import Plant, PowerSystem, load_scenarios, load_system

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture(scope="session")
def system() -> PowerSystem:
    return load_system(DATA_DIR)


@pytest.fixture(scope="session")
def scenarios():
    return load_scenarios(DATA_DIR / "scenarios.yaml")


def tiny_system(**overrides) -> PowerSystem:
    """A system small enough to solve by hand: two gas plants, two hours.

    Hour 0 needs 40 MW: the cheap plant (50 MW at R$10) covers it, so the price is 10.
    Hour 1 needs 80 MW: cheap is full, the expensive plant (R$30) is marginal, price 30.
    """
    defaults = dict(
        plants=(
            Plant(name="cheap", technology="gas", capacity_mw=50, variable_cost=10, emissions_t_per_mwh=0.4),
            Plant(name="expensive", technology="gas", capacity_mw=100, variable_cost=30, emissions_t_per_mwh=0.6),
        ),
        demand_mw=(40.0, 80.0),
        hydro_budget_mwh=0.0,
        value_of_lost_load=1000.0,
    )
    defaults.update(overrides)
    return PowerSystem(**defaults)
