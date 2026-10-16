#!/bin/sh
set -eu

# A named volume replaces the image-owned /app/data directory. Repair only the
# application-data root and map cache, including retained files from older images,
# then run the requested app/scheduler command as the unprivileged service user.
install -d -o ontrack -g ontrack /app/data/track_maps
chown ontrack:ontrack /app/data /app/data/track_maps
find /app/data/track_maps -xdev \( ! -user ontrack -o ! -group ontrack \) \
  -exec chown ontrack:ontrack {} +

exec gosu ontrack "$@"
