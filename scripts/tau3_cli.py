"""Register the TraceGraph agent and delegate to the upstream τ³ CLI."""

from tracegraph.integrations.tau3_agent import register_tau3_agent

register_tau3_agent()

from tau2.cli import main  # noqa: E402

main()
