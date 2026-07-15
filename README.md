# TraceGraph

TraceGraph is an experiment framework for dependency-graph-aware lifecycle
management of tool-using agent traces. It implements the research plan in
`工具调用建图_生命周期压缩调研报告.md`: preserve evidence, unresolved failures,
policy constraints, and auditable side effects while reducing the active LLM
context.

The project deliberately separates three layers:

1. a benchmark-independent trace graph and recoverable archive;
2. interchangeable context managers and reliability metrics;
3. adapters and experiment runners for τ-bench/τ³-bench and saved trajectories.

The core package has no runtime dependencies and works on Python 3.11. The
current τ³-bench integration is optional because upstream requires Python 3.12
and `uv`.

## Development status

The repository is being assembled in verified milestones. The current
milestone contains the typed trace schema, incremental graph, archive store,
and tool-call capture wrapper. Context managers, experiment runners, and
benchmark adapters are added in subsequent milestones.

## Quick verification

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

## Research integrity

Generated smoke-test results are labeled as synthetic. Real benchmark results
are only written after an actual τ-bench/τ³-bench trajectory import or live
run; the framework never substitutes fixture results for benchmark evidence.

