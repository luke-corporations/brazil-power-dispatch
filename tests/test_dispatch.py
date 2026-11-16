import pytest

from dispatch import Plant, apply_scenario, solve

from .conftest import tiny_system

TOL = 1e-4


# ---- Small cases we can check by hand -----------------------------------------------------


def test_cheapest_plant_runs_first_and_sets_the_price():
    result = solve(tiny_system())
    assert result.dispatch.loc[0, "cheap"] == pytest.approx(40)
    assert result.dispatch.loc[0, "expensive"] == pytest.approx(0)
    assert result.dispatch.loc[1, "cheap"] == pytest.approx(50)  # full
    assert result.dispatch.loc[1, "expensive"] == pytest.approx(30)
    assert list(result.prices) == [10, 30]  # price = cost of the last plant needed


def test_shortage_is_priced_at_value_of_lost_load():
    result = solve(tiny_system(demand_mw=(200.0,)))  # only 150 MW available
    assert result.unserved_mwh == pytest.approx(50)
    assert result.prices[0] == pytest.approx(1000)


def test_limited_water_is_worth_the_cost_of_the_gas_it_replaces():
    hydro = Plant(name="hydro", technology="hydro", capacity_mw=100, variable_cost=0)
    gas = Plant(name="gas", technology="gas", capacity_mw=100, variable_cost=50)
    result = solve(tiny_system(plants=(hydro, gas), demand_mw=(80.0, 80.0), hydro_budget_mwh=100))
    assert result.dispatch["hydro"].sum() == pytest.approx(100)  # all the water is used
    assert result.water_value == pytest.approx(50)
    assert list(result.prices) == [50, 50]


def test_ramp_limit_spreads_changes_over_hours():
    slow = Plant(name="slow", technology="gas", capacity_mw=100, variable_cost=10, ramp_mw_per_hour=20)
    fast = Plant(name="fast", technology="gas", capacity_mw=100, variable_cost=90)
    result = solve(tiny_system(plants=(slow, fast), demand_mw=(10.0, 80.0)))
    assert result.dispatch.loc[1, "slow"] == pytest.approx(30)  # 10 + 20, not 80
    assert result.dispatch.loc[1, "fast"] == pytest.approx(50)


def test_carbon_price_raises_cost_and_prices():
    without = solve(tiny_system())
    with_carbon = solve(tiny_system(carbon_price=100))
    assert with_carbon.operating_cost > without.operating_cost
    assert list(with_carbon.prices) == [10 + 100 * 0.4, 30 + 100 * 0.6]


# ---- The full system: physical rules must always hold --------------------------------------


@pytest.fixture(scope="module")
def normal(system, scenarios):
    return apply_scenario(system, scenarios["normal"]), solve(apply_scenario(system, scenarios["normal"]))


def test_demand_is_met_every_hour(normal):
    sys_, result = normal
    supplied = result.dispatch.sum(axis=1)
    assert (abs(supplied - result.demand) < TOL).all()
    assert result.unserved_mwh == pytest.approx(0)


def test_no_plant_exceeds_available_capacity(normal):
    sys_, result = normal
    for plant in sys_.plants:
        for hour in sys_.hours:
            limit = plant.capacity_mw * sys_.availability_of(plant.technology, hour)
            assert result.dispatch.loc[hour, plant.name] <= limit + TOL


def test_water_budget_and_ecological_flow_are_respected(normal):
    sys_, result = normal
    assert result.dispatch["hydro"].sum() <= sys_.hydro_budget_mwh + TOL
    assert (result.dispatch["hydro"] >= 1000 - TOL).all()


def test_ramp_limits_are_respected(normal):
    sys_, result = normal
    for plant in sys_.plants:
        if plant.ramp_mw_per_hour:
            assert (result.dispatch[plant.name].diff().abs().dropna() <= plant.ramp_mw_per_hour + TOL).all()


# ---- Scenarios: the economics should make sense ---------------------------------------------


def test_every_scenario_solves(system, scenarios):
    for scenario in scenarios.values():
        result = solve(apply_scenario(system, scenario))
        assert result.prices.notna().all()


def test_drought_raises_prices_and_the_value_of_water(system, scenarios):
    normal = solve(apply_scenario(system, scenarios["normal"]))
    dry = solve(apply_scenario(system, scenarios["dry_year"]))
    assert dry.average_price > normal.average_price
    assert dry.water_value > normal.water_value


def test_more_solar_pushes_midday_prices_down(system, scenarios):
    normal = solve(apply_scenario(system, scenarios["normal"]))
    sunny = solve(apply_scenario(system, scenarios["more_solar"]))
    assert sunny.prices[12] < normal.prices[12]
    assert sunny.emissions_t < normal.emissions_t
