#!/bin/bash
echo "Starting Multi-Cloud DR Demo..."
echo "================================"

# Start services
docker-compose up -d
sleep 10

# Install dependencies
pip install psycopg2-binary

# Run demo
python src/dr_orchestrator.py
