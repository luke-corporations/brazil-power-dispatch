import pytest

from dispatch import Candidate, Plant, apply_scenario, solve

from .conftest import tiny_system


def candidate(**overrides) -> Candidate:
    defaults = dict(
        name="new_gas",
        technology="gas",
        unit_size_mw=10,
        max_units=10,
        capex_per_mw=1000,
        lifetime_years=10,
        fixed_om_per_mw_year=0,
        variable_cost=10,
    )
    return Candidate(**{**defaults, **overrides})


def old_plant_only() -> dict:
    return dict(plants=(Plant(name="old", technology="gas", capacity_mw=100, variable_cost=100),), demand_mw=(50.0, 50.0))


def test_builds_whole_units_when_it_pays_off():
    result = solve(tiny_system(**old_plant_only(), candidates=(candidate(),)), expand=True)
    # Saves R$90/MWh on 50 MW all day: build exactly 5 units of 10 MW, no more.
    assert result.builds_mw["new_gas"] == pytest.approx(50)
    assert result.dispatch["old"].sum() == pytest.approx(0)


def test_builds_nothing_when_too_expensive():
    result = solve(tiny_system(**old_plant_only(), candidates=(candidate(capex_per_mw=1e12),)), expand=True)
    assert result.builds_mw["new_gas"] == 0


def test_respects_the_maximum_number_of_units():
    result = solve(tiny_system(**old_plant_only(), candidates=(candidate(max_units=2),)), expand=True)
    assert result.builds_mw["new_gas"] == pytest.approx(20)


def test_dispatch_ignores_candidates():
    result = solve(tiny_system(**old_plant_only(), candidates=(candidate(),)))
    assert result.builds_mw == {}
    assert "new_gas" not in result.dispatch.columns


def test_growth_scenario_investment_lowers_total_yearly_cost(system, scenarios):
    future = apply_scenario(system, scenarios["growth_2035"])
    before = solve(future)
    after = solve(future, expand=True)
    yearly_before = before.operating_cost * system.days_per_year
    yearly_after = after.operating_cost * system.days_per_year + after.investment_cost
    assert yearly_after < yearly_before
    assert after.unserved_mwh == pytest.approx(0)
    for name, mw in after.builds_mw.items():
        unit = next(c.unit_size_mw for c in system.candidates if c.name == name)
        assert mw % unit == pytest.approx(0)  # whole units only
