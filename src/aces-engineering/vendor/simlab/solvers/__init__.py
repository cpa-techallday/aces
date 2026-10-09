"""Solver adapters. Each module exposes `Adapter` with:

    inputs(case, spec) -> list[str]            files whose hashes go into the evidence manifest
    run(case, spec, run_dir) -> dict           metrics (numbers) plus 'artifacts': [paths] to log
"""
