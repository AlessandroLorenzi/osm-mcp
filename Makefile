IMAGE ?= osm-mcp
TAG := $(shell git rev-parse --short HEAD)
HOST ?= home

.PHONY: build push-home

build:
	docker build -t $(IMAGE):$(TAG) -t $(IMAGE):latest .

# Nessun registry: l'immagine passa via ssh e viene caricata nel docker di home.
push-home: build
	docker save $(IMAGE):$(TAG) $(IMAGE):latest | gzip | ssh $(HOST) 'gunzip | docker load'
