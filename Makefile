.PHONY: setup gateway agent start stop status clean

LOGS_DIR = logs

# Setup everything
setup: setup-gateway setup-agent setup-data
	@echo "Setup complete!"

setup-gateway:
	cd gateway && npm install

setup-agent:
	python3 -m venv .venv && \
	. .venv/bin/activate && \
	pip install -r agent/requirements.txt && \
	playwright install chromium

setup-data:
	mkdir -p data/users data/auth data/schedules data/voices $(LOGS_DIR)

# Run foreground (for development / QR scan)
gateway:
	node gateway/src/index.js

agent:
	.venv/bin/python -m agent.main

# Run background
start: stop setup-data
	@mkdir -p $(LOGS_DIR)
	@echo "Starting gateway..."
	@nohup $(HOME)/.nvm/versions/node/v24.14.0/bin/node gateway/src/index.js > $(LOGS_DIR)/gateway.log 2>&1 & echo $$! > $(LOGS_DIR)/gateway.pid
	@echo "Gateway started (PID $$(cat $(LOGS_DIR)/gateway.pid)), logs: $(LOGS_DIR)/gateway.log"
	@sleep 2
	@echo "Starting agent..."
	@nohup .venv/bin/python -m agent.main > $(LOGS_DIR)/agent.log 2>&1 & echo $$! > $(LOGS_DIR)/agent.pid
	@echo "Agent started (PID $$(cat $(LOGS_DIR)/agent.pid)), logs: $(LOGS_DIR)/agent.log"

restart: stop
	@sleep 2
	@$(MAKE) start

stop:
	@# Kill by PID file
	@if [ -f $(LOGS_DIR)/gateway.pid ]; then \
		kill $$(cat $(LOGS_DIR)/gateway.pid) 2>/dev/null && echo "Gateway stopped (pid)" || true; \
		rm -f $(LOGS_DIR)/gateway.pid; \
	fi
	@if [ -f $(LOGS_DIR)/agent.pid ]; then \
		kill $$(cat $(LOGS_DIR)/agent.pid) 2>/dev/null && echo "Agent stopped (pid)" || true; \
		rm -f $(LOGS_DIR)/agent.pid; \
	fi
	@# Kill any orphan processes
	@-pgrep -f 'python.*agent\.main' | xargs -r kill 2>/dev/null
	@-pgrep -f 'node.*gateway/src/index' | xargs -r kill 2>/dev/null
	@-lsof -t -i:8765 2>/dev/null | xargs -r kill 2>/dev/null
	@sleep 1

status:
	@if [ -f $(LOGS_DIR)/gateway.pid ] && kill -0 $$(cat $(LOGS_DIR)/gateway.pid) 2>/dev/null; then \
		echo "Gateway: running (PID $$(cat $(LOGS_DIR)/gateway.pid))"; \
	else echo "Gateway: stopped"; fi
	@if [ -f $(LOGS_DIR)/agent.pid ] && kill -0 $$(cat $(LOGS_DIR)/agent.pid) 2>/dev/null; then \
		echo "Agent:   running (PID $$(cat $(LOGS_DIR)/agent.pid))"; \
	else echo "Agent:   stopped"; fi

logs-gateway:
	@tail -f $(LOGS_DIR)/gateway.log

logs-agent:
	@tail -f $(LOGS_DIR)/agent.log

# Clean
clean:
	rm -rf gateway/node_modules .venv __pycache__ agent/__pycache__
