"""
Configuration management with pydantic-settings validation.
All env vars documented and type-safe.
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Enterprise configuration container.
    Load from environment variables with type coercion and validation.
    """

    # API Security
    api_key: Optional[str] = None
    api_title: str = "LinePilot WFM Engine"
    api_version: str = "0.3.0-prod"

    # Database paths
    tmk_db_path: str = "data/tmk_memory.db"
    hitl_db_path: str = "data/hitl_queue.db"
    interval_db_path: str = "data/interval_audit.db"

    # Erlang C constants
    erlang_default_sla_pct: float = 0.80
    erlang_target_answer_sec: int = 20
    erlang_max_agents_search: int = 500

    # Ingestor thresholds
    sla_breach_threshold: float = 0.80
    occupancy_breach_threshold: float = 0.85
    capacity_delta_hitl_pct: float = 0.20

    # Reflector constants
    deviation_threshold_pct: float = 0.05
    uncorrectable_loop_window: int = 3
    volume_spike_threshold_pct: float = 0.15
    aht_drift_threshold_pct: float = 0.10
    shrinkage_anomaly_threshold_pct: float = 0.08

    # Router constants
    routing_cooldown_seconds: int = 300
    max_queue_occupancy: float = 0.90
    min_available_agents: int = 2

    # HITL constants
    hitl_sla_hours: int = 2
    max_unresolved_before_halt: int = 3

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" or "text"

    # External integrations
    slack_webhook_url: Optional[str] = None
    enable_webhook_dry_run: bool = True

    # Async execution
    executor_pool_size: int = 4
    enable_task_queue: bool = True

    class Config:
        env_file = ".env"
        case_sensitive = False

    def validate_paths(self) -> None:
        """Ensure all database directories exist."""
        for path in [self.tmk_db_path, self.hitl_db_path, self.interval_db_path]:
            directory = os.path.dirname(path)
            if directory and not os.path.exists(directory):
                os.makedirs(directory, exist_ok=True)


# Singleton instance
settings = Settings()
settings.validate_paths()
