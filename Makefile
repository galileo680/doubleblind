PYTHON ?= python3
TASKS = $(PYTHON) scripts/tasks.py

.PHONY: install lint format test build check-comments smoke-demo golden up down reset probe demo-live demo-record demo-replay

install:
	$(TASKS) install

lint:
	$(TASKS) lint

format:
	$(TASKS) format

test:
	$(TASKS) test

build:
	$(TASKS) build

check-comments:
	$(TASKS) check-comments

smoke-demo:
	$(TASKS) smoke-demo

golden:
	$(TASKS) golden

up:
	$(TASKS) up

down:
	$(TASKS) down

reset:
	$(TASKS) reset

probe:
	$(TASKS) probe

demo-live:
	$(TASKS) demo-live

demo-record:
	$(TASKS) demo-record

demo-replay:
	$(TASKS) demo-replay
