# Laya service (CPU)

Runs [Laya](https://huggingface.co/convaiinnovations/laya) locally and exposes it
through the same typed-decision contract AutoJev uses for JEV
(`POST /api/alpha/decisions`), so it can be picked as the **JEV agent** model.

```powershell
cd laya_service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Behind a TLS-inspecting proxy, point Python at a bundle that trusts it:
$env:SSL_CERT_FILE = "$HOME\autojev-ca-bundle.pem"; $env:REQUESTS_CA_BUNDLE = $env:SSL_CERT_FILE
uvicorn laya_service:app --host 127.0.0.1 --port 8100
```

The first start downloads the checkpoint (~650 MB) and loads it before the
service answers; later starts use the Hugging Face cache. Then add a provider in AutoJev with Base URL
`http://127.0.0.1:8100` (no API key), click **Descobrir modelos** and choose
`convaiinnovations/laya` as the JEV agent.

| Variable | Default | Meaning |
|---|---|---|
| `LAYA_DEVICE` | `cpu` | Device passed to Laya's `Router` |
| `LAYA_CHECKPOINT` | `multilingual` | Laya checkpoint served (`multilingual` ~650 MB, `english` ~800 MB) |
| `LAYA_MAX_OPTIONS` | `20` | Candidate moves sent to Laya; Laya degrades above ~20 |
| `LAYA_STATE_CHARS` | `2400` | Character budget of the rendered page state |
| `LAYA_PRELOAD` | `1` | Set `0` to load the model on the first request instead |

The base checkpoint is not tuned for QA exploration (near chance on typed
decisions per its model card). AutoJev's deterministic coverage planner still
bounds what can be executed; fine-tune before relying on its choices.

Tests: `python -m pytest laya_service` (they use a fake predictor; no download).
