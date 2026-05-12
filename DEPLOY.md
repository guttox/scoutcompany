# Deploy Scout em VPS

Guia completo pra colocar o Scout rodando 24/7 numa VPS Linux, independente do Mac.

---

## 1. Provisionar a VPS

**Recomendações:**

| Provedor | Plano | Custo | Por quê |
|---|---|---|---|
| **Contabo VPS S** | 4 vCPU · 8GB RAM · 50GB SSD | €5/mês | Melhor custo/benefício |
| Hostinger VPS 2 | 2 vCPU · 4GB RAM | ~R$25/mês | Painel em português |
| DigitalOcean | 2 vCPU · 2GB RAM | $12/mês | Mais maduro, snapshots |

**Sistema:** Ubuntu 22.04 ou 24.04 LTS.

**Especificação mínima:** 1 vCPU · 2GB RAM · 20GB disco. Tudo abaixo disso pode engasgar com Evolution + Postgres.

Depois de criar:
1. Anote o **IP público** da VPS
2. Conecte via SSH: `ssh root@SEU_IP_AQUI`
3. (Opcional mas recomendado) crie um usuário não-root:
   ```bash
   adduser scout && usermod -aG sudo scout
   su - scout
   ```

---

## 2. Rodar o install.sh

Tudo é automatizado num único script. Cole na VPS:

```bash
curl -fsSL https://raw.githubusercontent.com/guttox/scoutcompany/main/deploy/install.sh | sudo bash
```

Na primeira execução o script vai:
1. Instalar Docker + Compose
2. Clonar o repo em `/opt/scout`
3. Criar `/opt/scout/.env` a partir do template
4. **Parar e pedir pra você editar o .env**

Pra editar:
```bash
sudo nano /opt/scout/.env
```

Preencha com os valores da próxima seção, salve (`Ctrl+O`, `Enter`, `Ctrl+X`) e rode o script de novo:

```bash
sudo bash /opt/scout/deploy/install.sh
```

A segunda execução vai:
1. Subir o stack Docker (Evolution + Postgres + Redis + scout-app)
2. Validar health checks
3. Instalar o crontab com os jobs

---

## 3. Variáveis obrigatórias do `.env`

Lista completa do que precisa preencher na VPS:

| Variável | O que é | Onde pegar |
|---|---|---|
| `ANTHROPIC_API_KEY` | Token Claude (mensagens + IA) | https://console.anthropic.com/settings/keys |
| `ANTHROPIC_MODEL` | Modelo | `claude-sonnet-4-6` (já no template) |
| `GOOGLE_PLACES_KEY` | Token Google Places | https://console.cloud.google.com/google/maps-apis |
| `TELEGRAM_TOKEN` | Bot @usescout_bot | Mesmo valor que está no `~/scout/.env` do Mac. Copie de lá. |
| `TELEGRAM_CHAT_ID` | Seu chat pessoal | Mesmo valor que está no `~/scout/.env` do Mac. |
| `WHATSAPP_SCOUT` | Número Scout | `5511940670464` |
| `EVOLUTION_APIKEY` | Senha forte (você define) | Gere com `openssl rand -hex 24` |
| `EVOLUTION_INSTANCE` | Nome da instância | `scout-wa` |
| `EVOLUTION_URL` | URL interna do compose | **Não mude:** `http://evolution:8080` |
| `DISPATCH_MODE` | `DRY` ou `LIVE` | **Comece DRY** — valida tudo, depois troca pra LIVE |
| `USE_MOCK` | Mock de buscas | `0` |
| `SCORE_MIN` | Score mínimo qualificado | `6` |
| `MAX_PROSPECTS` | Limite busca | `50` |
| `TELEGRAM_DIGEST` | Digest individual de prospects | `0` (off) |
| `WEBHOOK_PORT` | Porta do webhook | `5005` |
| `ASSINATURA_NOME` | Nome nas mensagens email | `Augusto Barbosa` |
| `ASSINATURA_TELEFONE` | Telefone nas msgs email | `+55 11 94067-0464` |
| `LOCALIZACAO_PADRAO` | Cidade base | `Guarulhos, SP` |
| `RAIO_BUSCA_KM` | Raio Google Places | `50` |
| `CIDADES_RODIZIO` | Cidades em rodízio | (já no template) |
| `CIDADES_POR_RODADA` | Quantas cidades/dia | `3` |

> **Importante:** o `EVOLUTION_APIKEY` é uma senha que VOCÊ define. Não tem onde "pegar" — é simétrica entre Evolution e seus scripts. Use `openssl rand -hex 24` pra gerar uma forte.

---

## 4. Reconectar o WhatsApp na nova Evolution

A Evolution da VPS é nova e não tem a sessão do seu Mac. Precisa escanear o QR uma vez:

```bash
# Na VPS:
sudo bash /opt/scout/deploy/get-qrcode.sh
```

Isso vai:
1. Apagar instância antiga (se existir)
2. Criar `scout-wa`
3. Salvar QR em `/opt/scout/qrcode.png`

**Pra ver o QR (3 opções):**

**Opção A — copiar pro seu computador via scp:**
```bash
# No seu Mac:
scp scout@SEU_IP_VPS:/opt/scout/qrcode.png /tmp/qr.png && open /tmp/qr.png
```

**Opção B — servir temporariamente via HTTP (CUIDADO, fica público):**
```bash
# Na VPS:
cd /opt/scout && python3 -m http.server 8000
# No browser local: http://SEU_IP_VPS:8000/qrcode.png
# Quando terminar de escanear: Ctrl+C pra fechar o servidor
```

**Opção C — base64 inline no terminal (Linux com `xdg-open`):**
```bash
base64 -d /opt/scout/qrcode.png > /tmp/qr.png && xdg-open /tmp/qr.png
```

**No celular:**
1. Abra o WhatsApp Business (número `5511940670464`)
2. Menu (3 pontos) → **Aparelhos conectados** → **Conectar um aparelho**
3. Escaneie o QR

---

## 5. Configurar o webhook

Depois que escanear:

```bash
sudo bash /opt/scout/deploy/setup-webhook.sh
```

Isso aponta a Evolution pro webhook interno `http://scout:5005/webhook/whatsapp` (rede Docker — sem expor pra internet).

---

## 6. Validar que está funcionando

```bash
# Status dos containers
sudo docker compose -f /opt/scout/docker-compose.yml ps

# Health webhook
curl http://localhost:5005/health

# Estado da instância WhatsApp (deve dizer "open")
curl -s http://localhost:8080/instance/connectionState/scout-wa \
     -H "apikey: $(grep EVOLUTION_APIKEY /opt/scout/.env | cut -d= -f2)"

# Logs em tempo real
sudo docker compose -f /opt/scout/docker-compose.yml logs -f scout
```

**Teste de mensagem real:**
1. Do seu celular pessoal, mande uma msg pro número Scout (5511940670464)
2. Acompanhe o log do webhook — deve aparecer a msg chegando
3. Em ~10-15 segundos a IA Scout responde

Se a resposta chegar = **sistema 100% no ar na VPS**.

---

## 7. Trocar pra LIVE quando estiver pronto

Comece sempre em **DRY_MODE=DRY**. Quando validar pelo menos 1 dia rodando sem erros:

```bash
sudo sed -i 's/^DISPATCH_MODE=.*/DISPATCH_MODE=LIVE/' /opt/scout/.env
sudo docker compose -f /opt/scout/docker-compose.yml restart scout
```

---

## 8. Migrar dados do Mac (opcional)

Se você quer levar a fila/pipeline atual do Mac pra VPS:

```bash
# No seu Mac:
cd ~/scout
scp -r data/ conversas/ mensagens/ scout@SEU_IP_VPS:/opt/scout/

# Na VPS, ajuste permissões:
sudo chown -R scout:scout /opt/scout/data /opt/scout/conversas /opt/scout/mensagens
```

Lembrando: a sessão WhatsApp **não migra** — Evolution na VPS é uma instância nova e precisa de QR novo.

---

## 9. Desligar o Mac com segurança

Depois de validar que VPS está rodando + LIVE + recebendo respostas:

```bash
# No Mac:
launchctl unload ~/Library/LaunchAgents/com.scout.webhook.plist
launchctl unload ~/Library/LaunchAgents/com.scout.dispatcher.plist
launchctl unload ~/Library/LaunchAgents/com.scout.daily.plist
launchctl unload ~/Library/LaunchAgents/com.scout.weekly.plist
launchctl unload ~/Library/LaunchAgents/com.scout.responder.plist
launchctl unload ~/Library/LaunchAgents/com.scout.lembrete-domingo.plist

# Desliga o Docker da Evolution local
cd ~/scout/evolution && docker compose down
```

A partir daí, **o Mac pode ficar desligado** que o Scout segue trabalhando na VPS.

---

## 10. Comandos úteis de operação

```bash
# Acompanhar todos os logs
sudo docker compose -f /opt/scout/docker-compose.yml logs -f

# Rodar pipeline manualmente
sudo docker compose -f /opt/scout/docker-compose.yml exec scout python scripts/run_all.py

# Rodar dispatcher na hora
sudo docker compose -f /opt/scout/docker-compose.yml exec scout python scripts/dispatcher.py --force --max 1

# Gerar relatório semanal sob demanda
sudo docker compose -f /opt/scout/docker-compose.yml exec scout bash scripts/weekly_pipeline.sh

# Reiniciar só o webhook (após mudar .env)
sudo docker compose -f /opt/scout/docker-compose.yml restart scout

# Atualizar o código depois de push pro GitHub
cd /opt/scout
sudo git pull
sudo docker compose up -d --build scout
```

---

## Troubleshooting

**"Webhook respondendo mas IA não responde":**
- `docker compose logs scout` — confere erros do Claude
- Verifica se `ANTHROPIC_API_KEY` está no .env

**"WhatsApp desconectou":**
- Roda `bash deploy/get-qrcode.sh` de novo → escaneia novo QR
- Volume `evolution_instances` persiste sessão — se ainda assim cair muito, abra issue

**"Crontab não dispara":**
- `crontab -l` — confere se as linhas estão lá
- `tail -f /opt/scout/logs/cron-*.log` — vê erros
- Confirma que o user que tem o crontab tem permissão de rodar `docker compose`
