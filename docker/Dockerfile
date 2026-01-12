FROM 3dgi/geoflow-bundle-builder:2026.01.06 AS builder

# Download Dutch transformation grids
RUN wget https://cdn.proj.org/nl_nsgi_nlgeo2018.tif -O /usr/local/share/proj/nl_nsgi_nlgeo2018.tif && \
    wget https://cdn.proj.org/nl_nsgi_rdtrans2018.tif -O /usr/local/share/proj/nl_nsgi_rdtrans2018.tif

# Install system dependencies with cleanup in single layer
# Install helper packages, add UbuntuGIS PPA (provides newer GIS packages including PDAL),
# then install PDAL and development headers. Clean apt lists to reduce image size.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        software-properties-common dirmngr gnupg ca-certificates curl libffi-dev \
        python3-dev build-essential cmake

RUN add-apt-repository -y ppa:ubuntugis/ubuntugis-unstable && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        libgdal-dev \
        libgeotiff-dev \
        liblaszip-dev \
        libproj-dev \
        libxml2-dev \
        libzstd-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Build PDAL from source 
RUN git clone --branch 2.8-maintenance --depth 1 https://github.com/PDAL/PDAL.git && \
    cd PDAL && \
    mkdir build && \
    cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release && \
    make -j$(nproc) && \
    make install && \
    cd ../.. && \
    rm -rf PDAL

ENV PATH="/app/.venv/bin:$PATH"
ENV GF_PLUGIN_FOLDER="/usr/local/lib/geoflow-plugins"
ENV TYLER_RESOURCES_DIR="/app/assets"

# Install Binaries and strip them
COPY docker/binaries/roofer /usr/bin/roofer
COPY docker/binaries/cjseq /usr/bin/cjseq
COPY docker/binaries/reconstruct /usr/bin/reconstruct
COPY docker/binaries/tyler /usr/bin/tyler

# Strip debug symbols from binaries to reduce size
RUN strip /usr/bin/roofer /usr/bin/cjseq /usr/bin/reconstruct /usr/bin/tyler && \
    ldconfig

# Install python environment
RUN curl -LsSf https://astral.sh/uv/install.sh | /bin/sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app
COPY src /app/.
COPY docker/tyler /app/assets

COPY pyproject.toml /app
COPY mypy.ini /app
COPY uv.lock /app
COPY docker/metadata.city.json /metadata.city.json
COPY docker/binaries/proj.db /usr/share/proj/proj.db
COPY docker/ahn.json /ahn.json

# Install rust
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y

# Add Rust to PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# Install packages
RUN uv sync

# Install Node.js and npm packages with cleanup
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g 3d-tiles-tools && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    rm -rf /tmp/* && \
    rm -rf /var/tmp/* && \
    rm -rf /root/.npm && \
    npm cache clean --force

# Final cleanup - remove unnecessary files
RUN find /app -name "*.pyc" -delete && \
    find /app -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true && \
    rm -rf /root/.cache
