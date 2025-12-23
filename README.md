# Receipt Organizer

Projeto pessoal para organizar comprovantes de pagamento (principalmente PDFs) de forma automática, estruturando os arquivos por ano e mês com base nas informações extraídas do próprio comprovante.

O foco do projeto é uso pessoal, simplicidade e previsibilidade.


---

## Objetivo

- Centralizar comprovantes de pagamento
- Renomear arquivos com base em:
  - beneficiário
  - data do pagamento ou vencimento
- Organizar os arquivos na estrutura:

```
PATH_FINAL/
└── year/
    └── month/
        └── ENEL-ENERGIA_15_03_2025.pdf
```

---

## Estrutura do projeto

```
receipt-organizer/
├── path_base/
├── path_final/
├── logs/
├── backup/
├── pdf_parser.py
├── jpeg_parser.py
├── main.py
├── run_receipt_organizer.sh
├── .env
├── pyproject.toml
└── README.md
```


---

## Configuração

### Ambiente Python (uv)

O projeto utiliza **uv** para gerenciamento de dependências, com configuração no `pyproject.toml`.

```bash
uv sync
```

Isso criará automaticamente o ambiente virtual em `.venv`.

---

### Variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto:

```env
PATH_MODELO=/caminho/para/comprovantes_modelo
PATH_FINAL=/caminho/para/organizados
```

---

## Execução

### Execução manual

```bash
./run_receipt_organizer.sh
```

Ou:

```bash
.venv/bin/python main.py
```

*obs: não esquecer de dar permissão de execução para o script

## Logs

- Gravados em `logs/`
- Exibidos no terminal
- Incluem data, hora e nível de severidade

---

## Concorrência

O projeto utiliza um arquivo de lock para impedir múltiplas execuções simultâneas.

---

## Limitações e manutenção 

Comprovantes do formato .jpeg podem não funcionar muito bem devido a limitações de OCR. O objeto de maior manutenção nesse script são os métodos parsers, pois o modelo dos comprovantes podem mudar, sendo assim pode ser necessário alterar os condicionais.

_*ignorem o português misturado com o inglês nesse projeto :p_

