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
    pip install --upgrade pip
    pip install -r requirements-test.txt
else
    source .venv/bin/activate
    # Ensure pymodbus is installed
    if ! python3 -c "import pymodbus" 2>/dev/null; then
        echo "Installing pymodbus..."
        pip install pymodbus>=3.0.0
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