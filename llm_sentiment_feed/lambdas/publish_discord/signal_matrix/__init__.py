"""Cross-feed signal matrix — extensible provider architecture."""
from .context import build_context
from .matrix import build_matrix, matrix_to_dict
from .registry import DEFAULT_PROVIDER_IDS, list_providers, resolve_providers
from .types import SignalMatrix, SignalVote

__all__ = [
    "DEFAULT_PROVIDER_IDS",
    "SignalMatrix",
    "SignalVote",
    "build_context",
    "build_matrix",
    "list_providers",
    "matrix_to_dict",
    "resolve_providers",
]
