from __future__ import annotations

from copy import deepcopy
from uuid import uuid4


_EXPERIMENTS: dict[str, dict] = {}


def create_experiment(state: dict) -> str:
    experiment_id = uuid4().hex
    _EXPERIMENTS[experiment_id] = deepcopy(state)
    return experiment_id


def get_experiment(experiment_id: str) -> dict:
    return deepcopy(_EXPERIMENTS[experiment_id])


def update_experiment(experiment_id: str, state: dict) -> None:
    _EXPERIMENTS[experiment_id] = deepcopy(state)
