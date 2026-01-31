#!/usr/bin/env bash
pkill -f "ollama serve" 2>/dev/null || true
# On Linux, also try killing by name
pkill -x ollama 2>/dev/null || true
echo "Ollama stopped"
