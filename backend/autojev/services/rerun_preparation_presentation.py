"""One preparation message/consent vocabulary for the app and trusted panel."""
from __future__ import annotations

from typing import Any

CONSENT_LABEL = (
    "Autorizo tentar entrar com o login preenchido e executar a ação original "
    "somente se a conferência dos pré-requisitos passar."
)
CONTINUE_LABEL = "Continuar reexecução"
LOGIN_INSTRUCTION = (
    "Marque a autorização e faça login pelo próprio site. O AutoJev identifica o login e "
    "continua automaticamente após conferir a página e os pré-requisitos. Se preferir, "
    "preencha usuário e senha e clique em “Continuar reexecução” para o AutoJev tentar entrar."
)
LOGIN_NOTE = (
    "Com a autorização marcada, o teste continua automaticamente após o login e a "
    "conferência dos pré-requisitos."
)
LOGIN_READY_INSTRUCTION = (
    "Seu login foi reconhecido e os pré-requisitos foram conferidos. "
    "Marque a autorização para o teste continuar automaticamente."
)
PREPARATION_INSTRUCTION = (
    "Prepare a página e os dados iniciais do teste. Depois, marque a autorização e clique em "
    "“Continuar reexecução”. Se houver um login preenchido no site autorizado, o AutoJev tentará "
    "entrar. A ação do teste só será executada se os pré-requisitos forem confirmados."
)
VALIDATING_INSTRUCTION = (
    "Aguarde a conferência do login, da página e dos pré-requisitos. "
    "Não é necessário confirmar novamente."
)
NOTE = "Marcar a autorização não inicia o teste. Clique em “Continuar reexecução” para confirmar."


def preparation_presentation(preparation: dict[str, Any] | None, phase: str | None) -> dict[str, str]:
    preparation = preparation or {}
    login = preparation.get("reason_code") == "authentication_required"
    manual_login = preparation.get("manual_login") or {}
    login_ready = manual_login.get("status") == "ready"
    validating = phase == "validating_preparation"
    attempt = preparation.get("login_attempt") or {}
    feedback = str(attempt.get("reason_label") or preparation.get("reason_label") or "")[:2000]
    return {
        "kicker": "Conferindo a preparação" if validating else "Login reconhecido" if login_ready else "Login necessário" if login else "Prepare a página",
        "title": "Entrando e conferindo preparação…" if validating else "Login realizado. Autorize para continuar" if login_ready else "Faça login para continuar" if login else "Prepare a página para continuar",
        "instruction": VALIDATING_INSTRUCTION if validating else LOGIN_READY_INSTRUCTION if login_ready else LOGIN_INSTRUCTION if login else PREPARATION_INSTRUCTION,
        "consent_label": CONSENT_LABEL,
        "button_label": "Entrando e conferindo preparação…" if validating else CONTINUE_LABEL,
        "details_label": "Ver detalhes da preparação",
        "note": "Aguarde o resultado da conferência." if validating else LOGIN_NOTE if login or login_ready else NOTE,
        "feedback": "Login reconhecido; aguardando sua autorização para continuar." if login_ready and not validating else feedback,
    }
