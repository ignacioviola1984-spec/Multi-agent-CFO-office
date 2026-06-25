"""
cli.py - Thin command-line entrypoint for Nexo.

A convenience dispatcher over the real modules. The canonical entrypoints stay
`python nexo/nexo_orchestrator.py` (full loop) and `streamlit run nexo/app.py`
(approval inbox); this CLI just wires the common operations together.

  python nexo/cli.py gen-data      # (re)generate the synthetic demo cartera
  python nexo/cli.py run           # run the orchestrator over the demo cartera
  python nexo/cli.py eval          # run the guardrail / determinism evals

Imports are lazy (inside each handler) so `--help` works before later phases
add their modules, and so a missing API key never breaks `--help`.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _cmd_gen_data(args):
    from data import generate_synthetic_cartera as gen
    path = gen.main(seed=args.seed)
    print(f"Cartera sintetica generada en: {path}")


def _cmd_run(args):
    import nexo_orchestrator as orch
    orch.run(cartera_path=args.cartera)


def _cmd_eval(args):
    from evals import run_evals
    sys.exit(run_evals.main())


def build_parser():
    p = argparse.ArgumentParser(prog="nexo", description="Nexo - co-piloto del productor de seguros")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("gen-data", help="(re)generar la cartera sintetica de demo")
    g.add_argument("--seed", type=int, default=42, help="semilla deterministica (default 42)")
    g.set_defaults(func=_cmd_gen_data)

    r = sub.add_parser("run", help="correr el orquestador sobre la cartera")
    r.add_argument("--cartera", default=None, help="ruta a un Excel de cartera (default: demo)")
    r.set_defaults(func=_cmd_run)

    e = sub.add_parser("eval", help="correr las evals / guardrails")
    e.set_defaults(func=_cmd_eval)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
