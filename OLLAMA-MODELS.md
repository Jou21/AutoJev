# AutoJev - Ollama Models Configuration

## Modelos Locais Disponíveis

| Nome | Contexto | Tamanho | Uso |
|------|----------|---------|-----|
| `qwen3.6-35b-262k` | **262K** | 21 GB | Modelo principal (262K) |
| `qwen3.6-35b-64k` | 64K | 21 GB | Alternativo |
| `qwen3.8-27b` | 131K | 16 GB | Modelo mais leve |
| `voxtral-mini-4b` | padrão | 2.8 GB | Rápido/leve |
| `jan-v3.5-4b` | padrão | 3.0 GB | Rápido/leve |

## Claude Code CLI (não suporta Ollama nativamente)

O Claude Code CLI v2.x exige autenticação com Anthropic. Para usar Ollama:
- Use aliases no `~/.bashrc` (já configurados)
- Ou use a extensão **Continue** no VS Code (recomendado)

## Extensão Continue - Configuração

Arquivo de config: `~/.continue/config.json`

### Modelos configurados:
1. **qwen3.6-35b-262k** - Principal (262K context)
2. **qwen3.6-35b-64k** - Alternativo (64K context)
3. **qwen3.8-27b** - Modelo mais leve (131K context)

### Como usar:
1. Instale a extensão **Continue** no VS Code
2. Pressione `Ctrl+Shift+P` → **"Continue: Select Model"**
3. Escolha o modelo desejado
4. Use `/ask`, `/edit`, `/comment` para comandos rápidos

## Verificar status do Ollama

```bash
# Verificar se Ollama está rodando
ollama list

# Verificar status do serviço
systemctl --user status ollama

# Iniciar se necessário
systemctl --user start ollama
```
