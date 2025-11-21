# Qvantum Home Assistant Integration - Tests

This directory contains unit tests for the Qvantum heat pump integration.

## Running Tests

### Prerequisites

Install test dependencies:
```bash
pip install -r requirements-test.txt
```

### Run Tests

Run all tests:
```bash
pytest
```

Run with coverage:
```bash
pytest --cov=custom_components.qvantum
```

Run specific test file:
```bash
pytest tests/test_api.py
```

### Test Structure

- `conftest.py` - Pytest fixtures and configuration
- `test_api.py` - Tests for the QvantumAPI class
- `test_sensor.py` - Tests for sensor entities
- `test_binary_sensor.py` - Tests for binary sensor entities
- `test_init.py` - Tests for integration setup/unload

### Test Coverage

The tests aim to cover:
- API authentication and data fetching
- Sensor entity creation and state management
- Binary sensor functionality
- Integration setup and teardown
- Error handling scenarios

### Mocking

Tests use extensive mocking to avoid external dependencies:
- Home Assistant core components
- HTTP requests (aiohttp)
- Device and entity registries
- Coordinator functionality

This allows tests to run quickly and reliably without requiring:
- Actual Qvantum API access
- Home Assistant runtime
- Network connectivity