"""
Enhanced Configuration System for Deforum Music Generator
Supports YAML loading, validation, and environment variable overrides.
"""

import os
import yaml
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List
from pathlib import Path
from pydantic import BaseModel, validator


@dataclass
class AudioConfig:
    """Audio analysis configuration."""
    sample_rate: int = 22050
    chunk_size: int = 30
    chunk_threshold: int = 60
    max_duration: int = 1800
    cache_dir: Optional[str] = "data/cache"
    enable_noise_reduction: bool = True
    normalize_audio: bool = True
    beat_track_units: str = "time"  # or "frames"


@dataclass
class LyricsConfig:
    """Lyrics transcription configuration."""
    enabled: bool = True
    provider: str = "whisper"  # whisper, azure, google
    model: str = "base"  # tiny, base, small, medium, large
    language: Optional[str] = None  # auto-detect if None
    device: str = "cuda"
    compute_type: str = "float16"  # float16, float32
    beam_size: int = 5
    best_of: int = 5
    temperature: float = 0.0


@dataclass
class AIConfig:
    """AI provider configuration for prompt generation."""
    enabled: bool = False
    provider: str = "openai"  # NVIDIA NIM / OpenAI-compatible
    model: str = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    base_url: str = "http://127.0.0.1:8001"
    api_key_env: str = "EDMG_AI_OPENAI_COMPAT_API_KEY"
    timeout: int = 60
    temperature: float = 0.4
    max_tokens: int = 800


@dataclass
class AnimationConfig:
    """Animation generation configuration."""
    fps: int = 30
    duration: int = 60  # seconds
    resolution: str = "512x512"
    sampler: str = "DPM++ 2M Karras"
    steps: int = 20
    cfg_scale_base: float = 7.0
    cfg_scale_range: float = 3.0
    strength_base: float = 0.8
    strength_range: float = 0.2
    zoom_base: float = 1.0
    zoom_range: float = 0.05
    rotation_range: float = 2.0
    translation_range: float = 10.0


@dataclass
class CloudConfig:
    """Cloud storage configuration."""
    enabled: bool = False
    provider: str = "aws"  # aws, azure, gcp
    bucket_name: str = ""
    region: str = "us-east-1"
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    endpoint_url: Optional[str] = None


@dataclass
class AdvancedConfig:
    """Advanced features configuration."""
    enable_multitrack: bool = False
    enable_style_transfer: bool = False
    enable_preview: bool = True
    enable_cloud_storage: bool = False
    enable_batch_processing: bool = False
    enable_real_time: bool = False
    max_concurrent_jobs: int = 2
    preview_fps: int = 10
    preview_resolution: str = "256x256"


@dataclass
class InterfaceConfig:
    """Web interface configuration."""
    server_port: int = 7862
    server_host: str = "127.0.0.1"
    enable_sharing: bool = False
    share_server_name: Optional[str] = None
    share_server_port: Optional[int] = None
    auto_open_browser: bool = True
    show_error_details: bool = True
    max_file_size: int = 500  # MB
    allowed_extensions: List[str] = field(default_factory=lambda: [".mp3", ".wav", ".flac", ".m4a", ".ogg"])
    theme: str = "default"
    title: str = "🎶 Enhanced Deforum Music Generator"


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "[%(levelname)s] %(asctime)s - %(name)s - %(message)s"
    file_logging: bool = True
    log_file: str = "data/logs/deforum_music.log"
    max_file_size: int = 10  # MB
    backup_count: int = 5
    console_logging: bool = True


@dataclass
class Config:
    """Master configuration class."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    lyrics: LyricsConfig = field(default_factory=LyricsConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    animation: AnimationConfig = field(default_factory=AnimationConfig)
    cloud: CloudConfig = field(default_factory=CloudConfig)
    advanced: AdvancedConfig = field(default_factory=AdvancedConfig)
    interface: InterfaceConfig = field(default_factory=InterfaceConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def __post_init__(self):
        """Apply environment variable overrides."""
        self._apply_env_overrides()
        self._validate_config()

    def _apply_env_overrides(self):
        """Apply environment variable overrides using DEFORUM_ prefix."""
        env_mapping = {
            "DEFORUM_SERVER_PORT": ("interface", "server_port"),
            "DEFORUM_CLOUD_PROVIDER": ("cloud", "provider"),
            "DEFORUM_CLOUD_BUCKET": ("cloud", "bucket_name"),
            "DEFORUM_LOG_LEVEL": ("logging", "level"),
        }

        for env_var, (section, key) in env_mapping.items():
            value = os.getenv(env_var)
            if value:
                section_obj = getattr(self, section)
                if hasattr(section_obj, key):
                    # Type conversion
                    current_value = getattr(section_obj, key)
                    if isinstance(current_value, bool):
                        setattr(section_obj, key, value.lower() in ("true", "1", "yes"))
                    elif isinstance(current_value, int):
                        setattr(section_obj, key, int(value))
                    elif isinstance(current_value, float):
                        setattr(section_obj, key, float(value))
                    else:
                        setattr(section_obj, key, value)

    def _validate_config(self):
        """Validate configuration values."""
        # Validate resolution format
        if "x" not in self.animation.resolution:
            raise ValueError(f"Invalid resolution format: {self.animation.resolution}")
        
        # Validate FPS
        if self.animation.fps <= 0:
            raise ValueError("FPS must be positive")
        
        # Validate ports
        if not (1024 <= self.interface.server_port <= 65535):
            raise ValueError("Server port must be between 1024 and 65535")

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        """Load configuration from YAML file."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {path}")
        
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> "Config":
        """Create config from dictionary."""
        config = cls()
        
        # Update each section
        for section_name in ["audio", "lyrics", "ai", "animation", "cloud", "advanced", "interface", "logging"]:
            if section_name in data:
                section_obj = getattr(config, section_name)
                section_data = data[section_name]
                
                # Update fields that exist in the dataclass
                for key, value in section_data.items():
                    if hasattr(section_obj, key):
                        setattr(section_obj, key, value)
        
        return config

    def to_yaml(self, path: str):
        """Save configuration to YAML file."""
        config_dict = asdict(self)
        
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, default_flow_style=False, indent=2)

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return asdict(self)

    def get_section(self, section_name: str):
        """Get a specific configuration section."""
        return getattr(self, section_name, None)

    def update_section(self, section_name: str, updates: Dict[str, Any]):
        """Update a configuration section with new values."""
        section = self.get_section(section_name)
        if section:
            for key, value in updates.items():
                if hasattr(section, key):
                    setattr(section, key, value)

    def create_directories(self):
        """Create necessary directories based on config."""
        dirs_to_create = [
            "data",
            "output",
            "output/previews",
            "output/analysis", 
            "output/packages",
            "output/videos",
            Path(self.logging.log_file).parent,
        ]
        
        if self.audio.cache_dir:
            dirs_to_create.append(self.audio.cache_dir)
        
        for dir_path in dirs_to_create:
            Path(dir_path).mkdir(parents=True, exist_ok=True)


# Pydantic models for API validation
class AudioConfigModel(BaseModel):
    sample_rate: int = 22050
    max_duration: int = 1800
    enable_noise_reduction: bool = True

class GenerationRequest(BaseModel):
    audio_file: str
    base_prompt: str = "beautiful abstract art"
    negative_prompt: str = "ugly, blurry"
    enable_lyrics: bool = True
    custom_schedule: Optional[Dict[str, Any]] = None

class GenerationResponse(BaseModel):
    success: bool
    schedule_path: Optional[str] = None
    preview_path: Optional[str] = None
    message: str
    metadata: Dict[str, Any] = {}


def load_config(config_path: Optional[str] = None) -> Config:
    """Load configuration with fallback to defaults."""
    if config_path and Path(config_path).exists():
        return Config.from_yaml(config_path)
    
    # Try default locations
    default_paths = [
        "custom.yaml",
        "config/custom.yaml", 
        "enhanced_deforum_music_generator/config/custom.yaml"
    ]
    
    for path in default_paths:
        if Path(path).exists():
            return Config.from_yaml(path)
    
    # Return default config
    return Config()
