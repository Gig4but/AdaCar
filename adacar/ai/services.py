"""Wires the AI parts together for the app (one instance per app)."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from adacar import paths
from adacar.ai.enrich import Enricher, ProfileCars
from adacar.ai.manager import ModelManager
from adacar.ai.runtime import LlamaServer
from adacar.config import Settings
from adacar.system.resources import ResourceManager

UPLOADS = "uploads"  # pictures the user added, in the data folder


@dataclass
class AiServices:
    manager: ModelManager
    enricher: Enricher

    def delete_models(self, model_id: str | None = None) -> bool:
        """One model, or all (None); AI work stops first when it uses them."""
        active = self.manager.active_model()
        if model_id is None or (active is not None and active.id == model_id):
            self.enricher.stop()
            self.manager.server.stop()  # ends a call in progress, so the enricher sees "stop"
            self.enricher.wait(15)  # it must not load the model again while it is deleted
        if model_id is None:
            self.manager.delete_all()
            return True
        return self.manager.delete_model(model_id)

    def shutdown(self) -> None:
        self.enricher.stop()
        self.manager.shutdown()


def make_ai_services(
    settings: Settings,
    sessions: sessionmaker[Session],
    data_dir: Path,
    resources: ResourceManager | None = None,
    profile_cars: ProfileCars = lambda _s: None,
) -> AiServices:
    server = LlamaServer(paths.vendor_dir(), paths.log_dir() / "llama-server.log")
    manager = ModelManager(
        lambda: settings.ai, data_dir / "ai-state.json", data_dir / "models", server, resources
    )
    enricher = Enricher(sessions, manager, profile_cars, uploads_dir=data_dir / UPLOADS)
    manager.on_switched = enricher.start
    return AiServices(manager, enricher)
