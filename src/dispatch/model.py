"""The optimisation model: the cheapest way to meet demand, hour by hour.

* Dispatch (LP): decide how much each existing plant produces in each hour.
* Expansion (MIP): also decide how many new units to build, in whole units.

Hourly prices come from the duals of the demand-balance constraint: the extra
cost of serving one more MWh in that hour. That is the marginal price a power
market clears at. The dual of the water constraint is the "water value": what
one more MWh of water in the reservoir would save.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import pandas as pd
import pyomo.environ as pyo

from .data import PowerSystem

log = logging.getLogger(__name__)

SOLVER_NAME = "appsi_highs"  # HiGHS: free, open-source LP/MIP solver
UNSERVED = "unserved"


@dataclass
class Result:
    demand: pd.Series  # MW by hour
    dispatch: pd.DataFrame  # MW by hour (rows) and plant (columns), plus "unserved"
    prices: pd.Series  # R$/MWh by hour
    water_value: float  # R$/MWh
    builds_mw: dict[str, float]  # new capacity by candidate (expansion only)
    operating_cost: float  # R$ per day
    investment_cost: float  # R$ per year (expansion only)
    emissions_t: float  # tonnes of CO2 per day

    @property
    def unserved_mwh(self) -> float:
        return float(self.dispatch[UNSERVED].sum())

    @property
    def average_price(self) -> float:
        """Demand-weighted average price: what consumers pay per MWh on average."""
        return float((self.prices * self.demand).sum() / self.demand.sum())


def build_model(system: PowerSystem, expand: bool = False) -> pyo.ConcreteModel:
    plants = {p.name: p for p in system.plants}
    candidates = {c.name: c for c in system.candidates} if expand else {}

    m = pyo.ConcreteModel(name="power_dispatch")
    m.T = pyo.Set(initialize=list(system.hours), ordered=True)  # hours
    m.G = pyo.Set(initialize=list(plants))  # existing plants
    m.C = pyo.Set(initialize=list(candidates))  # plants we may build

    # ---- Decision variables -------------------------------------------------------------
    m.gen = pyo.Var(m.G, m.T, domain=pyo.NonNegativeReals)  # MW from existing plants
    m.new_gen = pyo.Var(m.C, m.T, domain=pyo.NonNegativeReals)  # MW from new plants
    m.units = pyo.Var(  # how many units to build: a whole number, so this makes it a MIP
        m.C, domain=pyo.NonNegativeIntegers, bounds=lambda m, c: (0, candidates[c].max_units)
    )
    m.unserved = pyo.Var(m.T, domain=pyo.NonNegativeReals)  # MW of demand left unmet

    # ---- Constraints --------------------------------------------------------------------
    # 1. Supply meets demand in every hour. Its dual is the hourly price.
    m.balance = pyo.Constraint(
        m.T,
        rule=lambda m, t: sum(m.gen[g, t] for g in m.G)
        + sum(m.new_gen[c, t] for c in m.C)
        + m.unserved[t]
        == system.demand_mw[t],
    )

    # 2. No plant produces more than its available capacity (solar depends on the sun).
    m.capacity = pyo.Constraint(
        m.G,
        m.T,
        rule=lambda m, g, t: m.gen[g, t]
        <= plants[g].capacity_mw * system.availability_of(plants[g].technology, t),
    )
    m.new_capacity = pyo.Constraint(
        m.C,
        m.T,
        rule=lambda m, c, t: m.new_gen[c, t]
        <= m.units[c] * candidates[c].unit_size_mw * system.availability_of(candidates[c].technology, t),
    )

    # 3. Minimum output, e.g. the ecological flow that must keep running below a dam.
    m.min_output = pyo.Constraint(
        m.G,
        m.T,
        rule=lambda m, g, t: m.gen[g, t] >= plants[g].min_output_mw
        if plants[g].min_output_mw > 0
        else pyo.Constraint.Skip,
    )

    # 4. Ramp limits: thermal plants can't change output too fast from one hour to the next.
    def ramp_rule(m, g, t, direction):
        ramp = plants[g].ramp_mw_per_hour
        if ramp is None or t == m.T.first():
            return pyo.Constraint.Skip
        change = m.gen[g, t] - m.gen[g, m.T.prev(t)]
        return (change if direction == "up" else -change) <= ramp

    m.ramp_up = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: ramp_rule(m, g, t, "up"))
    m.ramp_down = pyo.Constraint(m.G, m.T, rule=lambda m, g, t: ramp_rule(m, g, t, "down"))

    # 5. Hydro plants share the water in the reservoir: they can't use more than today's budget.
    hydro = [g for g, p in plants.items() if p.technology == "hydro"]
    if hydro:
        m.water = pyo.Constraint(
            expr=sum(m.gen[g, t] for g in hydro for t in m.T) <= system.hydro_budget_mwh
        )

    # ---- Objective ----------------------------------------------------------------------
    def cost_per_mwh(variable_cost: float, emissions: float) -> float:
        return variable_cost + system.carbon_price * emissions

    m.operating_cost = pyo.Expression(
        expr=sum(
            cost_per_mwh(plants[g].variable_cost, plants[g].emissions_t_per_mwh) * m.gen[g, t]
            for g in m.G
            for t in m.T
        )
        + sum(
            cost_per_mwh(candidates[c].variable_cost, candidates[c].emissions_t_per_mwh) * m.new_gen[c, t]
            for c in m.C
            for t in m.T
        )
        + system.value_of_lost_load * sum(m.unserved[t] for t in m.T)
    )
    m.investment_cost = pyo.Expression(
        expr=sum(
            candidates[c].annualised_cost_per_mw(system.discount_rate) * candidates[c].unit_size_mw * m.units[c]
            for c in m.C
        )
    )
    # Dispatch minimises the cost of one day. Expansion minimises the cost of one year:
    # yearly investment cost plus the representative day repeated days_per_year times.
    m.days_weight = pyo.Param(initialize=system.days_per_year if expand else 1)
    m.objective = pyo.Objective(
        expr=m.investment_cost + m.days_weight * m.operating_cost, sense=pyo.minimize
    )
    return m


def solve(system: PowerSystem, expand: bool = False) -> Result:
    """Build and solve the model, then return dispatch, prices and costs."""
    m = build_model(system, expand)
    solver = pyo.SolverFactory(SOLVER_NAME)
    start = time.perf_counter()

    if expand and len(m.C) > 0:
        _solve(solver, m)  # 1) MIP: find the best whole number of units to build
        # 2) Prices only exist for linear problems. Fix the build decisions, make them
        #    continuous, and re-solve the remaining LP to read the hourly prices.
        for c in m.C:
            built = round(pyo.value(m.units[c]))
            m.units[c].domain = pyo.NonNegativeReals
            m.units[c].fix(built)

    m.dual = pyo.Suffix(direction=pyo.Suffix.IMPORT)  # ask the solver for the duals
    _solve(solver, m)

    log.info(
        "Solved %s in %.2fs (%d variables, %d constraints)",
        "expansion (MIP)" if expand else "dispatch (LP)",
        time.perf_counter() - start,
        m.nvariables(),
        m.nconstraints(),
    )
    return _extract(system, m)


def _solve(solver, m: pyo.ConcreteModel) -> None:
    results = solver.solve(m)
    condition = results.solver.termination_condition
    if condition != pyo.TerminationCondition.optimal:
        raise RuntimeError(f"Solver did not find an optimal solution: {condition}")


def _extract(system: PowerSystem, m: pyo.ConcreteModel) -> Result:
    hours = list(m.T)
    index = pd.Index(hours, name="hour")
    plants = {p.name: p for p in system.plants}
    candidates = {c.name: c for c in system.candidates}

    columns = {g: [pyo.value(m.gen[g, t]) for t in hours] for g in m.G}
    columns.update({c: [pyo.value(m.new_gen[c, t]) for t in hours] for c in m.C})
    columns[UNSERVED] = [pyo.value(m.unserved[t]) for t in hours]
    dispatch = pd.DataFrame(columns, index=index).clip(lower=0).round(3) + 0.0

    # Duals are "per unit of objective". In expansion the day counts days_weight times,
    # so divide to get R$ per MWh for a single hour.
    weight = pyo.value(m.days_weight)
    prices = pd.Series([m.dual[m.balance[t]] / weight for t in hours], index=index, name="price")
    water_value = -m.dual[m.water] / weight if hasattr(m, "water") else 0.0

    emissions = sum(plants[g].emissions_t_per_mwh * dispatch[g].sum() for g in m.G) + sum(
        candidates[c].emissions_t_per_mwh * dispatch[c].sum() for c in m.C
    )
    return Result(
        demand=pd.Series(system.demand_mw, index=index, name="demand"),
        dispatch=dispatch,
        prices=prices.round(2) + 0.0,  # "+ 0.0" turns -0.0 into 0.0
        water_value=round(water_value, 2) + 0.0,
        builds_mw={c: pyo.value(m.units[c]) * candidates[c].unit_size_mw for c in m.C},
        operating_cost=float(pyo.value(m.operating_cost)),
        investment_cost=float(pyo.value(m.investment_cost)),
        emissions_t=float(emissions),
    )
