To build for arm64:

# Build the custom container locally

docker build -t cross-custom-udev-image -f Cross.Dockerfile .

# Run your usual cross-compilation command

cross build --target aarch64-unknown-linux-gnu --release
