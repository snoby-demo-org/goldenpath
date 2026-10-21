#!/bin/sh
# Regenerate the autotools build system. Runs in the CI toolchain container
# as the `autogen` declared hook in .github/truenorth.yaml.
set -e
autoreconf -fiv
