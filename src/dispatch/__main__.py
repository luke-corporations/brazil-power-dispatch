"""Command line interface.

    python -m dispatch run --scenario dry_year     one scenario, hour by hour
    python -m dispatch compare                     every scenario side by side
    python -m dispatch expand --scenario growth_2035   what should we build?
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from .data import load_system
from .model import Result, solve
from .plots import plot_dispatch, plot_price_comparison
from .scenarios import Scenario, apply_scenario, load_scenarios


def summary(name: str, result: Result) -> dict[str, float | str]:
    return {
        "scenario": name,
        "avg price (R$/MWh)": result.average_price,
        "peak price": result.prices.max(),
        "water value": result.water_value,
        "daily cost (R$ m)": result.operating_cost / 1e6,
        "CO2 (kt/day)": result.emissions_t / 1e3,
        "unserved (MWh)": result.unserved_mwh,
    }


def pick(scenarios: dict[str, Scenario], name: str) -> Scenario:
    if name not in scenarios:
        raise SystemExit(f"Unknown scenario '{name}'. Available: {', '.join(scenarios)}")
    return scenarios[name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dispatch", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="folder with the input files")
    parser.add_argument("--out-dir", type=Path, default=Path("results"), help="where charts are saved")
    parser.add_argument("-v", "--verbose", action="store_true", help="show log messages")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="dispatch one scenario hour by hour")
    run.add_argument("--scenario", default="normal")
    commands.add_parser("compare", help="dispatch every scenario and compare prices")
    expand = commands.add_parser("expand", help="decide what new plants to build (MIP)")
    expand.add_argument("--scenario", default="growth_2035")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("pyomo").setLevel(logging.WARNING)  # keep the solver's own log quiet
    pd.set_option("display.float_format", "{:,.1f}".format)
    pd.set_option("display.width", 140)

    system = load_system(args.data_dir)
    scenarios = load_scenarios(args.data_dir / "scenarios.yaml")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "run":
        scenario = pick(scenarios, args.scenario)
        result = solve(apply_scenario(system, scenario))
        print(f"\n{scenario.name}: {scenario.description}\n")
        print(result.dispatch.join(result.prices).to_string())
        print("\n" + pd.DataFrame([summary(scenario.name, result)]).to_string(index=False))
        chart = plot_dispatch(result, f"Hourly dispatch - {scenario.description}", args.out_dir / f"dispatch_{scenario.name}.png")
        print(f"\nChart saved to {chart}")

    elif args.command == "compare":
        results = {name: solve(apply_scenario(system, s)) for name, s in scenarios.items()}
        print("\n" + pd.DataFrame([summary(n, r) for n, r in results.items()]).to_string(index=False))
        for name, result in results.items():
            plot_dispatch(result, f"Hourly dispatch - {scenarios[name].description}", args.out_dir / f"dispatch_{name}.png")
        chart = plot_price_comparison(results, args.out_dir / "price_comparison.png")
        print(f"\nCharts saved to {args.out_dir} (price comparison: {chart})")

    elif args.command == "expand":
        scenario = pick(scenarios, args.scenario)
        future = apply_scenario(system, scenario)
        before = solve(future)
        after = solve(future, expand=True)
        candidates = {c.name: c for c in system.candidates}
        builds = pd.DataFrame(
            [
                {
                    "candidate": name,
                    "units": round(mw / candidates[name].unit_size_mw),
                    "MW built": mw,
                    "cost per year (R$ m)": mw * candidates[name].annualised_cost_per_mw(system.discount_rate) / 1e6,
                }
                for name, mw in after.builds_mw.items()
            ]
        )
        print(f"\n{scenario.name}: {scenario.description}\n\nWhat to build:")
        print(builds.to_string(index=False))
        yearly_saving = (before.operating_cost - after.operating_cost) * system.days_per_year
        print(
            f"\nInvestment: R$ {after.investment_cost / 1e6:,.0f} m/year | "
            f"operating savings: R$ {yearly_saving / 1e6:,.0f} m/year | "
            f"net benefit: R$ {(yearly_saving - after.investment_cost) / 1e6:,.0f} m/year"
        )
        print("\n" + pd.DataFrame([summary("without new plants", before), summary("with new plants", after)]).to_string(index=False))
        chart = plot_dispatch(after, f"Dispatch after investment - {scenario.description}", args.out_dir / f"expansion_{scenario.name}.png")
        print(f"\nChart saved to {chart}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
