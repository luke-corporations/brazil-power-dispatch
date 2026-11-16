import pytest

from dispatch import Plant, apply_scenario, capital_recovery_factor

from .conftest import tiny_system


def test_capital_recovery_factor_matches_known_value():
    # 10% over 25 years: about 11% of the investment per year
    assert capital_recovery_factor(0.10, 25) == pytest.approx(0.110168, rel=1e-5)


def test_capital_recovery_factor_with_zero_rate_is_straight_line():
    assert capital_recovery_factor(0.0, 10) == pytest.approx(0.1)


def test_loaded_system_has_a_full_day(system):
    assert len(system.demand_mw) == 24
    assert all(0 <= a <= 1 for a in system.availability["solar"])
    assert {p.name for p in system.plants} >= {"hydro", "solar", "gas_ccgt"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"capacity_mw": -1},
        {"variable_cost": -5},
        {"min_output_mw": 200},  # more than capacity
        {"ramp_mw_per_hour": 0},
    ],
)
def test_invalid_plant_is_rejected(kwargs):
    base = dict(name="p", technology="gas", capacity_mw=100, variable_cost=10)
    with pytest.raises(ValueError):
        Plant(**{**base, **kwargs})


def test_availability_profile_must_cover_every_hour():
    with pytest.raises(ValueError, match="hours"):
        tiny_system(availability={"solar": (0.5,)})


def test_duplicate_plant_names_are_rejected():
    plant = Plant(name="same", technology="gas", capacity_mw=10, variable_cost=1)
    with pytest.raises(ValueError, match="duplicate"):
        tiny_system(plants=(plant, plant))


def test_dry_year_scenario_cuts_the_water_budget(system, scenarios):
    dry = apply_scenario(system, scenarios["dry_year"])
    assert dry.hydro_budget_mwh == pytest.approx(system.hydro_budget_mwh * 0.6)
    assert system.hydro_budget_mwh == 100_000  # the base system is not modified


def test_scenario_with_unknown_plant_is_rejected(system, scenarios):
    from dispatch import Scenario

    with pytest.raises(ValueError, match="unknown plants"):
        apply_scenario(system, Scenario(name="typo", capacity_factor={"soalr": 2.0}))
