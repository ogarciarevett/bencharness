#!/bin/sh
# Run after registering the pending PyPI publisher and pypi environment reviewer.
set -eu
exec gh workflow run release.yml --repo ogarciarevett/bencharness --ref main -f version=0.1.0
