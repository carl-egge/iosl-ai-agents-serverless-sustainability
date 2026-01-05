#!/usr/bin/env python3
"""Entrypoint that re-exports each sample handler for Buildpack discovery."""

from __future__ import annotations

from importlib import import_module
from typing import Any, Callable

__all__ = [
    "api_health_check",
    "carbon_api_call",
    "crypto_key_gen",
    "image_format_converter",
    "simple_addition",
    "video_transcoder",
    "write_to_bucket",
    "available_functions",
]


FunctionCallable = Callable[..., Any]


def _lazy_loader(module_name: str, attr_name: str) -> FunctionCallable:
    """Return a callable that imports the handler the first time it is invoked."""

    cached: FunctionCallable | None = None

    def _caller(*args: Any, **kwargs: Any) -> Any:
        nonlocal cached
        if cached is None:
            module = import_module(module_name)
            cached = getattr(module, attr_name)
        return cached(*args, **kwargs)

    _caller.__name__ = attr_name
    _caller.__doc__ = _caller.__doc__ or f"Lazy proxy for {module_name}.{attr_name}"
    return _caller


api_health_check = _lazy_loader("api_health_check", "api_health_check")
carbon_api_call = _lazy_loader("carbon_api_call", "carbon_api_call")
crypto_key_gen = _lazy_loader("crypto_key_gen", "crypto_key_gen")
image_format_converter = _lazy_loader("image_format_converter", "image_format_converter")
simple_addition = _lazy_loader("simple_addition", "simple_addition")
video_transcoder = _lazy_loader("video_transcoder", "video_transcoder")
write_to_bucket = _lazy_loader("write_to_bucket", "write_to_bucket")

FUNCTION_REGISTRY: dict[str, FunctionCallable] = {
    "api_health_check": api_health_check,
    "carbon_api_call": carbon_api_call,
    "crypto_key_gen": crypto_key_gen,
    "image_format_converter": image_format_converter,
    "simple_addition": simple_addition,
    "video_transcoder": video_transcoder,
    "write_to_bucket": write_to_bucket,
}


def available_functions() -> list[str]:
    """List the handler names that gcloud Run can deploy from this directory."""

    return list(FUNCTION_REGISTRY)
