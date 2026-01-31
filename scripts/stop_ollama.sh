#!/usr/bin/env bash
# Stop Ollama server (runnable via /run stop_ollama)
pkill -f "ollama serve" 2>/dev/null || true
# On macOS/Linux, also try killing by name
pkill -x ollama 2>/dev/null || true
echo "Ollama stopped"
