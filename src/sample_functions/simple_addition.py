#!/usr/bin/env python3
"""
Minimal arithmetic example: adds two numbers from an event payload.

Source: [1] S. Werner, M. Kähler, and A. Hakamian, “Code once, Run Green: Automated Green Code Translation in Serverless Computing,” in 2025 IEEE International Conference on Cloud Engineering (IC2E), Sept. 2025, pp. 105–113. doi: 10.1109/IC2E65552.2025.00026.
Table 1: Basic Function Examples f2 (Basic addition function)
"""

import json
from typing import Any, Dict


def simple_addition(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Add num1 and num2 from the event payload and return a lambda-style response."""
    num1 = event.get("num1", 0)
    num2 = event.get("num2", 0)
    result = num1 + num2
    return {
        "statusCode": 200,
        "body": json.dumps({"result": result}),
    }


def handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Wrapper to mirror serverless handler signatures."""
    return simple_addition(event, context)


if __name__ == "__main__":
    sample_event = {"num1": 2, "num2": 3}
    print(simple_addition(sample_event))

