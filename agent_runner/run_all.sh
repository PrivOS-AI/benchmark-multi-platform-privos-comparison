#!/bin/bash
# Official batch: 5 runs per stack. Real integrations (full + tool-filtered MCP) vs PrivOS skills, plus the
# minimal-tools control. Telegram seed messages must have been posted (as an anonymous group admin) within the last 24h.
cd "$(dirname "$0")"
RUNS=${RUNS:-5}
R="python3 -u runner.py --all-jobs --runs $RUNS --reset-between-runs"
echo "=== Real integrations";  $R --stacks stack_a_mcp,stack_a_mcp_min,stack_c_mcp,stack_c_mcp_min,privos_skill
echo "=== Control: same minimal tools on every side";  $R --stacks stack_a,stack_c,privos
python3 runner.py --reset
