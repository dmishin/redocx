"""Formatting properties: the property registry and validated property sets."""

from wordgenx.props.registry import (
    EMPTY_PARAGRAPH,
    EMPTY_RUN,
    REGISTRY,
    EmitContext,
    PropertySet,
    PropSpec,
    Target,
    UnknownPropertyError,
    lookup,
    para_props,
    run_props,
)

__all__ = [
    "EMPTY_PARAGRAPH",
    "EMPTY_RUN",
    "REGISTRY",
    "EmitContext",
    "PropSpec",
    "PropertySet",
    "Target",
    "UnknownPropertyError",
    "lookup",
    "para_props",
    "run_props",
]
