# Required because cross rs isnt as stable as beautiful glorious perfect gcc with c++ 

# Replace with your specific cross-rs base image (e.g., gnu or musl variants)
FROM ghcr.io/cross-rs/aarch64-unknown-linux-gnu:edge

# Enable multi-arch package installation inside the container
RUN dpkg --add-architecture arm64 && \
    apt-get update && \
    apt-get install -y \
    pkg-config \
    libudev-dev:arm64
