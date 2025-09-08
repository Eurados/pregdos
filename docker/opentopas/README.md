To build:

docker build -t pregdos/topas -f docker/Dockerfile.topas .


To run:


docker run -it --rm \
    --name pregdos-topas \
    -h pregdos-topas \
    -v "$PWD":/work \
    -v pregdos-opt:/opt \
    pregdos/topas