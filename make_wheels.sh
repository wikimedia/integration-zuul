#!/usr/bin/env bash
# Build wheels for distribution
set -o errexit
set -o nounset
set -o pipefail

BASE=$(realpath $(dirname $0))
BUILD=${BASE}/_build
VENV=${BUILD}/venv
ZUUL=${BASE}
WHEEL_DIR=${BASE}/wheels
REQUIREMENTS=${BASE}/requirements.txt

PIP=${VENV}/bin/pip

mkdir -p $VENV
# Zuul only supports python2 at the moment.
# TODO: convert to python3.
virtualenv --python python2 $VENV || /bin/true
$PIP install -r ${ZUUL}/requirements.txt
$PIP freeze --local --requirement $REQUIREMENTS > $REQUIREMENTS



$PIP install wheel
$PIP wheel --find-links $WHEEL_DIR \
    --wheel-dir $WHEEL_DIR \
    --requirement $REQUIREMENTS
