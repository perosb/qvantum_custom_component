#!/usr/bin/env python3
"""Simple test runner for Qvantum integration."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

def test_api():
    """Test the QvantumAPI class."""
    print("Testing QvantumAPI...")

    try:
        # Mock aiohttp before importing
        import sys
        from unittest.mock import MagicMock, AsyncMock
        aiohttp_mock = MagicMock()
        aiohttp_mock.ClientSession = MagicMock(return_value=AsyncMock())
        sys.modules['aiohttp'] = aiohttp_mock

        # Mock the const module
        const_mock = type('Mock', (), {})()
        const_mock.DOMAIN = "qvantum"
        const_mock.FAN_SPEED_STATE_OFF = "off"
        const_mock.FAN_SPEED_STATE_NORMAL = "normal"
        const_mock.FAN_SPEED_STATE_EXTRA = "extra"
        const_mock.FAN_SPEED_VALUE_OFF = 0
        const_mock.FAN_SPEED_VALUE_NORMAL = 1
        const_mock.FAN_SPEED_VALUE_EXTRA = 2
        const_mock.DEFAULT_ENABLED_METRICS = ["bt1", "bt2"]
        const_mock.DEFAULT_DISABLED_METRICS = ["bt3", "bt4"]
        original_const = sys.modules.get("custom_components.qvantum.const")
        sys.modules['custom_components.qvantum.const'] = const_mock

        # Import the API
        from custom_components.qvantum.api import QvantumAPI

        # Test initialization
        api = QvantumAPI("test@example.com", "password", "test-agent")
        assert api._username == "test@example.com"
        assert api._password == "password"
        assert api._user_agent == "test-agent"
        assert api.hass is None

        print("✓ QvantumAPI initialization test passed")
        return True

    except Exception as e:
        print(f"✗ QvantumAPI test failed: {e}")
        import traceback

        traceback.print_exc()
        return False

    finally:
        # Clean up sys.modules
        if "custom_components.qvantum.const" in sys.modules:
            if original_const is not None:
                sys.modules["custom_components.qvantum.const"] = original_const
            else:
                del sys.modules["custom_components.qvantum.const"]

def test_syntax():
    """Test that all Python files compile correctly."""
    print("Testing syntax of all Python files...")

    import glob
    import py_compile

    python_files = glob.glob("custom_components/**/*.py", recursive=True)
    python_files.extend(glob.glob("tests/*.py"))

    failed = []
    for file in python_files:
        try:
            py_compile.compile(file, doraise=True)
        except Exception as e:
            failed.append((file, str(e)))

    if failed:
        print("✗ Syntax errors found:")
        for file, error in failed:
            print(f"  {file}: {error}")
        return False
    else:
        print(f"✓ All {len(python_files)} Python files compile successfully")
        return True

def main():
    """Run all tests."""
    print("Running Qvantum Integration Tests")
    print("=" * 40)

    results = []
    results.append(test_syntax())
    results.append(test_api())

    print("\n" + "=" * 40)
    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"✓ All {total} test suites passed!")
        return 0
    else:
        print(f"✗ {total - passed} of {total} test suites failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())