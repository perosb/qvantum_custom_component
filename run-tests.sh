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
if [ ! -d ".venv" ]; then
    echo "Setting up virtual environment..."
    python3 -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements-test.txt
else
    # shellcheck disable=SC1091
    source .venv/bin/activate
    if ! python -c "import homeassistant, modbus_connection" 2>/dev/null; then
        echo "Installing test dependencies..."
        pip install -r requirements-test.txt
    fi
fi

# Run tests
echo "Running pytest..."
PROJECT_ROOT=$(pwd)
PYTHONPATH=${PROJECT_ROOT}:${PROJECT_ROOT}/custom_components \
    python -m pytest \
    tests/ \
    -v --tb=short --cov=custom_components.qvantum --cov-report=xml \
    -W ignore::DeprecationWarning -W ignore::PendingDeprecationWarning

echo "Tests completed!"