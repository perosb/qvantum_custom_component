#!/bin/bash
# Test runner script for Qvantum integration

set -e

echo "Running Qvantum integration tests..."

# Check if we're in the right directory
if [ ! -f "custom_components/qvantum/__init__.py" ]; then
    echo "Error: Must be run from the integration root directory"
    exit 1
fi

# Install test dependencies if needed
if [ ! -d ".venv" ] || ! python3 -c "import homeassistant" 2>/dev/null; then
    echo "Setting up virtual environment..."
    rm -rf .venv
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-test.txt
else
    source .venv/bin/activate
fi

# Run tests
echo "Running pytest..."
PYTHONPATH=/home/perosb/build/qvantum_custom_component:/home/perosb/build/qvantum_custom_component/custom_components \
    python -m pytest -p no:homeassistant_custom_component \
    tests/test_api.py tests/test_init.py tests/test_sensor_working.py tests/test_binary_sensor_working.py tests/test_number_working.py tests/test_climate_working.py tests/test_switch_working.py tests/test_fan_working.py tests/test_services_working.py \
    -v --tb=short --cov=custom_components.qvantum --cov-report=xml

echo "Tests completed!"