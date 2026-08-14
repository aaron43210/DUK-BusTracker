FROM gis-ops/docker-valhalla/valhalla:latest

# Copy the custom Trivandrum PBF file directly into the image
COPY custom_files/trivandrum_patched.osm.pbf /custom_files/trivandrum_patched.osm.pbf

# Set environment variables to force Valhalla to build the tiles on startup
# if they don't exist in the image yet.
ENV use_tiles_ignore_pbf=True
ENV force_rebuild=False
ENV build_elevation=False
ENV build_admins=False
ENV build_timezones=False

# The entrypoint script provided by the gis-ops/docker-valhalla image
# will automatically detect the PBF file in /custom_files and build the graph.
