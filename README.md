# Receipt Organizer

Projeto pessoal para **não esquecer de pagar nenhuma conta**.

Você cadastra as contas que paga todo mês (aluguel, energia, faturas de cartão...) e, de tempos em tempos, entrega os extratos do banco. O projeto cruza uma coisa com a outra e mostra o que já foi pago, o que vence logo e o que ficou para trás. Um bot no Telegram avisa todo dia de manhã.

O foco é uso pessoal, simplicidade e previsibilidade. Fique à vontade para copiar e adaptar.

---

## Histórico do projeto

O projeto mudou de objetivo no caminho. O código das versões antigas continua no histórico do git.

| Quando | Versão | O que mudou |
|---|---|---|
| dez/2025 | **v1: organizador de comprovantes** | Lia comprovantes de pagamento (PDF com `pdfplumber` e JPEG com OCR via Tesseract), extraía beneficiário e data, renomeava o arquivo e o movia para `ano/mês/`. Commits `993dc0e` a `3c71290`. |
| mar/2026 | v1.1 | Ajuste no `move_file`: comprovantes com mesmo beneficiário e data deixam de sobrescrever um ao outro (`_1`, `_2`...). Commit `3108e19`, o último da v1. |
| out/2026 | **v2: conciliação de extratos** | Guardar comprovante um a um dava trabalho e não resolvia o problema real, que era *lembrar* de pagar. Os comprovantes deram lugar aos **extratos** (OFX do Nubank, PDF do Itaú e do Mercado Pago), cruzados com um **cadastro de contas recorrentes** (`contas.yaml`). Entraram SQLite, testes e ruff. |
| out/2026 | v2.1: Telegram e cadastro pelo terminal | Bot com alerta diário, botão "Paguei" e comandos, usado por duas pessoas (cada uma é avisada quando a outra marca uma conta). Comandos `contas adicionar/editar/remover/testar` e `buscar` para montar o cadastro sem editar YAML. |

Para ver o código da v1: `git checkout 3108e19`.

---

## Como funciona

```
contas.yaml (o que você espera pagar) ──┐
                                        ├──► conciliação ──► ✅ paga
extratos do banco (o que foi pago) ─────┘                    ⏳ vence em N dias
                                                             🔴 atrasada
                                                             ❓ falta extrato
```

- Cada conta é procurada nos extratos pelos seus **padrões** (trechos da descrição, como `COPEL`), em uma janela de **15 dias antes até 15 dias depois** do vencimento. Assim, pagar no dia 30 uma conta que vence no dia 1 funciona.
- Cada pagamento conta para **um mês só**: o aluguel pago em 30/09 vale para o vencimento de 01/10, não para o de 01/11.
- **🔴 atrasada**: os extratos de todos os bancos cobrem a janela de pagamento e o pagamento não está lá.
- **❓ falta extrato**: venceu, mas algum banco ainda não tem extrato cobrindo essa janela. Importe o extrato que falta ou marque a conta como paga.
- Pagou e o extrato ainda não saiu? Marque com `paguei` (terminal) ou `/paguei` (Telegram). Quando o extrato chegar, ele confirma.

### Bancos suportados

| Banco | Formato do extrato |
|---|---|
| Nubank | OFX |
| Itaú | PDF ("extrato conta / lançamentos") |
| Mercado Pago | PDF ("extrato de conta") |

O banco é reconhecido pelo **conteúdo** do arquivo, então o nome do arquivo não importa. O Mercado Pago, por exemplo, gera nomes aleatórios.

---

## Instalação

Precisa de Python 3.13 e do [uv](https://docs.astral.sh/uv/).

```bash
git clone <url-do-repositório>
cd receipt-organizer
uv sync
```

Crie o `.env` a partir do modelo e ajuste os caminhos:

```bash
cp .env-exemple .env
```

```env
# pasta onde você vai colocar os extratos baixados
PATH_ENTRADA=/home/voce/receipt-organizer/extratos/entrada
# para onde os extratos vão depois de importados
PATH_EXTRATOS=/home/voce/receipt-organizer/extratos/arquivo
```

```bash
mkdir -p extratos/entrada extratos/arquivo
```

---

## 1. Cadastre suas contas

O jeito mais fácil é pelo terminal. Sem opções, o comando pergunta o que precisa:

```bash
uv run python main.py contas adicionar
```

```
Nome da conta (ex.: Energia): Energia
Dia do vencimento (1 a 31): 10
Como aparece no extrato? Trechos separados por vírgula (ex.: COPEL): COPEL
Só procurar em um banco? (nubank, itau, mercadopago ou Enter para qualquer um):
Meio de pagamento (boleto, fatura, debito, pix) [boleto]:

Nova conta:
  id:         energia
  nome:       Energia
  vencimento: dia 10
  ...

Salvar? [s/N]: s
```

Também dá para passar tudo de uma vez:

```bash
uv run python main.py contas adicionar --nome "Fatura Nubank" --vencimento 8 \
    --padrao "^PAGAMENTO DE FATURA$" --banco nubank --meio fatura
```

**Não sabe como a conta aparece no extrato?** Importe um extrato (passo 2) e procure:

```bash
uv run python main.py buscar copel
```

```
1 lançamento(s) com 'copel':

  06/10/2026     - R$ 212,22  Mercado Pago  Pagamento de conta COPEL- DIS
```

Outros comandos do cadastro:

| Comando | O que faz |
|---|---|
| `contas` | lista as contas cadastradas |
| `contas editar energia` | altera uma conta (pergunta campo a campo; Enter mantém o valor) |
| `contas editar energia --vencimento 12` | altera só um campo |
| `contas remover energia` | remove (pede confirmação) |
| `contas testar energia` | mostra quais pagamentos dos extratos batem com a conta |

Se preferir editar à mão, o arquivo é o `contas.yaml` (há um modelo em `contas.exemplo.yaml`):

```yaml
contas:
- id: energia
  nome: Energia
  vencimento: 10          # dia do mês
  meio: boleto            # boleto, fatura, debito ou pix
  padroes: [COPEL]        # trechos da descrição; sem diferença de maiúscula/acento
- id: fatura-nubank
  nome: Fatura Nubank
  vencimento: 8
  meio: fatura
  banco: nubank           # opcional: só procura nos extratos desse banco
  padroes: ["^PAGAMENTO DE FATURA$"]
```

Os padrões aceitam expressões regulares: `^...$` exige a descrição inteira, e `(DE FATURA )?` torna um trecho opcional.

---

## 2. Importe os extratos

### Passo a passo

1. **Baixe o extrato** no app ou site do banco:
   - Nubank: exporte em **OFX**;
   - Itaú e Mercado Pago: baixe em **PDF**.

   Pegue o **mês inteiro**. Para os ❓ sumirem, o extrato precisa cobrir também os últimos dias do mês anterior ao vencimento, porque é quando muita conta do dia 1 é paga.

2. **Coloque o arquivo na pasta de entrada** (`PATH_ENTRADA`):

   ```bash
   mv ~/Downloads/NU_*.ofx ~/Downloads/itau_extrato_*.pdf extratos/entrada/
   ```

3. **Importe.** Com o bot rodando (veja "Telegram"), isso acontece sozinho em até um minuto e ele avisa no chat. Pelo terminal:

   ```bash
   uv run python main.py importar
   ```

   ```
   Nubank de 01/10/2026 a 30/10/2026: 27 lançamentos (27 novos) · NU_123_01OUT2026_30OUT2026.ofx
   Itaú de 01/09/2026 a 31/10/2026: 23 lançamentos (23 novos) · itau_extrato_102026.pdf
   ```

   Depois de importado, cada arquivo vai para `PATH_EXTRATOS/<banco>/<ano>/`, por exemplo `extratos/arquivo/nubank/2026/nubank_2026-10-01_2026-10-30.ofx`. Se um arquivo não puder ser lido, ele continua na pasta de entrada e o motivo vai para o log.

4. **Veja o resultado:**

   ```bash
   uv run python main.py status
   ```

Importar o mesmo extrato duas vezes, ou extratos com períodos sobrepostos, não duplica nada.

Para importar um arquivo **sem movê-lo** de onde está:

```bash
uv run python main.py importar ~/Downloads/extrato.pdf
```

### Com que frequência importar

Uma vez por mês, logo depois de pagar as contas do mês, já dá baixa em quase tudo. Para o que for pago fora desse ciclo, use `paguei`: o extrato do mês seguinte confirma.

---

## 3. Acompanhe

```bash
uv run python main.py                 # importa a pasta de entrada e mostra o painel de hoje
uv run python main.py status          # painel de hoje
uv run python main.py status --mes 2026-10   # o mês inteiro

uv run python main.py paguei energia              # marca como paga (o mês mais provável)
uv run python main.py paguei energia --mes 2026-10
uv run python main.py paguei energia --desfazer   # desfaz a última marcação
```

```
CONTAS DE OUTUBRO/2026
──────────────────────

Precisa de atenção (1)

  ❓ 01/10  Aluguel
            Venceu há 30 dias · falta extrato: Nubank

Pagas (2)

  ✅ 08/10  Fatura Nubank
            Paga em 06/10 · R$ 1.234,56 · Nubank

  ✅ 10/10  Energia
            Paga em 06/10 · R$ 212,22 · Mercado Pago

──────────────────────
Extratos importados: Mercado Pago até 30/10 · Nubank até 30/10
```

---

## Telegram

O bot (`main.py bot`) fica rodando e:

- **manda um alerta por dia**, a partir de `HORA_ALERTA` (padrão 9h), com o que vence em até 3 dias, vence hoje ou ficou para trás. Cada conta vem com um botão **"✅ Paguei"**. Se o computador estava desligado no horário, o alerta sai quando ele ligar;
- **responde comandos**: `/status`, `/mes 2026-10`, `/contas`, `/paguei energia`, `/desfazer energia`;
- **avisa as outras pessoas** quando alguém marca ou desmarca uma conta, para ninguém pagar duas vezes;
- **importa sozinho** os extratos que aparecerem em `PATH_ENTRADA` e avisa no chat.

Só os chats listados em `TELEGRAM_CHAT_IDS` são atendidos. Mensagens de outras pessoas são ignoradas e registradas no log com o id do chat.

### Configuração

1. No Telegram, abra o **@BotFather** (tem selo azul de verificado) e mande `/newbot`. Escolha um nome e um username terminado em `bot`.
2. Copie o **token** que ele responde para o `.env`:
   ```env
   TELEGRAM_TOKEN=123456789:AAH...
   ```
3. Ainda no BotFather: `/mybots` → seu bot → **Bot Settings** → **Allow Groups?** → **Turn groups off**.
4. Cada pessoa que vai usar o bot abre `t.me/<username_do_bot>`, toca em **Iniciar** e manda uma mensagem.
5. Rode o bot uma vez (`uv run python main.py bot`) e veja os ids de quem mandou mensagem:
   ```bash
   grep "não autorizado" logs/organizer.log
   ```
6. Coloque os ids no `.env`, separados por vírgula, e reinicie o bot:
   ```env
   TELEGRAM_CHAT_IDS=111111111,222222222
   ```

**Segurança:** o token é a senha do bot e fica só no `.env`, que está fora do git. Se ele vazar, gere outro no BotFather (`/mybots` → seu bot → **API Token** → **Revoke current token**). Também vale ativar a verificação em duas etapas na conta do Telegram que criou o bot.

### Deixar o bot sempre rodando

Use o serviço de usuário do systemd em `deploy/contas-bot.service`. Ajuste os caminhos, se o projeto não estiver em `~/Documentos/receipt-organizer`, e siga as instruções no topo do arquivo:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/contas-bot.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now contas-bot
```

---

## Desenvolvimento

```bash
uv run pytest                                  # testes
uv run ruff check . && uv run ruff format .    # lint e formatação
```

### Estrutura

```
receipt-organizer/
├── organizer/
│   ├── importers/        # um leitor por banco: ofx.py, itau_pdf.py, mercadopago_pdf.py
│   ├── acoes.py          # importar e marcar como paga (usado pelo terminal e pelo bot)
│   ├── cadastro.py       # comandos de terminal para o contas.yaml
│   ├── contas.py         # leitura, validação e gravação do contas.yaml
│   ├── reconcile.py      # conciliação conta × pagamento
│   ├── report.py         # textos e seções do relatório
│   ├── storage.py        # SQLite: transações, importações, marcações manuais
│   └── telegram.py       # bot: alertas e comandos
├── deploy/
│   └── contas-bot.service
├── tests/
├── contas.exemplo.yaml
├── main.py               # linha de comando
└── pyproject.toml
```

### Adicionando um banco novo

1. Crie `organizer/importers/<banco>.py` com uma função que receba o conteúdo do arquivo e devolva um `Extrato` (veja `itau_pdf.py`, o mais simples).
2. Registre a detecção em `organizer/importers/__init__.py`.
3. Adicione o nome de exibição em `NOMES_BANCOS` (`organizer/models.py`).
4. Escreva um teste com um extrato de exemplo **com dados fictícios** em `tests/test_importers.py`.

### Logs e concorrência

- Os logs ficam em `logs/organizer.log`. Avisos e erros também aparecem no terminal.
- A importação da pasta de entrada usa uma trava (`flock` em `.process.lock`) para dois processos não moverem os mesmos arquivos ao mesmo tempo. Os outros comandos podem rodar com o bot ligado.
