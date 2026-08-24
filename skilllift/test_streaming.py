#!/usr/bin/env python3
"""Test script for streaming LLM client."""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env file manually
def load_dotenv():
    """Load environment variables from .env file."""
    env_paths = [
        Path(__file__).parent.parent / ".env",
        Path(__file__).parent / ".env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            print(f"📁 Loading environment from: {env_path}")
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key and value:
                            os.environ[key] = value
            return True
    return False

load_dotenv()

from skilllift.llm_client import create_llm_client

def test_streaming():
    """Test streaming with a simple request."""

    # Check if environment variables are set
    required_vars = ["GPT_BASE_URL", "GPT_API_KEY", "GPT_MODEL"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        print(f"❌ Missing environment variables: {', '.join(missing)}")
        print(f"   GPT_BASE_URL: {os.getenv('GPT_BASE_URL', '<not set>')}")
        print(f"   GPT_API_KEY: {'set' if os.getenv('GPT_API_KEY') else '<not set>'}")
        print(f"   GPT_MODEL: {os.getenv('GPT_MODEL', '<not set>')}")
        return False

    print("🔧 Creating LLM client with streaming support...")
    config_path = Path(__file__).parent / "models_config.json"

    try:
        client = create_llm_client(config_path, role="test")
        print(f"✅ Client created: {client.display_name}")
        print(f"   Base URL: {client.base_url}")
        print(f"   Timeout: {client.timeout}s")
        print()

        print("📡 Testing streaming request...")
        print("   System: You are a helpful assistant.")
        print("   User: Say 'Hello, streaming works!' and explain in 2 sentences why streaming is useful.")
        print()

        response = client.call_text(
            system_prompt="You are a helpful assistant.",
            user_prompt="Say 'Hello, streaming works!' and explain in 2 sentences why streaming is useful.",
            temperature=0.0
        )

        print("✅ Streaming response received:")
        print("-" * 60)
        print(response)
        print("-" * 60)
        print()
        print(f"📊 Total tokens used: {client.total_tokens}")
        print(f"📊 Request count: {client.request_count}")

        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_streaming()
    sys.exit(0 if success else 1)
