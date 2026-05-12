# 🔍 Scout — Prospecção Inteligente

Sistema autônomo de prospecção para venda de sites e sistemas digitais.

> **Identidade:** projeto pessoal do **Augusto Barbosa**, totalmente **separado da operação Amantius**. Bot do Telegram, branding das mensagens e base de dados são independentes.

---

## O que ele faz

1. **Caça prospects** via Google Places API em Guarulhos / São Paulo
2. **Qualifica cada um** com score 1–10 baseado em critérios reais (site, Instagram, rating, reviews)
3. **Gera mensagens personalizadas** (WhatsApp + Email) com Claude API
4. **Entrega digest no Telegram** com leads prontos pra você copiar e mandar
5. **Acompanha pipeline** com relatório semanal automático

> Nenhuma mensagem é enviada automaticamente. O Scout entrega leads + texto pronto. Você copia, ajusta o que quiser, e envia do seu WhatsApp.

---

## Setup rápido

### 1. Instalar dependências

```bash
cd ~/scout
pip3 install -r requirements.txt
```

(O sistema funciona mesmo sem instalar — cai em mock mode quando dependências externas não estão prontas.)

### 2. Configurar API keys

Copie `.env.example` pra `.env` e preencha:

```bash
cp .env.example .env
# Edite .env
```

#### Google Places API Key

1. Acesse https://console.cloud.google.com/google/maps-apis
2. Crie um projeto novo (sugestão: `scout-augusto`)
3. Habilite **Places API (New)**
4. Crie uma API key em *Credentials*
5. ⚠️ Defina cota diária pra não estourar fatura. Sugestão: $5/dia.
6. Cole no `.env`:
   ```
   GOOGLE_PLACES_KEY=AIza...
   ```

**Custo:** ~US$ 0.017 por busca text-search + US$ 0.005 por place details. Buscar 50 prospects ≈ $1.10 USD.

#### Anthropic API Key

1. Acesse https://console.anthropic.com/settings/keys
2. Gere uma key
3. Cole no `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

**Custo:** Sonnet 4.6 ≈ US$ 0.003 por mensagem gerada. 50 prospects ≈ $0.15.

#### Telegram Bot

Já configurado para o bot **dedicado do Scout** (token e chat_id no `.env`).

> ⚠️ O Scout NUNCA usa o bot da Amantius. Eles são operações isoladas.

### 3. Modo mock (sem keys)

Sem keys, o Scout roda em mock:
- Etapa 1 usa o dataset `mock/guarulhos_sample.json` (40 negócios fictícios em Guarulhos)
- Etapa 3 usa template paramétrico no lugar do Claude

Pra forçar mock mesmo com keys configuradas: `USE_MOCK=1` no `.env`.

---

## Uso diário

### Rodar pipeline completo

```bash
cd ~/scout
python3 scripts/run_all.py --max 30 --top 10
```

Flags:
- `--max 30`: busca até 30 prospects nessa rodada
- `--top 10`: envia top 10 no Telegram
- `--no-telegram`: gera arquivos mas não dispara mensagem (modo dry-run)

### Rodar etapas individualmente

```bash
python3 scripts/search_prospects.py --max 30
python3 scripts/qualify.py
python3 scripts/generate_messages.py
python3 scripts/send_telegram.py --top 15
```

### Atualizar status de um prospect

```bash
# Status válidos: Novo, Abordado, Respondeu, Reunião, Fechado, Perdido
python3 scripts/pipeline_report.py --update padaria-sao-miguel --status Abordado
python3 scripts/pipeline_report.py --update padaria-sao-miguel --status Fechado --obs "Site simples R$1200"
```

### Relatório semanal de pipeline

```bash
python3 scripts/pipeline_report.py --weekly
```

---

## Automação (cron)

Edite o crontab:

```bash
crontab -e
```

Adicione:

```cron
# Scout — relatório diário 9h
0 9 * * * cd ~/scout && /usr/bin/python3 scripts/run_all.py --max 30 --top 10 >> logs/cron.log 2>&1

# Scout — pipeline weekly sexta 18h
0 18 * * 5 cd ~/scout && /usr/bin/python3 scripts/pipeline_report.py --weekly >> logs/cron.log 2>&1
```

> Dica: ative o cron diário só quando estiver pronto pra responder leads. Lead bom esfria em 24h.

---

## Estrutura

```
~/scout/
├── .env                       # keys (gitignored)
├── .env.example               # template
├── README.md
├── requirements.txt
├── data/
│   ├── prospects.csv          # tudo que foi coletado
│   ├── qualificados.csv       # score >= 6
│   └── pipeline.csv           # acompanhamento (status / observações)
├── mensagens/
│   └── <empresa>.txt          # WhatsApp + Email pronto
├── scripts/
│   ├── _common.py             # helpers compartilhados
│   ├── search_prospects.py    # Etapa 1
│   ├── qualify.py             # Etapa 2
│   ├── generate_messages.py   # Etapa 3
│   ├── send_telegram.py       # Etapa 4
│   ├── pipeline_report.py     # Etapa 5
│   └── run_all.py             # orquestrador
├── mock/
│   └── guarulhos_sample.json  # dataset mock
└── logs/
    └── YYYY-MM-DD.log
```

---

## Score 1–10 — como é calculado

| Critério                              | Pontos |
| ------------------------------------- | ------ |
| Sem site nenhum                       | +4     |
| Tem site mas é antigo/desatualizado   | +3     |
| Só Instagram (sem site)               | +3     |
| Avaliação Google ≥ 4.0                | +2     |
| Mais de 50 avaliações no Google       | +1     |
| Telefone disponível                   | +2     |

Score máximo: **10**. Cutoff default: **6**. Ajustável via `SCORE_MIN` no `.env`.

---

## Branding

Cabeçalho de mensagens no Telegram: `🔍 Scout — Prospecção Inteligente`
Rodapé: `Scout by Augusto Barbosa`

Pra trocar a identidade, edite as funções `montar_relatorio` em `scripts/send_telegram.py` e `weekly_report` em `scripts/pipeline_report.py`.

---

## Próximos passos (checklist)

- [ ] Plugar `GOOGLE_PLACES_KEY` real (com cota diária $5/dia)
- [ ] Plugar `ANTHROPIC_API_KEY` real (Claude vai escrever as mensagens com mais sutileza que o template)
- [ ] Trocar `USE_MOCK=1` para `USE_MOCK=0`
- [ ] Rodar `python3 scripts/run_all.py --max 30` em modo real
- [ ] Configurar cron daily (linha acima)
- [ ] Após primeira rodada de respostas, ajustar templates de mensagem com base nos retornos
