from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from autojev.adapters.report import StandaloneHtmlReport
from autojev.adapters.repository import SQLiteRepository
from autojev.adapters.secrets import EphemeralCredentialVault, SecretCipher
from autojev.api.routes import router
from autojev.config import Settings
from autojev.config import settings as default_settings
from autojev.services.exploration import ExplorationManager
from autojev.services.visual_review_resume import VisualReviewResumeService
from autojev.services.scenario_rerun import ScenarioRerunService
from autojev.services.test_suites import TestSuiteService
from autojev.api.test_suites import router as test_suites_router
from autojev.services.planning_assistant import PlanningRecorder


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_settings.data_dir.mkdir(parents=True, exist_ok=True)
        runtime_settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        cipher = SecretCipher(runtime_settings.data_dir, runtime_settings.secret_key)
        repository = SQLiteRepository(runtime_settings.database_path, cipher)
        credential_vault = EphemeralCredentialVault()
        report_renderer = StandaloneHtmlReport(runtime_settings.artifact_dir)
        repository.initialize()
        manager = ExplorationManager(
            repository,
            credential_vault,
            report_renderer,
            runtime_settings,
        )
        app.state.settings = runtime_settings
        app.state.repository = repository
        app.state.credential_vault = credential_vault
        app.state.exploration_manager = manager
        review_resume = VisualReviewResumeService(manager, runtime_settings)
        app.state.visual_review_resume_service = review_resume
        reruns = ScenarioRerunService(manager, runtime_settings)
        app.state.scenario_rerun_service = reruns
        suites = TestSuiteService(repository, reruns)
        app.state.test_suite_service = suites
        recorder = PlanningRecorder(runtime_settings, suites)
        app.state.planning_recorder = recorder
        yield
        await recorder.shutdown()
        await suites.shutdown()
        await reruns.shutdown()
        await review_resume.shutdown()
        await manager.shutdown()

    app = FastAPI(
        title="AutoJev API",
        description="FastAPI backend for evidence-based AI exploratory web testing.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.include_router(router)
    app.include_router(test_suites_router)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default response includes the rejected raw input. That can be
        # an API key or test credential, so expose locations/messages only.
        errors = [
            {
                "loc": list(error.get("loc", ())),
                "message": str(error.get("msg", "Invalid value")),
                "type": str(error.get("type", "value_error")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"detail": "Request validation failed", "errors": errors},
            headers={"Cache-Control": "no-store"} if request.url.path.endswith(("/capture-print/preview", "/preparation-consent")) else None,
        )

    return app


app = create_app()
