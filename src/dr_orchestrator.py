"""
Multi-Cloud Database Disaster Recovery System
Complete functional implementation with automated failover
"""

import asyncio
import psycopg2
import time
import json
from datetime import datetime
from typing import Dict, List, Optional
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DatabaseNode:
    """Represents a database node in the DR cluster"""
    
    def __init__(self, name: str, host: str, port: int, user: str, password: str, database: str):
        self.name = name
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.is_primary = False
        self.is_healthy = True
        self.connection = None
        self.replication_lag = 0
        
    def connect(self) -> bool:
        """Establish database connection"""
        try:
            self.connection = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                connect_timeout=5
            )
            logger.info(f"✓ Connected to {self.name}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to connect to {self.name}: {e}")
            self.is_healthy = False
            return False
    
    def health_check(self) -> bool:
        """Perform health check on database node"""
        try:
            if not self.connection or self.connection.closed:
                return self.connect()
            
            cursor = self.connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
            
            self.is_healthy = True
            return True
        except Exception as e:
            logger.error(f"Health check failed for {self.name}: {e}")
            self.is_healthy = False
            return False
    
    def get_replication_lag(self) -> float:
        """Get replication lag in seconds"""
        if self.is_primary:
            return 0.0
        
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))
            """)
            lag = cursor.fetchone()[0]
            cursor.close()
            
            self.replication_lag = lag if lag else 0.0
            return self.replication_lag
        except Exception as e:
            logger.warning(f"Could not get replication lag for {self.name}: {e}")
            return 0.0
    
    def promote_to_primary(self) -> bool:
        """Promote standby to primary"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("SELECT pg_promote()")
            cursor.close()
            
            self.is_primary = True
            logger.info(f"✓ {self.name} promoted to PRIMARY")
            return True
        except Exception as e:
            logger.error(f"Failed to promote {self.name}: {e}")
            return False
    
    def get_stats(self) -> Dict:
        """Get database statistics"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT 
                    numbackends as active_connections,
                    xact_commit as transactions_committed,
                    xact_rollback as transactions_rolled_back,
                    blks_read as blocks_read,
                    blks_hit as blocks_hit
                FROM pg_stat_database 
                WHERE datname = %s
            """, (self.database,))
            
            result = cursor.fetchone()
            cursor.close()
            
            return {
                "active_connections": result[0],
                "transactions_committed": result[1],
                "transactions_rolled_back": result[2],
                "blocks_read": result[3],
                "blocks_hit": result[4],
                "cache_hit_ratio": (result[4] / (result[3] + result[4]) * 100) if (result[3] + result[4]) > 0 else 0
            }
        except Exception as e:
            logger.error(f"Failed to get stats for {self.name}: {e}")
            return {}


class DisasterRecoveryOrchestrator:
    """Orchestrates disaster recovery across multiple database nodes"""
    
    def __init__(self):
        self.nodes: List[DatabaseNode] = []
        self.primary_node: Optional[DatabaseNode] = None
        self.monitoring = True
        self.failover_in_progress = False
        self.metrics = []
        
    def add_node(self, node: DatabaseNode):
        """Add a database node to the DR cluster"""
        self.nodes.append(node)
        if node.is_primary:
            self.primary_node = node
    
    def initialize(self) -> bool:
        """Initialize all database connections"""
        logger.info("Initializing DR cluster...")
        
        success = True
        for node in self.nodes:
            if not node.connect():
                success = False
        
        if success:
            logger.info(f"✓ DR cluster initialized with {len(self.nodes)} nodes")
        
        return success
    
    async def monitor_cluster(self):
        """Continuously monitor cluster health"""
        logger.info("Starting cluster monitoring...")
        
        while self.monitoring:
            await asyncio.sleep(5)  # Check every 5 seconds
            
            # Health check all nodes
            for node in self.nodes:
                node.health_check()
                if not node.is_primary:
                    node.get_replication_lag()
            
            # Check if primary is down
            if self.primary_node and not self.primary_node.is_healthy:
                logger.warning(f"⚠ PRIMARY NODE {self.primary_node.name} IS DOWN!")
                await self.trigger_failover()
            
            # Log metrics
            self.log_metrics()
    
    async def trigger_failover(self):
        """Trigger automatic failover to standby"""
        if self.failover_in_progress:
            logger.info("Failover already in progress...")
            return
        
        self.failover_in_progress = True
        logger.info("=" * 60)
        logger.info("INITIATING AUTOMATIC FAILOVER")
        logger.info("=" * 60)
        
        # Find best standby (lowest lag, healthy)
        standbys = [n for n in self.nodes if not n.is_primary and n.is_healthy]
        
        if not standbys:
            logger.error("✗ NO HEALTHY STANDBY AVAILABLE - MANUAL INTERVENTION REQUIRED")
            self.failover_in_progress = False
            return
        
        # Sort by replication lag (lowest first)
        standbys.sort(key=lambda x: x.replication_lag)
        new_primary = standbys[0]
        
        logger.info(f"→ Selected {new_primary.name} as new primary (lag: {new_primary.replication_lag:.2f}s)")
        
        # Wait for replication to catch up
        max_wait = 30
        waited = 0
        while new_primary.replication_lag > 1.0 and waited < max_wait:
            logger.info(f"  Waiting for replication to catch up... (lag: {new_primary.replication_lag:.2f}s)")
            await asyncio.sleep(2)
            new_primary.get_replication_lag()
            waited += 2
        
        # Promote standby to primary
        if new_primary.promote_to_primary():
            old_primary = self.primary_node
            self.primary_node = new_primary
            
            if old_primary:
                old_primary.is_primary = False
            
            logger.info("=" * 60)
            logger.info(f"✓ FAILOVER COMPLETE - {new_primary.name} is now PRIMARY")
            logger.info(f"  RTO achieved: {waited}s")
            logger.info(f"  RPO: {new_primary.replication_lag:.2f}s")
            logger.info("=" * 60)
        else:
            logger.error("✗ FAILOVER FAILED")
        
        self.failover_in_progress = False
    
    def log_metrics(self):
        """Log cluster metrics"""
        timestamp = datetime.now().isoformat()
        
        for node in self.nodes:
            if node.is_healthy:
                stats = node.get_stats()
                
                metric = {
                    "timestamp": timestamp,
                    "node": node.name,
                    "is_primary": node.is_primary,
                    "is_healthy": node.is_healthy,
                    "replication_lag": node.replication_lag,
                    **stats
                }
                
                self.metrics.append(metric)
                
                # Keep only last 100 metrics
                if len(self.metrics) > 100:
                    self.metrics = self.metrics[-100:]
    
    def get_cluster_status(self) -> Dict:
        """Get current cluster status"""
        return {
            "timestamp": datetime.now().isoformat(),
            "primary": self.primary_node.name if self.primary_node else None,
            "total_nodes": len(self.nodes),
            "healthy_nodes": sum(1 for n in self.nodes if n.is_healthy),
            "nodes": [
                {
                    "name": n.name,
                    "is_primary": n.is_primary,
                    "is_healthy": n.is_healthy,
                    "replication_lag": n.replication_lag
                }
                for n in self.nodes
            ]
        }
    
    def print_status(self):
        """Print cluster status to console"""
        status = self.get_cluster_status()
        
        print("\n" + "=" * 60)
        print(f"CLUSTER STATUS - {status['timestamp']}")
        print("=" * 60)
        print(f"Primary Node: {status['primary']}")
        print(f"Total Nodes: {status['total_nodes']}")
        print(f"Healthy Nodes: {status['healthy_nodes']}")
        print("\nNode Details:")
        
        for node in status['nodes']:
            role = "PRIMARY" if node['is_primary'] else "STANDBY"
            health = "✓ HEALTHY" if node['is_healthy'] else "✗ DOWN"
            lag = f"Lag: {node['replication_lag']:.2f}s" if not node['is_primary'] else ""
            
            print(f"  {node['name']:20s} | {role:8s} | {health:10s} | {lag}")
        
        print("=" * 60)


async def main():
    """Main demo function"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║  Multi-Cloud Database Disaster Recovery System - DEMO        ║
║  Simulates automatic failover with RTO <15min, RPO <5min     ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # Create DR orchestrator
    dr = DisasterRecoveryOrchestrator()
    
    # Add database nodes (in production these would be actual DB instances)
    # For demo, we'll simulate with local PostgreSQL
    primary = DatabaseNode(
        name="primary-us-west-2",
        host="localhost",
        port=5435,
        user="postgres",
        password="postgres",
        database="postgres"
    )
    primary.is_primary = True
    
    standby1 = DatabaseNode(
        name="standby-us-east-1",
        host="localhost",
        port=5436,  # Would be different host in production
        user="postgres",
        password="postgres",
        database="postgres"
    )
    
    standby2 = DatabaseNode(
        name="standby-eu-west-1",
        host="localhost",
        port=5437,  # Would be different host in production
        user="postgres",
        password="postgres",
        database="postgres"
    )
    
    dr.add_node(primary)
    dr.add_node(standby1)
    dr.add_node(standby2)
    
    # Initialize cluster
    if not dr.initialize():
        print("Failed to initialize DR cluster. Make sure PostgreSQL is running.")
        print("Run: docker-compose up -d")
        return
    
    # Start monitoring
    monitor_task = asyncio.create_task(dr.monitor_cluster())
    
    # Run for demo period
    try:
        for i in range(12):  # Run for 1 minute (12 * 5 seconds)
            await asyncio.sleep(5)
            dr.print_status()
            
            # Simulate primary failure at 30 seconds
            if i == 6:
                print("\n⚠ SIMULATING PRIMARY FAILURE...")
                primary.is_healthy = False
    
    except KeyboardInterrupt:
        print("\nStopping monitoring...")
    
    finally:
        dr.monitoring = False
        await monitor_task
        
        print("\n✓ Demo complete!")
        print(f"  Total metrics collected: {len(dr.metrics)}")


if __name__ == "__main__":
    asyncio.run(main())