#!/usr/bin/env python3
"""Test both streaming and non-streaming modes."""

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

from skilllift.llm_client import LLMClient

def test_mode(use_stream: bool, mode_name: str):
    """Test a specific mode."""
    print(f"\n{'='*60}")
    print(f"Testing {mode_name} mode (stream={use_stream})")
    print(f"{'='*60}")

    base_url = os.getenv("GPT_BASE_URL")
    api_key = os.getenv("GPT_API_KEY")
    model = os.getenv("GPT_MODEL")

    if not all([base_url, api_key, model]):
        print(f"❌ Missing environment variables")
        return False

    try:
        client = LLMClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout=600,
            display_name=f"test-{mode_name}",
            role=f"test-{mode_name}",
            use_stream=use_stream
        )
        print(f"✅ Client created: {client.display_name}")
        print(f"   Stream mode: {client.use_stream}")
        print()

        print("📡 Sending request...")
        response = client.call_text(
            system_prompt="You are a helpful assistant.",
            user_prompt="Say 'Hello!' and explain in one sentence what mode you're testing.",
            temperature=0.0
        )

        print(f"✅ Response received:")
        print("-" * 60)
        print(response)
        print("-" * 60)
        print(f"📊 Total tokens: {client.total_tokens}")
        return True

    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("🧪 Testing both HTTP and SSE modes\n")

    # Test non-streaming mode (default)
    result1 = test_mode(use_stream=False, mode_name="HTTP")

    # Test streaming mode
    result2 = test_mode(use_stream=True, mode_name="SSE")

    print(f"\n{'='*60}")
    print("📊 Test Summary:")
    print(f"   HTTP mode: {'✅ PASSED' if result1 else '❌ FAILED'}")
    print(f"   SSE mode:  {'✅ PASSED' if result2 else '❌ FAILED'}")
    print(f"{'='*60}")

    return result1 and result2

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
