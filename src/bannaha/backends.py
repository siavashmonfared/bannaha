"""Shared backend details: endpoints, Bedrock credentials, optional packages.

Backends are not classes of their own: each harness knows how to reach a
model through the backends it supports. This module holds what they share.
"""
from __future__ import annotations

import importlib.util
import os

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
BACKENDS = {
    "direct": "the model vendor's own API",
    "openrouter": "OpenRouter (one key, many vendors' models)",
    "bedrock": "Amazon Bedrock (AWS credentials and region)",
    "local": "runs locally, no network (mock harness)",
}


def aws_region() -> str | None:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")


def bedrock_problems() -> list[str]:
    problems = []
    if not aws_region():
        problems.append("bedrock needs AWS_REGION (or AWS_DEFAULT_REGION)")
    if importlib.util.find_spec("boto3") is None:
        problems.append("bedrock needs boto3: install 'bannaha[bedrock]'")
        return problems
    import boto3

    try:
        if boto3.Session().get_credentials() is None:
            problems.append("bedrock found no AWS credentials (set AWS_PROFILE, or AWS access keys, or log in "
                            "with `aws sso login`)")
    except Exception as exc:  # botocore raises many profile/config errors
        problems.append(f"bedrock credentials could not be loaded: {exc}")
    return problems


def litellm_problems(harness: str, backend: str) -> list[str]:
    if importlib.util.find_spec("litellm") is None:
        return [f"{harness}@{backend} goes through LiteLLM: install 'bannaha[litellm]'"]
    return []
