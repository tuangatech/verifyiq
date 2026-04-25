# cli/verifyiq_cli/scenarios.py
"""Pre-defined subject payloads for each verification use case."""

import typer

SCENARIOS: dict[str, dict] = {
    "mortgage-intl": {
        "subject_name": "Nguyen Minh Tuan",
        "subject_id": "SIM-VN-2019",
        "use_case": "mortgage",
        "has_foreign_addr": True,
        "consent": True,
    },
    "mortgage-domestic": {
        "subject_name": "Sarah Johnson",
        "subject_id": "SIM-US-1985",
        "use_case": "mortgage",
        "has_foreign_addr": True,
        "consent": True,
    },
    "rental": {
        "subject_name": "Marcus Williams",
        "subject_id": "SIM-US-1992",
        "use_case": "rental",
        "has_foreign_addr": False,
        "consent": True,
    },
    "auto": {
        "subject_name": "Jennifer Chen",
        "subject_id": "SIM-US-1988",
        "use_case": "auto",
        "has_foreign_addr": False,
        "consent": True,
    },
    "hire": {
        "subject_name": "Raj Patel",
        "subject_id": "SIM-IN-2020",
        "use_case": "hire",
        "has_foreign_addr": True,
        "consent": True,
    },
    "mortgage-platform": None,
}


def get_scenario(name: str) -> dict:
    """Return payload for a scenario name, or raise BadParameter with available list."""
    if name not in SCENARIOS:
        available = ", ".join(SCENARIOS.keys())
        raise typer.BadParameter(f"Unknown scenario '{name}'. Available: {available}")
    return SCENARIOS[name]
