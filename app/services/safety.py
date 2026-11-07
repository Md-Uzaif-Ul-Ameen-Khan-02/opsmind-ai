"""Human-in-the-loop safety guardrails.

OpsMind never executes a command. Suggested commands are also filtered:
anything that could mutate production is withheld from the response and the
action is downgraded to ``requires_approval``.
"""

from __future__ import annotations

import re

from ..schemas import RecommendedAction

# Any match => never surfaced as a copy-paste command.
DESTRUCTIVE_PATTERNS = (
    r"\brm\s+-[a-z]*r",
    r"\brm\s+-[a-z]*f",
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bkill\s+-9\b",
    r"\bpkill\b",
    r"\bkillall\b",
    r"\bsystemctl\s+(restart|stop|start|disable)",
    r"\bservice\s+\S+\s+(restart|stop|start)",
    r"\bkubectl\s+(delete|drain|cordon|scale|rollout\s+restart|apply|patch|replace)",
    r"\bdocker\s+(rm|kill|stop|restart|prune)",
    r"\bhelm\s+(uninstall|upgrade|rollback)",
    r"\bterraform\s+(apply|destroy|import|taint)",
    r"\b(ansible-playbook|pulumi\s+up)\b",
    r"\bdrop\s+(table|database|schema)\b",
    r"\btruncate\s+table\b",
    r"\bdelete\s+from\b",
    r"\bupdate\s+\w+\s+set\b",
    r"\binsert\s+into\b",
    r"\balter\s+(table|database|system)\b",
    r"\bgrant\b",
    r"\brevoke\b",
    r"\bflushall\b",
    r"\bflushdb\b",
    r"\biptables\s+-f\b",
    r"\baws\s+\S+\s+(delete|terminate|stop|reboot)",
    r"\bgcloud\s+\S+\s+(delete|stop)",
    r"\baz\s+\S+\s+delete",
    r"\bsudo\b",
    r"\bchmod\s+777\b",
    r">\s*/etc/",
)

# Diagnostic-only prefixes we are willing to present as copyable commands.
READ_ONLY_PREFIXES = (
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
    "kubectl top",
    "kubectl events",
    "kubectl explain",
    "cat ",
    "tail ",
    "head ",
    "grep ",
    "less ",
    "journalctl ",
    "systemctl status",
    "df ",
    "du ",
    "free ",
    "uptime",
    "top ",
    "htop",
    "ps ",
    "ss ",
    "netstat ",
    "lsof ",
    "ping ",
    "dig ",
    "nslookup ",
    "traceroute ",
    "curl -s",
    "curl --silent",
    "curl -I",
    "curl --head",
    "openssl s_client",
    "openssl x509",
    "pg_isready",
    "psql -c \"select",
    "psql -c 'select",
    "psql -c \"show",
    "redis-cli info",
    "redis-cli --latency",
    "redis-cli slowlog",
    "redis-cli client list",
    "sqlite3 ",
    "git log",
    "git status",
    "pip show",
    "node --version",
    "python --version",
)

DESTRUCTIVE_RE = re.compile("|".join(f"(?:{p})" for p in DESTRUCTIVE_PATTERNS), re.IGNORECASE)


def command_is_safe(command: str) -> bool:
    stripped = command.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if DESTRUCTIVE_RE.search(lowered):
        return False
    return any(lowered.startswith(prefix) for prefix in READ_ONLY_PREFIXES)


def sanitize_action(action: RecommendedAction) -> RecommendedAction:
    """Strip unsafe commands; downgrade risk when a mutation was suggested."""
    step = action.step.strip()
    rationale = action.rationale.strip()
    if action.command:
        if command_is_safe(action.command):
            return RecommendedAction(
                step=step,
                rationale=rationale,
                risk="read_only" if action.risk == "read_only" else action.risk,
                command=action.command.strip(),
            )
        rationale = (
            f"{rationale} [OpsMind withheld the suggested command because it may change "
            "production state; remediation requires human approval and a change process.]"
        ).strip()
        return RecommendedAction(
            step=step, rationale=rationale, risk="requires_approval", command=None
        )
    return RecommendedAction(step=step, rationale=rationale, risk=action.risk, command=None)


def safety_notice() -> str:
    return (
        "OpsMind never executes commands or changes infrastructure. Every remediation "
        "requires an engineer's approval."
    )
