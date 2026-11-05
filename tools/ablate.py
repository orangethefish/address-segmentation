"""Reproduce recovery feature ablations on an identical reviewed case set.

Run with --timing to include the existing five-process benchmark for every variant.
Reports include case-level gains/regressions, false positives, recall and budgets.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from tools.evaluate import run_evaluation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dictionary', required=True)
    parser.add_argument('--cases', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--timing', action='store_true')
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    variants = {'exact': (False, False, False), 'stage_d': (True, False, False),
                'compact': (True, True, False), 'typing': (True, False, True),
                'stage_e': (True, True, True)}
    summary = {}
    for name, flags in variants.items():
        config = output / f'{name}-config.json'
        config.write_text(json.dumps(dict(zip(('enable_fuzzy', 'enable_compact', 'enable_typing'), flags))) + '\n')
        report = output / f'{name}.json'
        arguments = ['--dictionary', args.dictionary, '--cases', args.cases,
                     '--config', str(config), '--output', str(report)]
        if name not in ('exact', 'stage_d'):
            arguments += ['--baseline', str(output / 'stage_d.json')]
        if run_evaluation(arguments):
            return 1
        data = json.loads(report.read_text())
        summary[name] = {'metadata': data['metadata'], 'metrics': data['metrics'],
                         'comparison': data.get('baseline_comparison')}
        if args.timing:
            timing = output / f'{name}-timing.json'
            subprocess.run([sys.executable, '-m', 'tools.benchmark', '--dictionary', args.dictionary,
                            '--cases', args.cases, '--config', str(config), '--output', str(timing)], check=True)
            summary[name]['timing'] = json.loads(timing.read_text())
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
