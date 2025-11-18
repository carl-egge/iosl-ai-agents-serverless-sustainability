#!/usr/bin/env python3
"""Test script to list available Gemini models."""

import google.generativeai as genai
import os
import sys

# Configure Gemini
api_key = os.environ.get('GEMINI_API_KEY')
if not api_key and len(sys.argv) > 1:
    api_key = sys.argv[1]
if not api_key:
    print("Error: GEMINI_API_KEY environment variable not set")
    print("Usage: python test_gemini.py [API_KEY]")
    exit(1)

genai.configure(api_key=api_key)

print("Available Gemini models:")
print("=" * 60)

for model in genai.list_models():
    if 'generateContent' in model.supported_generation_methods:
        print(f"Model: {model.name}")
        print(f"  Display name: {model.display_name}")
        print(f"  Description: {model.description}")
        print(f"  Supported methods: {model.supported_generation_methods}")
        print("-" * 60)
