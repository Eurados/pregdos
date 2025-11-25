FROM opentopas-geant4

ENV DEBIAN_FRONTEND=noninteractive

USER root
RUN apt-get update && \
    apt-get install -y python3 python3-pip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip3 install --no-cache-dir --break-system-packages .

# Create upload dir and give it to `user`
RUN mkdir -p /tmp/pregdos_uploads && \
    chown -R user:user /tmp/pregdos_uploads

# Drop privileges for the app
USER user
WORKDIR /app

EXPOSE 5000

CMD ["python3", "-m", "pregdos.webserver"]